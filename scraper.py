import json, re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

CARECO='https://www.careco.co.uk'
COMPLETE='https://completecareshop.co.uk'
UA='Mozilla/5.0 (compatible; CareCoPriceWatch/1.0)'
TIMEOUT=25
session=requests.Session(); session.headers.update({'User-Agent':UA,'Accept-Language':'en-GB,en;q=0.9'})
GENERIC=set('the and for with from careco mobility scooter scooters wheelchair wheelchairs powerchair powerchairs electric chair chairs folding foldable travel pavement road model product new sale offer offers lightweight premium comfort standard deluxe size colour color black blue red green grey white'.split())

def clean(s): return re.sub(r'\s+',' ',s or '').strip()
def money(s):
    if not s:return None
    m=re.search(r'£\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)',str(s))
    return float(m.group(1).replace(',','')) if m else None
def norm(s):
    s=(s or '').lower().replace('&',' and '); s=re.sub(r'[^a-z0-9]+',' ',s)
    return ' '.join(w for w in s.split() if w not in GENERIC)
def brand(title,vendor=''):
    x=(vendor+' '+title).lower(); known=['careco','abilize','li-tech','litech','i-go','igo','x-go','xgo','pride','drive','kymco','rascal','solax','quickie','motion healthcare','nrs','invacare','days','freestyle','homecraft','trulife','strident']
    for b in known:
        if b in x:return b.replace(' ','')
    return ''
def fetch(url):
    r=session.get(url,timeout=TIMEOUT); r.raise_for_status(); return r.text

def sitemap_urls(base):
    seen=set(); urls=[]
    def walk(u):
        if u in seen or len(seen)>200:return
        seen.add(u)
        try:txt=fetch(u)
        except Exception:return
        soup=BeautifulSoup(txt,'xml'); locs=[clean(x.get_text()) for x in soup.find_all('loc')]
        if soup.find('sitemapindex'):
            for x in locs:walk(x)
        elif soup.find('urlset'):urls.extend(locs)
    try:
        robots=fetch(base+'/robots.txt')
        for line in robots.splitlines():
            if line.lower().startswith('sitemap:'):walk(line.split(':',1)[1].strip())
    except Exception:pass
    if not urls:walk(base+'/sitemap.xml')
    return list(dict.fromkeys(urls))

def product_from_page(url,html,retailer):
    soup=BeautifulSoup(html,'html.parser'); title=vendor=''; price=None; sku=''
    for script in soup.find_all('script',type=re.compile('ld\+json',re.I)):
        try:data=json.loads(script.string or script.get_text())
        except Exception:continue
        for d in (data if isinstance(data,list) else [data]):
            if not isinstance(d,dict) or d.get('@type') not in ('Product',['Product']):continue
            title=clean(d.get('name')) or title; vendor=(d.get('brand') or {}).get('name','') if isinstance(d.get('brand'),dict) else clean(d.get('brand')); sku=clean(d.get('sku'))
            offers=d.get('offers') or {}; offers=offers[0] if isinstance(offers,list) and offers else offers
            if isinstance(offers,dict):price=money(offers.get('price'))
    if not title:
        h=soup.find('h1'); title=clean(h.get_text(' ',strip=True)) if h else ''
    text=soup.get_text(' ',strip=True)
    if retailer=='CareCo':
        m=re.search(r'(?:Product Code|Item code)[:\s-]+([A-Z0-9.-]+)',text,re.I); sku=sku or (m.group(1) if m else '')
    if not price:price=money(text)
    if not title or not price:return None
    return {'title':title,'price':price,'url':url,'sku':sku,'vendor':vendor,'retailer':retailer,'brand':brand(title,vendor)}

def careco_product_urls():
    urls=sitemap_urls(CARECO)
    if urls:
        bad=('blog','media','customer','account','checkout','search','contact','privacy','terms','pricing','sitemap','guides','videos','brochure','careers','showroom','returns','warranty','order-','track-','tv-ad','christmas')
        out=[]
        for u in urls:
            p=urlparse(u).path.strip('/')
            if not p or '/' in p or any(p.startswith(x) for x in bad):continue
            out.append(u)
        if out:return out
    return []

def scrape_careco(limit=None):
    urls=careco_product_urls(); urls=urls[:limit] if limit else urls
    results=[]
    with ThreadPoolExecutor(max_workers=8) as ex:
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
            seen.add(h);vs=p.get('variants') or [];prices=[money(v.get('price')) for v in vs if money(v.get('price')) is not None]
            if prices:out.append({'title':clean(p.get('title')),'price':min(prices),'url':COMPLETE+'/products/'+h,'sku':clean((vs[0].get('sku') if vs else '') or ''),'vendor':clean(p.get('vendor')),'retailer':'Complete Care Shop','brand':brand(p.get('title',''),p.get('vendor',''))})
        if len(batch)<250:break
    return out

def match(care,complete):
    if care.get('sku'):
        cs=care['sku'].lower()
        for p in complete:
            if cs and cs==p.get('sku','').lower():return p,1.0,'Exact SKU'
    cn=norm(care['title']);cb=care.get('brand','');best=None;bestscore=0
    for p in complete:
        pn=norm(p['title']);pb=p.get('brand','')
        if cb and pb and cb!=pb:continue
        if cn==pn:return p,.99,'Exact product name'
        a=set(cn.split());b=set(pn.split())
        if not a or not b:continue
        score=len(a&b)/len(a|b)
        if score>bestscore and score>=.72 and len(a&b)>=2:best,bestscore=p,score
    return (best,bestscore,'Strong name match') if best else (None,0,'No match')

def compare(limit=None,progress=None):
    care=scrape_careco(limit); complete=complete_products()
    if progress:progress('Matching',0,len(care))
    rows=[]
    for i,c in enumerate(care,1):
        m,score,status=match(c,complete);d=round(c['price']-m['price'],2) if m else None
        rows.append({'careco':c,'complete':m,'match_score':score,'match_status':status,'difference':d,'cheaper':('Complete Care Shop' if d>0 else ('CareCo' if d<0 else 'Same price')) if d is not None else 'No match'})
        if progress and (i%25==0 or i==len(care)):progress('Matching',i,len(care))
    return rows
