from flask import Flask, jsonify, render_template
import requests, re, time, threading
from html import unescape
from bs4 import BeautifulSoup

app = Flask(__name__)
SITES = {
    'GMS Mobility': 'https://www.gmsmobility.co.uk',
    'Mobigo': 'https://mobigo.co.uk',
    'Mobility Giant': 'https://www.mobilitygiant.co.uk',
}
session = requests.Session()
session.headers.update({'User-Agent':'Mozilla/5.0 (compatible; MobilityPriceWatch/8.0)'})
cache={'ts':0,'products':[],'running':False,'error':None}

GENERIC={'ex','demo','display','used','refurbished','refurb','preowned','pre','owned','second','hand','new','clearance','sale','portable','lightweight','mobility','scooter','scooters','powerchair','powerchairs','power','chair','chairs','electric','folding','foldable','buggy','wheelchair','transportable','road','pavement','comfort','comforter','car','boot','colour','color','offer','offers','fantastic','value','popular','brilliant','stunning','super','excellent','fair','good','premium','quality','fully','checked','model','late','bought','sold','inc','including','lithium','carbon','fibre','fiber','for','the','with','and','from','to','2020','2021','2022','2023','2024','2025','2026','2027','purple','black','blue','white','red','grey','gray','teal','dune','yellow','orange','green','silver','gold','cream','navy','pink','light','dark','price','special','actual','item','manufacturer','images','image','condition','grade','version','edition','series'}
BRAND_ALIASES={'careco':'careco','care-co':'careco','abilize':'abilize','li-tech':'litech','litech':'litech','i-go':'igo','igo':'igo','prolite':'prolite','pro-lite':'prolite','pride':'pride','drive':'drive','kymco':'kymco','rascal':'rascal','solax':'solax','quickie':'quickie','movinglife':'movinglife','scooterpac':'scooterpac','quingo':'quingo','motion':'motion','tuni':'tuni','komfi':'komfi','x-go':'xgo','xgo':'xgo','efoldi':'efoldi','e-foldi':'efoldi','one':'one','monarch':'monarch','muick':'muick','muicksandy':'muicksandy','tga':'tga','sterling':'sterling','excel':'excel','pride mobility':'pride'}

def money(v):
    try:
        m=re.search(r'-?\d[\d,]*(?:\.\d+)?',str(v).replace('£',''))
        return float(m.group(0).replace(',','')) if m else None
    except:return None

def words(text):return re.findall(r'[a-z0-9]+',(text or '').lower())

def brand_key(title,vendor=''):
    for arr in (words(vendor),words(title)):
        for i in range(len(arr)-1):
            pair=arr[i]+'-'+arr[i+1]
            if pair in BRAND_ALIASES:return BRAND_ALIASES[pair]
        for w in arr:
            if w in BRAND_ALIASES:return BRAND_ALIASES[w]
    return ''

def model_identity(title,vendor=''):
    ws=words(title); brand=brand_key(title,vendor); out=[]; i=0
    while i<len(ws):
        pair=ws[i]+'-'+ws[i+1] if i+1<len(ws) else ''
        if pair in BRAND_ALIASES:i+=2; continue
        if ws[i] in BRAND_ALIASES or ws[i] in GENERIC:i+=1; continue
        out.append(ws[i]); i+=1
    return brand,tuple(w for w in out if w.isdigit() or len(w)>=4)[:6]

def confident_match(a,b):
    ba,ma=model_identity(a['title'],a.get('vendor','')); bb,mb=model_identity(b['title'],b.get('vendor',''))
    if not ma or not mb or ma!=mb:return False
    if ba and bb and ba!=bb:return False
    return True

def extract_condition(text='',tags=None,options=None):
    raw=unescape(re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',text or ''))).strip(); low=raw.lower()
    combined=' '.join([raw]+[str(x) for x in (tags or [])]+[str(x) for x in (options or [])]); clow=combined.lower()
    for needle,grade in [('very good','Very Good'),('excellent cosmetic condition','Excellent'),('cosmetic condition is excellent','Excellent'),('cosmetic condition: excellent','Excellent'),('cosmetic condition - excellent','Excellent'),('excellent condition','Excellent'),('good cosmetic condition','Good'),('cosmetic condition is good','Good'),('cosmetic condition: good','Good'),('cosmetic condition - good','Good'),('good condition','Good'),('fair cosmetic condition','Fair'),('cosmetic condition is fair','Fair'),('cosmetic condition: fair','Fair'),('cosmetic condition - fair','Fair'),('fair condition','Fair')]:
        if needle in low:return grade
    for grade in ('Excellent','Very Good','Good','Fair','Premium','Nearly New'):
        if re.search(r'\b'+re.escape(grade.lower())+r'\b',clow):
            if grade.lower()=='good' and any(x in clow for x in ('good value','good choice','good for')):continue
            return grade
    if re.search(r'\bex[- ]?demo\b|\bex[- ]?display\b',clow):return 'Ex-Demo'
    if re.search(r'\brefurbished\b|\brefurb\b',clow):return 'Refurbished'
    if re.search(r'\bused\b|\bcertified used\b',clow):return 'Used'
    if 'nearly new' in clow:return 'Nearly New'
    return 'Unknown'

def page_condition(url,retailer,fallback='Unknown'):
    try:
        r=session.get(url,timeout=12)
        if not r.ok:return fallback
        html=r.text
        if retailer=='Mobigo':
            m=re.search(r'<[^>]*class=["\'][^"\']*product-grading__value[^"\']*["\'][^>]*>\s*([^<]+?)\s*</',html,re.I)
            if m:
                value=re.sub(r'\s+',' ',unescape(m.group(1))).strip()
                if value:return value.title()
        return extract_condition(html) or fallback
    except Exception:return fallback

def fetch_catalog_page(base,page):
    try:
        r=session.get(f'{base}/products.json',params={'limit':250,'page':page},timeout=20); r.raise_for_status(); return r.json().get('products',[])
    except:return []

def scrape_shopify(retailer,base):
    products=[]; seen=set()
    for page in range(1,21):
        batch=fetch_catalog_page(base,page)
        if not batch:break
        for p in batch:
            handle=p.get('handle') or ''
            if handle in seen:continue
            seen.add(handle)
            variants=p.get('variants') or []; prices=[money(v.get('price')) for v in variants if money(v.get('price')) is not None]
            if not prices or not p.get('title'):continue
            opts=[vval for v in variants for vval in v.values() if isinstance(vval,str)]
            body=p.get('body_html') or p.get('body') or ''; tags=p.get('tags') or []
            products.append({'title':p['title'].strip(),'price':min(prices),'url':f'{base}/products/{handle}','image':(p.get('images') or [{}])[0].get('src'),'vendor':p.get('vendor') or '','retailer':retailer,'condition':extract_condition(body+' '+p.get('title',''),tags,opts)})
        if len(batch)<250:break
    return products

def scrape_mobility_giant():
    products=[]; seen=set()
    for page in range(1,31):
        url=SITES['Mobility Giant'] + ('/' if page==1 else f'/?page={page}')
        try:
            r=session.get(url,timeout=20); r.raise_for_status()
            soup=BeautifulSoup(r.text,'html.parser')
            found=0
            for a in soup.select('a[href*="/products/"]'):
                href=a.get('href','').split('?')[0]
                if not href or href.rstrip('/') in seen:continue
                title=a.get_text(' ',strip=True)
                card=a
                for _ in range(4):
                    if card.parent: card=card.parent
                text=card.get_text(' ',strip=True)
                prices=re.findall(r'£\s*[\d,]+(?:\.\d+)?',text)
                if not title or not prices:continue
                price=money(prices[-1])
                if price is None:continue
                full=href if href.startswith('http') else SITES['Mobility Giant']+href
                seen.add(href.rstrip('/')); found+=1
                products.append({'title':title,'price':price,'url':full,'image':'','vendor':'','retailer':'Mobility Giant','condition':extract_condition(text+' '+title)})
            if found==0:break
        except Exception:
            if page==1: raise
            break
    return products

def scrape_all():
    catalog={
        'GMS Mobility':scrape_shopify('GMS Mobility',SITES['GMS Mobility']),
        'Mobigo':scrape_shopify('Mobigo',SITES['Mobigo']),
        'Mobility Giant':scrape_mobility_giant(),
    }
    grouped={}
    for retailer,items in catalog.items():
        for p in items:
            brand,model=model_identity(p['title'],p.get('vendor',''))
            if model:grouped.setdefault((brand,model),{}).setdefault(retailer,[]).append(p)
    matches=[]
    for (brand,model),byret in grouped.items():
        available={}
        for retailer,items in byret.items():
            available[retailer]=[x for x in items if x.get('price') is not None]
        if len(available)<2:continue
        selected={}
        for retailer,items in available.items():
            p=min(items,key=lambda x:x['price'])
            p['condition']=page_condition(p['url'],retailer,p.get('condition','Unknown')) if retailer in ('GMS Mobility','Mobigo','Mobility Giant') else p.get('condition','Unknown')
            selected[retailer]=p
        prices=[p['price'] for p in selected.values()]
        low=min(prices); high=max(prices)
        matches.append({'product':' '.join(model).title(),'brand':brand,'gms':selected.get('GMS Mobility'),'mobigo':selected.get('Mobigo'),'mobility_giant':selected.get('Mobility Giant'),'difference':round(high-low,2),'match_score':1.0,'match_status':'Verified exact model'})
    return sorted(matches,key=lambda x:x['product'].lower())

def refresh_worker():
    if cache['running']:return
    cache['running']=True; cache['error']=None
    try:cache['products']=scrape_all(); cache['ts']=time.time()
    except Exception as exc:cache['error']=str(exc)
    finally:cache['running']=False

@app.route('/')
def home():return render_template('index.html')
@app.route('/api/prices')
def prices():
    if (not cache['products'] or time.time()-cache['ts']>900) and not cache['running']:threading.Thread(target=refresh_worker,daemon=True).start()
    return jsonify({'updated':cache['ts'],'matches':cache['products'],'count':len(cache['products']),'refreshing':cache['running'],'error':cache['error']})
@app.route('/health')
def health():return {'status':'ok','refreshing':cache['running'],'count':len(cache['products']),'error':cache['error']}
if __name__=='__main__':app.run(host='0.0.0.0',port=10000)
