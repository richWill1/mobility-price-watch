import json, re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse, urlunparse
from difflib import SequenceMatcher
import requests
from bs4 import BeautifulSoup

CARECO='https://www.careco.co.uk'; COMPLETE='https://completecareshop.co.uk'
UA='Mozilla/5.0 (compatible; CareCoPriceWatch/2.1)'; TIMEOUT=25
session=requests.Session(); session.headers.update({'User-Agent':UA,'Accept-Language':'en-GB,en;q=0.9'})
GENERIC=set('the and for with from careco mobility scooter scooters wheelchair wheelchairs powerchair powerchairs electric chair chairs folding foldable travel pavement road model product new sale offer offers lightweight premium comfort standard deluxe size colour color black blue red green grey white'.split())
BAD_PREFIX=('blog','media','customer','account','checkout','search','contact','privacy','terms','pricing','sitemap','guides','videos','brochure','careers','showroom','returns','warranty','order-','track-','tv-ad','christmas','about','faq')
CATEGORY_HINTS=('bath','bed','living','seating','chair','scooter','wheelchair','powerchair','walking','rollator','incontinence','transfer','ramp','accessor','kitchen','bedroom','mobility','everyday','essentials','recliner')

def clean(s): return re.sub(r'\s+',' ',s or '').strip()
def money(s):
    if s is None:return None
    m=re.search(r'£\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)',str(s)); return float(m.group(1).replace(',','')) if m else None
def norm(s):
    s=(s or '').lower().replace('&',' and '); s=re.sub(r'[^a-z0-9]+',' ',s); return ' '.join(w for w in s.split() if w not in GENERIC)
def compact(s): return re.sub(r'[^a-z0-9]','',str(s or '').lower())
def model_tokens(s):
    raw=re.findall(r'[a-z]+\d+[a-z0-9]*|\d+[a-z]+[a-z0-9]*|[a-z]{1,5}-\d+[a-z0-9-]*',str(s or '').lower())
    return {compact(x) for x in raw if len(compact(x))>=3}
def brand(title,vendor=''):
    x=(vendor+' '+title).lower(); known=['careco','abilize','li-tech','litech','i-go','igo','x-go','xgo','pride','drive','kymco','rascal','solax','quickie','motion healthcare','nrs','invacare','days','freestyle','homecraft','trulife','strident']
    for b in known:
        if b in x:return b.replace(' ','')
    return ''
def fetch(url):
    r=session.get(url,timeout=TIMEOUT); r.raise_for_status(); return r.text

def product_from_page(url,html,retailer):
    soup=BeautifulSoup(html,'html.parser'); title=vendor=''; price=None; sku=''; is_product=False
    for script in soup.find_all('script',type=re.compile(r'ld\+json',re.I)):
        try:data=json.loads(script.string or script.get_text())
        except Exception:continue
        for d in (data if isinstance(data,list) else [data]):
            if not isinstance(d,dict):continue
            typ=d.get('@type')
            if typ=='Product' or (isinstance(typ,list) and 'Product' in typ):
                is_product=True; title=clean(d.get('name')) or title
                b=d.get('brand'); vendor=(b.get('name','') if isinstance(b,dict) else clean(b)) or vendor
                sku=clean(d.get('sku')) or sku; offers=d.get('offers') or {}
                offers=offers[0] if isinstance(offers,list) and offers else offers
                if isinstance(offers,dict):price=money(offers.get('price')) or price
    if not title:
        h=soup.find('h1'); title=clean(h.get_text(' ',strip=True)) if h else ''
    text=soup.get_text(' ',strip=True)
    if retailer=='CareCo':
        m=re.search(r'(?:Product Code|Item code)[:\s-]+([A-Z0-9.-]+)',text,re.I); sku=sku or (m.group(1) if m else '')
    if not price:price=money(text)
    if not title or price is None or (retailer=='CareCo' and not is_product):return None
    return {'title':title,'price':price,'url':url,'sku':sku,'vendor':vendor,'retailer':retailer,'brand':brand(title,vendor)}

def extract_links(base,html):
    soup=BeautifulSoup(html,'html.parser'); out=[]
    for a in soup.find_all('a',href=True):
        u=urljoin(base,a['href'].split('#')[0]); q=urlparse(u)
        if q.netloc and q.netloc not in ('www.careco.co.uk','careco.co.uk'):continue
        path=q.path.rstrip('/')
        if not path or any(path.strip('/').lower().startswith(x) for x in BAD_PREFIX):continue
        out.append(urlunparse((q.scheme or 'https',q.netloc or 'www.careco.co.uk',path,q.params,q.query,'')))
    return list(dict.fromkeys(out))

def careco_candidate_urls():
    seeds=[]
    for seed in [CARECO+'/sitemap.html',CARECO+'/mobility-scooters/',CARECO+'/walking-aids/',CARECO+'/wheelchairs/',CARECO+'/powerchairs/',CARECO+'/seating/',CARECO+'/bathroom/',CARECO+'/living-aids/',CARECO+'/incontinence/',CARECO+'/bedroom/',CARECO+'/transfer/']:
        try:seeds.extend(extract_links(CARECO,fetch(seed)))
        except Exception:pass
    categories=[]
    for u in seeds:
        p=urlparse(u).path.strip('/')
        if '/' not in p and p and any(h in p.lower() for h in CATEGORY_HINTS):categories.append(u)
    categories += [CARECO+'/mobility-scooters/',CARECO+'/walking-aids/',CARECO+'/wheelchairs/',CARECO+'/powerchairs/',CARECO+'/seating/',CARECO+'/bathroom/',CARECO+'/living-aids/',CARECO+'/incontinence/',CARECO+'/bedroom/',CARECO+'/transfer/']
    categories=list(dict.fromkeys(categories))[:120]
    product_urls=set(); seen_pages=set(); queue=list(categories)
    while queue and len(seen_pages)<220:
        u=queue.pop(0)
        if u in seen_pages:continue
        seen_pages.add(u)
        try:html=fetch(u)
        except Exception:continue
        for link in extract_links(CARECO,html):
            p=urlparse(link).path.strip('/'); lp=p.lower()
            if '/' not in p and p and not any(lp.startswith(x) for x in BAD_PREFIX):product_urls.add(link)
            if ('?' in link or any(h in lp for h in CATEGORY_HINTS)) and len(p.split('/'))<=2 and link not in seen_pages:queue.append(link)
    return list(product_urls)

def scrape_careco(limit=None):
    urls=careco_candidate_urls(); urls=urls[:limit] if limit else urls; results=[]
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs={ex.submit(lambda u:product_from_page(u,fetch(u),'CareCo'),u):u for u in urls}
        for f in as_completed(futs):
            try:
                p=f.result()
                if p:results.append(p)
            except Exception:pass
    seen=set(); out=[]
    for p in results:
        k=p['sku'] or p['url']
        if k not in seen:seen.add(k);out.append(p)
    return sorted(out,key=lambda x:x['title'].lower())

def complete_products():
    out=[];seen=set()
    for page in range(1,200):
        try:
            r=session.get(COMPLETE+'/products.json',params={'limit':250,'page':page},timeout=TIMEOUT);r.raise_for_status();batch=r.json().get('products',[])
        except Exception:break
        if not batch:break
        for p in batch:
            h=p.get('handle')
            if not h or h in seen:continue
            seen.add(h); vs=p.get('variants') or []; prices=[money(v.get('price')) for v in vs if money(v.get('price')) is not None]
            if prices:out.append({'title':clean(p.get('title')),'price':min(prices),'url':COMPLETE+'/products/'+h,'sku':clean((vs[0].get('sku') if vs else '') or ''),'vendor':clean(p.get('vendor')),'retailer':'Complete Care Shop','brand':brand(p.get('title',''),p.get('vendor',''))})
        if len(batch)<250:break
    return out

def match(care,complete,used):
    # 1. Exact MPN/SKU is the strongest signal.
    cm=compact(care.get('sku'))
    if cm:
        hits=[p for p in complete if compact(p.get('sku'))==cm and id(p) not in used]
        if hits:return hits[0],1.0,'Exact MPN/SKU'

    cn=norm(care['title']); cb=care.get('brand',''); cmodels=model_tokens(care['title']+' '+care.get('sku',''))
    candidates=[]
    for p in complete:
        if id(p) in used: continue
        pb=p.get('brand','')
        # Never use a known conflicting brand as a match.
        if cb and pb and cb!=pb: continue
        pn=norm(p['title']); pmodels=model_tokens(p['title']+' '+p.get('sku',''))
        model_overlap=len(cmodels & pmodels)
        if cmodels and pmodels and model_overlap==0: continue
        seq=SequenceMatcher(None,cn,pn).ratio()
        at=set(cn.split()); bt=set(pn.split()); token=(len(at&bt)/max(1,len(at|bt)))
        score=.58*seq+.42*token
        if model_overlap: score += min(.22,.11*model_overlap)
        if cb and pb and cb==pb: score += .12
        candidates.append((score,seq,token,model_overlap,p))
    if not candidates:return None,0,'No match'
    candidates.sort(key=lambda x:x[0],reverse=True); score,seq,token,mo,p=candidates[0]
    # Require materially strong evidence; model overlap can lower title threshold.
    if mo and score>=.73 and seq>=.62:return p,min(score,0.98),'Model + name match'
    if score>=.84 and seq>=.76:return p,score,'Strong name match'
    if score>=.77 and seq>=.70 and token>=.55:return p,score,'Likely name match'
    return None,score,'No confident match'

def compare(limit=None,progress=None):
    care=scrape_careco(limit); complete=complete_products(); used=set()
    if progress:progress('Matching',0,len(care))
    rows=[]
    for i,c in enumerate(care,1):
        m,score,status=match(c,complete,used)
        if m:used.add(id(m))
        d=round(c['price']-m['price'],2) if m else None
        rows.append({'careco':c,'complete':m,'match_score':score,'match_status':status,'difference':d,'cheaper':('Complete Care Shop' if d>0 else ('CareCo' if d<0 else 'Same price')) if d is not None else 'No match'})
        if progress and (i%25==0 or i==len(care)):progress('Matching',i,len(care))
    return rows
