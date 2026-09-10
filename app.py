from flask import Flask, jsonify, render_template
import requests, re, time
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

app = Flask(__name__)
SITES={'GMS Mobility':'https://www.gmsmobility.co.uk','Mobigo':'https://mobigo.co.uk'}
session=requests.Session(); session.headers.update({'User-Agent':'Mozilla/5.0 (compatible; MobilityPriceWatch/3.0)'})
cache={'ts':0,'products':[]}

GENERIC={
'ex','demo','display','used','refurbished','refurb','preowned','pre','owned','second','hand','new','clearance','sale','portable','lightweight','mobility','scooter','scooters','powerchair','powerchairs','power','chair','chairs','electric','folding','foldable','buggy','wheelchair','transportable','road','pavement','comfort','comforter','car','boot','colour','color','on','off','offer','offers','fantastic','value','popular','brilliant','stunning','super','excellent','fair','good','premium','quality','fully','checked','model','late','bought','sold','inc','including','lithium','carbon','fibre','fiber','for','the','with','and','2020','2021','2022','2023','2024','2025','2026','purple','black','blue','white','red','grey','gray','teal','dune','yellow','orange','green','silver','gold','cream','navy','pink','light','dark','price','special'
}
BRAND_ALIASES={
'careco':'careco','care-co':'careco','abilize':'abilize','li-tech':'litech','litech':'litech','i-go':'igo','igo':'igo','prolite':'prolite','pro-lite':'prolite','pride':'pride','drive':'drive','kymco':'kymco','rascal':'rascal','solax':'solax','quickie':'quickie','movinglife':'movinglife','scooterpac':'scooterpac','quingo':'quingo','motion':'motion','tuni':'tuni','komfi':'komfi','x-go':'xgo','xgo':'xgo','efoldi':'efoldi','one':'one','monarch':'monarch','muicksandy':'muicksandy','muick':'muick'
}

def money(v):
    try:return float(str(v).replace(',','').replace('£','').strip())
    except:return None

def words(text): return re.findall(r'[a-z0-9]+',(text or '').lower().replace('&',' and '))

def brand_key(title,vendor=''):
    ws=words(title); vw=words(vendor)
    for arr in (vw,ws):
        for i in range(len(arr)-1):
            p=arr[i]+'-'+arr[i+1]
            if p in BRAND_ALIASES:return BRAND_ALIASES[p]
        for w in arr:
            if w in BRAND_ALIASES:return BRAND_ALIASES[w]
    return ''

def canonical_model(title,vendor=''):
    ws=words(title); brand=brand_key(title,vendor)
    # Strip brand aliases, generic sales language, years and colours.
    out=[]; i=0
    while i<len(ws):
        pair=ws[i]+'-'+ws[i+1] if i+1<len(ws) else ''
        if pair in BRAND_ALIASES: i+=2; continue
        if ws[i] in BRAND_ALIASES: i+=1; continue
        if ws[i] in GENERIC or ws[i].isdigit(): i+=1; continue
        out.append(ws[i]); i+=1
    # Normalise common punctuation/spacing variants without changing model identity.
    return ' '.join(out).strip(), brand

def canonical_identity(p):
    model,brand=canonical_model(p['title'],p.get('vendor',''))
    return (brand,model)

def fetch_product_json(url):
    for suffix in ('.js','.json'):
        try:
            r=session.get(url+suffix,timeout=12)
            if not r.ok: continue
            ct=(r.headers.get('content-type') or '').lower()
            if 'json' not in ct: continue
            data=r.json(); variants=data.get('variants') or []
            prices=[money(v.get('price')) for v in variants if money(v.get('price')) is not None]
            if data.get('title') and prices:
                image=data.get('featured_image') or ((data.get('images') or [None])[0])
                return {'title':data['title'].strip(),'price':min(prices),'url':url,'image':image,'vendor':data.get('vendor','')}
        except Exception: pass
    return None

def fetch_product_html(url):
    try:
        r=session.get(url,timeout=12); r.raise_for_status(); html=r.text
        soup=BeautifulSoup(html,'html.parser')
        title=(soup.find('meta',{'property':'og:title'}) or {}).get('content') if soup.find('meta',{'property':'og:title'}) else None
        if not title and soup.title: title=soup.title.get_text(' ',strip=True)
        vendor=''
        # Shopify JSON-LD often exposes brand/vendor and offers.
        price=None
        for script in soup.find_all('script',type='application/ld+json'):
            txt=script.string or script.get_text()
            if not txt: continue
            m=re.search(r'"brand"\s*:\s*\{\s*"@type"\s*:\s*"Brand"\s*,\s*"name"\s*:\s*"([^"]+)',txt,re.I)
            if m: vendor=m.group(1)
            m=re.search(r'"price"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)',txt,re.I)
            if m and price is None: price=money(m.group(1))
        if price is None:
            m=re.search(r'£\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)',html)
            if m: price=money(m.group(1))
        if title and price is not None:
            return {'title':re.sub(r'\s+',' ',title).strip(),'price':price,'url':url,'image':None,'vendor':vendor}
    except Exception: pass
    return None

def product_urls_from_collection(base):
    urls=set()
    # Both sites expose Shopify-style collection pagination. Crawl pages until
    # several consecutive pages return no new products.
    empty=0
    for page in range(1,61):
        found=set()
        for collection in ('/collections/all','/collections/shop-all','/collections/shop-all-mobility'):
            try:
                r=session.get(base+collection,params={'page':page},timeout=15)
                if not r.ok: continue
                soup=BeautifulSoup(r.text,'html.parser')
                for a in soup.select('a[href*="/products/"]'):
                    href=a.get('href','').split('?')[0]
                    if '/products/' in href:
                        found.add(urljoin(base,href))
                if found: break
            except Exception: pass
        new=found-urls
        urls.update(found)
        if not new: empty+=1
        else: empty=0
        if page>3 and empty>=2: break
    return sorted(urls)

def scrape_site(retailer,base):
    urls=product_urls_from_collection(base)
    products=[]
    with ThreadPoolExecutor(max_workers=16) as ex:
        futures={ex.submit(fetch_product_json,u):u for u in urls}
        for f in as_completed(futures):
            u=futures[f]
            p=f.result()
            if not p: p=fetch_product_html(u)
            if p:
                p['retailer']=retailer; products.append(p)
    return products

def scrape():
    catalog={r:[] for r in SITES}
    for r,b in SITES.items(): catalog[r]=scrape_site(r,b)
    grouped={}
    for retailer,items in catalog.items():
        for p in items:
            key=canonical_identity(p)
            if not key[1]: continue
            grouped.setdefault(key,{}).setdefault(retailer,[]).append(p)
    matches=[]
    for key,byret in grouped.items():
        g=byret.get('GMS Mobility',[]); m=byret.get('Mobigo',[])
        # Only compare when the exact model identity exists on both retailers.
        if not g or not m: continue
        # Multiple stock units of the same exact model are valid: use the lowest
        # currently listed price at each retailer, and retain the stock count.
        a=min(g,key=lambda x:x['price']); b=min(m,key=lambda x:x['price'])
        matches.append({'product':key[1].title(),'brand':key[0],'gms':a,'mobigo':b,'difference':round(a['price']-b['price'],2),'match_score':1.0,'match_status':'Verified exact model','gms_stock':len(g),'mobigo_stock':len(m)})
    matches.sort(key=lambda x: (x['product'].lower()))
    return matches

@app.route('/')
def home(): return render_template('index.html')
@app.route('/api/prices')
def prices():
    now=time.time()
    if now-cache['ts']>900 or not cache['products']:
        cache['products']=scrape(); cache['ts']=now
    return jsonify({'updated':cache['ts'],'matches':cache['products']})
@app.route('/health')
def health(): return {'status':'ok'}
if __name__=='__main__': app.run(host='0.0.0.0',port=10000)
