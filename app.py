from flask import Flask, jsonify, render_template, request
import os, re, time, threading
from html import unescape
import requests
try:
    import psycopg2
except ImportError:
    psycopg2 = None
app=Flask(__name__)
SITES={'CareCo':'https://www.careco.co.uk','GMS Mobility':'https://www.gmsmobility.co.uk','Mobigo':'https://mobigo.co.uk'}
session=requests.Session();session.headers.update({'User-Agent':'Mozilla/5.0 (compatible; MobilityPriceWatch/8.0)'})
cache={'ts':0,'rows':[],'running':False,'error':None}
GENERIC=set('ex demo display used refurbished refurb preowned pre owned second hand new clearance sale portable lightweight mobility scooter scooters powerchair powerchairs power chair chairs electric folding foldable buggy wheelchair transportable road pavement comfort comforter car boot colour color offer offers fantastic value popular brilliant stunning super excellent fair good premium quality fully checked model late bought sold inc including lithium carbon fibre fiber for the with and from to price special actual item manufacturer images image condition grade version edition series now save was rrp right left hand heat massage beige purple black blue white red grey gray teal dune yellow orange green silver gold cream navy pink latte cocoa mink oatmeal plum spray charcoal graphite metallic brown azure iron sand small medium large xlarge xl xxl pack pair single inch inches ft foot feet cm mm amp amps a v'.split())
BRAND_ALIASES={'careco':'careco','care-co':'careco','abilize':'abilize','li-tech':'litech','litech':'litech','i-go':'igo','igo':'igo','prolite':'prolite','pro-lite':'prolite','pride':'pride','drive':'drive','kymco':'kymco','rascal':'rascal','solax':'solax','quickie':'quickie','movinglife':'movinglife','scooterpac':'scooterpac','quingo':'quingo','motion':'motion','tuni':'tuni','komfi':'komfi','x-go':'xgo','xgo':'xgo','efoldi':'efoldi','e-foldi':'efoldi','one':'one','monarch':'monarch','muick':'muick','muicksandy':'muicksandy'}
def money(v):
    try:return float(str(v).replace(',','').replace('£','').strip())
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
    ws=words(title);brand=brand_key(title,vendor);out=[];i=0
    while i<len(ws):
        pair=ws[i]+'-'+ws[i+1] if i+1<len(ws) else ''
        if pair in BRAND_ALIASES:i+=2;continue
        if ws[i] in BRAND_ALIASES or ws[i] in GENERIC:i+=1;continue
        out.append(ws[i]);i+=1
    return brand,tuple(w for w in out if w.isdigit() or len(w)>=4)[:6]
def similarity(a,b):
    aa=set(words(a));bb=set(words(b));return len(aa&bb)/max(1,len(aa|bb)) if aa and bb else 0
def extract_condition(text='',tags=None,options=None):
    raw=unescape(re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',text or ''))).strip();low=raw.lower();combined=' '.join([raw]+[str(x) for x in (tags or [])]+[str(x) for x in (options or [])]);clow=combined.lower()
    for needle,grade in [('very good','Very Good'),('excellent cosmetic condition','Excellent'),('cosmetic condition is excellent','Excellent'),('cosmetic condition: excellent','Excellent'),('excellent condition','Excellent'),('good cosmetic condition','Good'),('cosmetic condition is good','Good'),('good condition','Good'),('fair cosmetic condition','Fair'),('cosmetic condition is fair','Fair'),('fair condition','Fair')]:
        if needle in low:return grade
    for grade in ('Excellent','Very Good','Good','Fair','Premium'):
        if re.search(r'\b'+re.escape(grade.lower())+r'\b',clow):
            if grade.lower()=='good' and any(x in clow for x in ('good value','good choice','good for')):continue
            return grade
    if re.search(r'\bex[- ]?demo\b|\bex[- ]?display\b',clow):return 'Ex-Demo'
    if re.search(r'\brefurbished\b|\brefurb\b',clow):return 'Refurbished'
    if re.search(r'\bused\b',clow):return 'Used'
    return 'Unknown'
def fetch_shopify(base,retailer):
    products=[];seen=set()
    for page in range(1,21):
        try:
            r=session.get(f'{base}/products.json',params={'limit':250,'page':page},timeout=25);r.raise_for_status();batch=r.json().get('products',[])
        except Exception:break
        if not batch:break
        for p in batch:
            handle=p.get('handle') or ''
            if handle in seen:continue
            seen.add(handle);variants=p.get('variants') or [];prices=[money(v.get('price')) for v in variants if money(v.get('price')) is not None]
            if not prices or not p.get('title'):continue
            opts=[v for variant in variants for v in variant.values() if isinstance(v,str)];body=p.get('body_html') or p.get('body') or ''
            products.append({'title':p['title'].strip(),'price':min(prices),'url':f'{base}/products/{handle}','vendor':p.get('vendor') or '','retailer':retailer,'condition':extract_condition(body+' '+p.get('title',''),p.get('tags') or [],opts)})
        if len(batch)<250:break
    return products
def fetch_sitemap_products(base):
    try:
        r=session.get(f'{base}/sitemap.xml',timeout=25);r.raise_for_status();locs=re.findall(r'<loc>(.*?)</loc>',r.text,re.I);child=[u for u in locs if 'sitemap' in u.lower()];urls=[]
        if child:
            for sm in child[:20]:
                try:urls.extend(re.findall(r'<loc>(.*?)</loc>',session.get(sm,timeout=20).text,re.I))
                except Exception:pass
        else:urls=locs
        return [u for u in urls if '/product' in u.lower()][:5000]
    except Exception:return []
def fetch_careco():
    urls=fetch_sitemap_products(SITES['CareCo']);out=[]
    for url in urls:
        try:
            r=session.get(url,timeout=15)
            if not r.ok:continue
            html=r.text;title='';m=re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',html,re.I)
            if m:title=unescape(m.group(1)).strip()
            if not title:
                m=re.search(r'<h1[^>]*>(.*?)</h1>',html,re.I|re.S);title=unescape(re.sub('<[^>]+>',' ',m.group(1))).strip() if m else ''
            price=None
            for pat in [r'"price"\s*:\s*"?([0-9]+(?:\.[0-9]{1,2})?)',r'"finalPrice"\s*:\s*([0-9]+(?:\.[0-9]{1,2})?)',r'£\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)']:
                mm=re.search(pat,html,re.I)
                if mm:price=money(mm.group(1));break
            if title and price is not None:out.append({'title':title,'price':price,'url':url,'vendor':'CareCo','retailer':'CareCo','condition':'New'})
        except Exception:continue
    return out
def load_watchlist():
    path=os.path.join(os.path.dirname(__file__),'watchlist.txt')
    if not os.path.exists(path):return []
    seen=set();out=[]
    for line in open(path,encoding='utf-8'):
        s=re.sub(r'\s+',' ',line.strip())
        if s and s not in seen:seen.add(s);out.append(s)
    return out
def db_conn():
    if not psycopg2 or not os.getenv('DATABASE_URL'):return None
    return psycopg2.connect(os.getenv('DATABASE_URL'),connect_timeout=10)
def init_db():
    conn=db_conn()
    if not conn:return
    with conn,conn.cursor() as cur:
        cur.execute('CREATE TABLE IF NOT EXISTS price_snapshots (id BIGSERIAL PRIMARY KEY, product TEXT NOT NULL, retailer TEXT NOT NULL, price NUMERIC(12,2) NOT NULL, product_url TEXT, scraped_at TIMESTAMPTZ NOT NULL DEFAULT NOW())');cur.execute('CREATE INDEX IF NOT EXISTS idx_price_product_time ON price_snapshots(product,retailer,scraped_at DESC)')
def save_rows(rows):
    conn=db_conn()
    if not conn:return
    with conn,conn.cursor() as cur:
        for row in rows:
            cur.execute('INSERT INTO price_snapshots(product,retailer,price,product_url) VALUES(%s,%s,%s,%s)',(row['product'],'CareCo',row['careco']['price'],row['careco']['url']))
            for c in row['competitors']:cur.execute('INSERT INTO price_snapshots(product,retailer,price,product_url) VALUES(%s,%s,%s,%s)',(row['product'],c['retailer'],c['price'],c['url']))
def previous_price(product,retailer):
    conn=db_conn()
    if not conn:return None
    with conn.cursor() as cur:
        cur.execute('SELECT price FROM price_snapshots WHERE product=%s AND retailer=%s ORDER BY scraped_at DESC OFFSET 1 LIMIT 1',(product,retailer));r=cur.fetchone();return round(float(r[0]),2) if r else None
def build_rows():
    watch=load_watchlist()
    if not watch:return []
    catalog={'CareCo':fetch_careco(),'GMS Mobility':fetch_shopify(SITES['GMS Mobility'],'GMS Mobility'),'Mobigo':fetch_shopify(SITES['Mobigo'],'Mobigo')};indexed={r:{} for r in catalog}
    for retailer,items in catalog.items():
        for p in items:
            k=(brand_key(p['title'],p.get('vendor','')),model_identity(p['title'],p.get('vendor',''))[1])
            if k[1]:indexed[retailer].setdefault(k,[]).append(p)
    rows=[]
    for name in watch:
        k=(brand_key(name),model_identity(name)[1]);own=min(indexed['CareCo'].get(k,[]),key=lambda p:p['price'],default=None)
        if not own:
            ranked=sorted(((similarity(name,p['title']),p) for p in catalog['CareCo']),reverse=True,key=lambda x:x[0])
            if ranked and ranked[0][0]>=.55:own=ranked[0][1]
        if not own:continue
        comps=[]
        for retailer in ('GMS Mobility','Mobigo'):
            items=indexed[retailer].get(k,[])
            if items:comps.append(min(items,key=lambda p:p['price']))
        cheapest=min(comps,key=lambda p:p['price'],default=None);gap=round(own['price']-cheapest['price'],2) if cheapest else None;gap_pct=round(gap/cheapest['price']*100,2) if cheapest and cheapest['price'] else None
        rows.append({'product':name,'careco':own,'cheapest':cheapest,'competitors':comps,'gap':gap,'gap_pct':gap_pct,'careco_movement':None,'competitor_movement':None})
    return rows
def refresh_worker():
    if cache['running']:return
    cache['running']=True;cache['error']=None
    try:
        init_db();rows=build_rows();save_rows(rows)
        for r in rows:
            prev=previous_price(r['product'],'CareCo');r['careco_movement']=round(r['careco']['price']-prev,2) if prev is not None else None
            if r['cheapest']:
                prevc=previous_price(r['product'],r['cheapest']['retailer']);r['competitor_movement']=round(r['cheapest']['price']-prevc,2) if prevc is not None else None
        cache['rows']=rows;cache['ts']=time.time()
    except Exception as exc:cache['error']=str(exc)
    finally:cache['running']=False
@app.route('/')
def home():return render_template('index.html')
@app.route('/api/prices')
def prices():
    if (not cache['rows'] or time.time()-cache['ts']>900) and not cache['running']:threading.Thread(target=refresh_worker,daemon=True).start()
    return jsonify({'updated':cache['ts'],'matches':cache['rows'],'count':len(cache['rows']),'refreshing':cache['running'],'error':cache['error']})
@app.route('/api/run-daily',methods=['GET','POST'])
def run_daily():
    if not cache['running']:threading.Thread(target=refresh_worker,daemon=True).start()
    return jsonify({'ok':True})
@app.route('/api/watchlist',methods=['GET','POST'])
def watchlist():
    path=os.path.join(os.path.dirname(__file__),'watchlist.txt')
    if request.method=='GET':
        p=load_watchlist();return jsonify({'products':p,'count':len(p)})
    payload=request.get_json(silent=True) or {};text=payload.get('text','');seen=set();lines=[]
    for line in text.splitlines():
        s=re.sub(r'\s+',' ',line.strip().strip('|').strip())
        if s and s not in seen:seen.add(s);lines.append(s)
    with open(path,'w',encoding='utf-8') as f:f.write('\n'.join(lines)+'\n')
    cache['rows']=[];cache['ts']=0;return jsonify({'ok':True,'count':len(lines)})
@app.route('/health')
def health():return {'status':'ok','refreshing':cache['running'],'count':len(cache['rows'])}
if __name__=='__main__':app.run(host='0.0.0.0',port=int(os.getenv('PORT','10000')))
