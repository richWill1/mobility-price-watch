from flask import Flask, jsonify, render_template
import requests,re,time,threading,json
from html import unescape
from bs4 import BeautifulSoup
app=Flask(__name__)
SITES={'GMS Mobility':'https://www.gmsmobility.co.uk','Mobigo':'https://mobigo.co.uk','Mobility Giant':'https://www.mobilitygiant.co.uk'}
s=requests.Session();s.headers.update({'User-Agent':'Mozilla/5.0 (compatible; MobilityPriceWatch/10.0)'})
cache={'ts':0,'products':[],'running':False,'error':None,'summary':{}}
GENERIC=set('ex demo display used refurbished refurb preowned pre owned second hand new clearance sale portable lightweight mobility scooter scooters powerchair powerchairs power chair chairs electric folding foldable buggy wheelchair transportable road pavement comfort comforter car boot colour color offer offers excellent fair good premium quality fully checked model late bought sold inc including lithium carbon fibre fiber for the with and from to 2020 2021 2022 2023 2024 2025 2026 2027 purple black blue white red grey gray teal dune yellow orange green silver gold cream navy pink light dark price special actual item manufacturer images image condition grade version edition series'.split())
BRANDS={'careco':'careco','care-co':'careco','pride':'pride','pride-mobility':'pride','drive':'drive','kymco':'kymco','rascal':'rascal','solax':'solax','quickie':'quickie','movinglife':'movinglife','scooterpac':'scooterpac','quingo':'quingo','motion':'motion','tuni':'tuni','komfi':'komfi','x-go':'xgo','xgo':'xgo','efoldi':'efoldi','e-foldi':'efoldi','one':'one','monarch':'monarch','muick':'muick','muicksandy':'muicksandy','tga':'tga','sterling':'sterling','excel':'excel','litech':'litech','li-tech':'litech','abilize':'abilize'}
def money(v):
 m=re.search(r'\d[\d,]*(?:\.\d+)?',str(v).replace('£',''));return float(m.group(0).replace(',','')) if m else None
def words(x):return re.findall(r'[a-z0-9]+',(x or '').lower())
def identity(title,vendor=''):
 w=words(title); brand=''
 for z in words(vendor)+w:
  if z in BRANDS:brand=BRANDS[z];break
 out=[];i=0
 while i<len(w):
  if w[i] in BRANDS or w[i] in GENERIC or re.fullmatch(r'20\d\d',w[i]):i+=1;continue
  out.append(w[i]);i+=1
 return brand,tuple(x for x in out if len(x)>=2)[:8]
def condition(text):
 x=unescape(re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',text or ''))).lower()
 for a,b in [('very good','Very Good'),('excellent cosmetic condition','Excellent'),('good cosmetic condition','Good'),('fair cosmetic condition','Fair'),('excellent condition','Excellent'),('good condition','Good'),('fair condition','Fair')]:
  if a in x:return b
 if re.search(r'\bex[- ]?(demo|display)\b',x):return 'Ex-Demo'
 if 'refurb' in x:return 'Refurbished'
 if 'nearly new' in x:return 'Nearly New'
 if re.search(r'\bused\b',x):return 'Used'
 return 'Unknown'
def shopify(retailer,base):
 out=[];seen=set()
 for page in range(1,21):
  try:r=s.get(base+'/products.json',params={'limit':250,'page':page},timeout=20);batch=r.json().get('products',[]) if r.ok else []
  except:batch=[]
  if not batch:break
  for p in batch:
   h=p.get('handle','')
   if h in seen or not p.get('title'):continue
   seen.add(h);vs=p.get('variants',[]);ps=[money(v.get('price')) for v in vs if money(v.get('price')) is not None]
   if not ps:continue
   body=p.get('body_html') or p.get('body') or ''
   out.append({'title':p['title'].strip(),'price':min(ps),'url':base+'/products/'+h,'vendor':p.get('vendor',''),'retailer':retailer,'condition':condition(body+' '+p['title']+' '+' '.join(map(str,p.get('tags',[]))))})
  if len(batch)<250:break
 return out
def giant():
 base=SITES['Mobility Giant'];out=[];seen=set();listing=[base+'/']+[base+'/products/?page='+str(i) for i in range(1,31)]
 for u in listing:
  try:r=s.get(u,timeout=20);soup=BeautifulSoup(r.text,'html.parser') if r.ok else None
  except:soup=None
  if not soup:continue
  links=[]
  for a in soup.select('a[href*="/products/"]'):
   h=a.get('href','').split('?')[0].rstrip('/');full=h if h.startswith('http') else base+h
   if h and h!='/products' and full not in seen:links.append(full)
  for full in links:
   seen.add(full)
   try:r=s.get(full,timeout=15);ps=BeautifulSoup(r.text,'html.parser') if r.ok else None
   except:ps=None
   if not ps:continue
   title='';price=None
   for sc in ps.select('script[type="application/ld+json"]'):
    try:
     d=json.loads(sc.string or sc.get_text());items=d if isinstance(d,list) else [d]
     for it in items:
      if isinstance(it,dict) and it.get('@type')=='Product':
       title=(it.get('name') or '').strip();o=it.get('offers') or {};o=o[0] if isinstance(o,list) and o else o;price=money(o.get('price')) if isinstance(o,dict) else None
       if title:break
    except:pass
   if not title:
    h=ps.find('h1');title=h.get_text(' ',strip=True) if h else ''
   if price is None:
    meta=ps.find('meta',attrs={'property':'product:price:amount'}) or ps.find('meta',attrs={'property':'og:price:amount'});price=money(meta.get('content')) if meta else None
   if price is None:
    vals=re.findall(r'£\s*[\d,]+(?:\.\d+)?',ps.get_text(' ',strip=True));price=money(vals[-1]) if vals else None
   if title and price is not None:out.append({'title':title,'price':price,'url':full,'vendor':'','retailer':'Mobility Giant','condition':'Ex-Demo' if re.search(r'ex[- ]?demo|ex[- ]?display',title,re.I) else 'Used'})
 return out
def scrape_all():
 cats={'GMS Mobility':shopify('GMS Mobility',SITES['GMS Mobility']),'Mobigo':shopify('Mobigo',SITES['Mobigo']),'Mobility Giant':giant()};groups={}
 for r,items in cats.items():
  for p in items:
   b,m=identity(p['title'],p.get('vendor',''))
   if m:groups.setdefault((b,m),{}).setdefault(r,[]).append(p)
 rows=[];matched_counts={r:0 for r in cats};three=0
 for (b,m),by in groups.items():
  sel={r:min(v,key=lambda x:x['price']) for r,v in by.items()}; n=len(sel)
  if n>=2:
   for r in sel:matched_counts[r]+=1
  if n==3:three+=1
  prices=[x['price'] for x in sel.values()];low=min(prices);high=max(prices);cs=[x.get('condition','Unknown') for x in sel.values()];known=[x.lower() for x in cs if x!='Unknown']
  rows.append({'product':' '.join(m).title(),'brand':b,'gms':sel.get('GMS Mobility'),'mobigo':sel.get('Mobigo'),'mobility_giant':sel.get('Mobility Giant'),'difference':round(high-low,2),'match_status':'Matched' if n>=2 else 'No match','condition_match':len(set(known))<=1 if known else False,'condition_status':'Like-for-like condition' if len(set(known))<=1 and known else 'Condition differs'})
 total_groups=len(rows);rates={r:round((matched_counts[r]/sum(1 for x in rows if x.get({'GMS Mobility':'gms','Mobigo':'mobigo','Mobility Giant':'mobility_giant'}[r])))*100,1) if sum(1 for x in rows if x.get({'GMS Mobility':'gms','Mobigo':'mobigo','Mobility Giant':'mobility_giant'}[r])) else 0 for r in cats}
 return sorted(rows,key=lambda x:x['product'].lower()),{'catalog_counts':{r:len(cats[r]) for r in cats},'match_rates':rates,'three_way_matches':three,'total_product_groups':total_groups}
def worker():
 if cache['running']:return
 cache['running']=True;cache['error']=None
 try:cache['products'],cache['summary']=scrape_all();cache['ts']=time.time()
 except Exception as e:cache['error']=str(e)
 finally:cache['running']=False
@app.route('/')
def home():return render_template('index.html')
@app.route('/api/prices')
def prices():
 if (not cache['products'] or time.time()-cache['ts']>900) and not cache['running']:threading.Thread(target=worker,daemon=True).start()
 return jsonify({'updated':cache['ts'],'matches':cache['products'],'count':len(cache['products']),'refreshing':cache['running'],'error':cache['error'],'summary':cache['summary']})
@app.route('/health')
def health():return {'status':'ok','refreshing':cache['running'],'count':len(cache['products']),'error':cache['error']}
if __name__=='__main__':app.run(host='0.0.0.0',port=10000)
