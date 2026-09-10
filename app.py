from flask import Flask, jsonify, render_template
import requests, re, time, json
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

app = Flask(__name__)
SITES = {'GMS Mobility':'https://www.gmsmobility.co.uk','Mobigo':'https://mobigo.co.uk'}
session = requests.Session(); session.headers.update({'User-Agent':'Mozilla/5.0 (compatible; MobilityPriceWatch/4.0)'})
cache={'ts':0,'products':[]}

GENERIC={'ex','demo','display','used','refurbished','refurb','preowned','pre','owned','second','hand','new','clearance','sale','portable','lightweight','mobility','scooter','scooters','powerchair','powerchairs','power','chair','chairs','electric','folding','foldable','buggy','wheelchair','transportable','road','pavement','comfort','comforter','car','boot','colour','color','on','off','offer','offers','fantastic','value','popular','brilliant','stunning','super','excellent','fair','good','premium','quality','fully','checked','model','late','bought','sold','inc','including','lithium','carbon','fibre','fiber','for','the','with','and','from','to','2020','2021','2022','2023','2024','2025','2026','2027','purple','black','blue','white','red','grey','gray','teal','dune','yellow','orange','green','silver','gold','cream','navy','pink','light','dark','price','special','actual','item','manufacturer','images','image','condition','grade','version','edition','series'}
BRAND_ALIASES={'careco':'careco','care-co':'careco','abilize':'abilize','li-tech':'litech','litech':'litech','i-go':'igo','igo':'igo','prolite':'prolite','pro-lite':'prolite','pride':'pride','drive':'drive','kymco':'kymco','rascal':'rascal','solax':'solax','quickie':'quickie','movinglife':'movinglife','scooterpac':'scooterpac','quingo':'quingo','motion':'motion','tuni':'tuni','komfi':'komfi','x-go':'xgo','xgo':'xgo','efoldi':'efoldi','e-foldi':'efoldi','one':'one','monarch':'monarch','muick':'muick','muicksandy':'muicksandy'}

def money(v):
    try:return float(str(v).replace(',','').replace('£','').strip())
    except:return None

def words(text): return re.findall(r'[a-z0-9]+',(text or '').lower().replace('&',' and '))

def brand_key(title,vendor=''):
    for arr in (words(vendor),words(title)):
        for i in range(len(arr)-1):
            p=arr[i]+'-'+arr[i+1]
            if p in BRAND_ALIASES:return BRAND_ALIASES[p]
        for w in arr:
            if w in BRAND_ALIASES:return BRAND_ALIASES[w]
    return ''

def identity_words(title):
    ws=words(title); out=[]; i=0
    while i<len(ws):
        pair=ws[i]+'-'+ws[i+1] if i+1<len(ws) else ''
        if pair in BRAND_ALIASES or ws[i] in BRAND_ALIASES: i+=2 if pair in BRAND_ALIASES else 1; continue
        if ws[i] in GENERIC: i+=1; continue
        out.append(ws[i]); i+=1
    return out

def model_identity(p):
    brand=brand_key(p['title'],p.get('vendor',''))
    title=identity_words(p['title'])
    # Use title first because GMS often has a clean manufacturer/model phrase.
    # Keep 1-4 distinctive terms and numeric model suffixes; discard descriptive tails.
    model=[w for w in title if len(w)>=4 or w.isdigit()][:4]
    return brand, tuple(model)

def confident_match(a,b):
    ba,ma=model_identity(a); bb,mb=model_identity(b)
    if not ma or not mb or ma!=mb:return False
    if ba and bb and ba!=bb:return False
    return True

def parse_product_json(text,url):
    try:
        data=json.loads(text); title=(data.get('title') or '').strip(); vendor=data.get('vendor') or ''
        variants=data.get('variants') or []; prices=[money(v.get('price')) for v in variants if money(v.get('price')) is not None]
        if title and prices:return {'title':title,'price':min(prices),'url':url,'image':data.get('featured_image') or ((data.get('images') or [None])[0]),'vendor':vendor}
    except Exception:pass
    return None

def fetch_product(url):
    try:
        for suffix in ('.js','.json'):
            r=session.get(url+suffix,timeout=12)
            if r.ok and 'json' in (r.headers.get('content-type') or '').lower():
                p=parse_product_json(r.text,url)
                if p:return p
        r=session.get(url,timeout=12)
        if not r.ok:return None
        soup=BeautifulSoup(r.text,'html.parser'); title=''; price=None; vendor=''
        og=soup.find('meta',{'property':'og:title'})
        if og:title=og.get('content','').strip()
        if not title and soup.title:title=soup.title.get_text(' ',strip=True)
        for s in soup.find_all('script',type='application/ld+json'):
            txt=s.string or s.get_text()
            mb=re.search(r'"brand"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)',txt,re.I)
            if mb:vendor=mb.group(1)
            mp=re.search(r'"price"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)',txt,re.I)
            if mp and price is None:price=money(mp.group(1))
        if price is None:
            mp=re.search(r'£\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)',r.text)
            if mp:price=money(mp.group(1))
        if title and price is not None:return {'title':re.sub(r'\s+',' ',title).strip(),'price':price,'url':url,'image':None,'vendor':vendor}
    except Exception:pass
    return None

def product_urls(base):
    urls=set(); collections=['/collections/all','/collections/shop-all','/collections/portable-scooters','/collections/powerchairs','/collections/mobility-scooters','/collections/clearance']
    empty=0
    for page in range(1,81):
        found=set()
        for collection in collections:
            try:
                r=session.get(base+collection,params={'page':page},timeout=15)
                if not r.ok:continue
                soup=BeautifulSoup(r.text,'html.parser')
                for a in soup.select('a[href*="/products/"]'):
                    h=a.get('href','').split('?')[0]
                    if '/products/' in h:found.add(urljoin(base,h))
            except Exception:pass
        new=found-urls; urls.update(found)
        empty=empty+1 if not new else 0
        if page>5 and empty>=3:break
    return sorted(urls)

def scrape_site(retailer,base):
    urls=product_urls(base); products=[]
    with ThreadPoolExecutor(max_workers=20) as ex:
        futures={ex.submit(fetch_product,u):u for u in urls}
        for f in as_completed(futures):
            try:
                p=f.result()
                if p:p['retailer']=retailer; products.append(p)
            except Exception:pass
    return products

def scrape():
    catalog={r:scrape_site(r,b) for r,b in SITES.items()}
    # Group by canonical model phrase, not exact marketing title. This allows
    # harmless wording differences while requiring the same distinctive model.
    groups={}
    for retailer,items in catalog.items():
        for p in items:
            brand,model=model_identity(p)
            if not model:continue
            groups.setdefault(model,{}).setdefault(retailer,[]).append(p)
    matches=[]
    for model,byret in groups.items():
        gs=byret.get('GMS Mobility',[]); ms=byret.get('Mobigo',[])
        if not gs or not ms:continue
        valid=[]
        for g in gs:
            for m in ms:
                if confident_match(g,m):valid.append((g,m))
        if not valid:continue
        # Same exact model can have multiple used units. Compare cheapest live unit on each site.
        g=min([x[0] for x in valid],key=lambda p:p['price']); m=min([x[1] for x in valid],key=lambda p:p['price'])
        gb=brand_key(g['title'],g.get('vendor','')); mb=brand_key(m['title'],m.get('vendor',''))
        matches.append({'product':' '.join(model).title(),'brand':gb or mb,'gms':g,'mobigo':m,'difference':round(g['price']-m['price'],2),'match_score':1.0,'match_status':'Verified exact model','gms_stock':len(gs),'mobigo_stock':len(ms)})
    return sorted(matches,key=lambda x:x['product'].lower())

@app.route('/')
def home():return render_template('index.html')
@app.route('/api/prices')
def prices():
    now=time.time()
    if now-cache['ts']>900 or not cache['products']:
        cache['products']=scrape(); cache['ts']=now
    return jsonify({'updated':cache['ts'],'matches':cache['products'],'count':len(cache['products'])})
@app.route('/health')
def health():return {'status':'ok'}
if __name__=='__main__':app.run(host='0.0.0.0',port=10000)
