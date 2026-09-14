import csv, io, re, requests
from difflib import SequenceMatcher

SHEET_ID='1dG7BtpO48dln4R30tLNK3uMO6T9LFkFpxmmJOUr4gCc'
UA='Mozilla/5.0 (compatible; CareCoPriceWatch/3.0)'
session=requests.Session(); session.headers.update({'User-Agent':UA,'Accept-Language':'en-GB,en;q=0.9'})

GENERIC=set('the and for with from careco mobility scooter scooters wheelchair wheelchairs powerchair powerchairs electric chair chairs folding foldable travel pavement road model product new sale offer offers lightweight premium comfort standard deluxe size colour color black blue red green grey white'.split())

def clean(v): return re.sub(r'\s+',' ',str(v or '')).strip()
def norm(v):
    s=clean(v).lower().replace('&amp;',' and ').replace('&',' and ')
    s=re.sub(r'[^a-z0-9]+',' ',s)
    return ' '.join(x for x in s.split() if x not in GENERIC)
def money(v):
    s=clean(v).replace(',','')
    m=re.search(r'([0-9]+(?:\.[0-9]{1,2})?)',s)
    return float(m.group(1)) if m else None
def tokens(s): return set(norm(s).split())
def similarity(a,b): return SequenceMatcher(None,norm(a),norm(b)).ratio()

def sheet_url(sheet):
    return f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&sheet={requests.utils.quote(sheet)}'

def read_sheet(sheet):
    r=session.get(sheet_url(sheet),timeout=30); r.raise_for_status()
    return list(csv.DictReader(io.StringIO(r.content.decode('utf-8-sig'))))

def first(row,*names):
    for n in names:
        if n in row and clean(row[n]): return clean(row[n])
    return ''

def normalise_product(row,retailer):
    title=first(row,'title','Title','product title','Product title')
    price=money(first(row,'sale price','price','Sale price','Price'))
    if not title or price is None:return None
    return {
        'title':title,'price':price,
        'url':first(row,'link','Link','url','URL'),
        'sku':first(row,'mpn','id','MPN','ID'),
        'gtin':first(row,'gtin','GTIN'),
        'brand':first(row,'brand','Brand'),
        'product_type':first(row,'product type','Product type'),
        'retailer':retailer
    }

def load_catalogues():
    care_rows=read_sheet('CareCo'); ccs_rows=read_sheet('CCS')
    care=[p for r in care_rows if (p:=normalise_product(r,'CareCo'))]
    ccs=[p for r in ccs_rows if (p:=normalise_product(r,'Complete Care Shop'))]
    return care,ccs

def match(care,ccs,used):
    # 1. Exact MPN/ID/GTIN. Only accept an exact identifier if it is unique.
    identifiers=[care.get('sku','').lower(),care.get('gtin','').lower()]
    for ident in identifiers:
        if ident and len(ident)>3:
            hits=[(i,p) for i,p in enumerate(ccs) if i not in used and (p.get('sku','').lower()==ident or p.get('gtin','').lower()==ident)]
            if len(hits)==1:return hits[0][1],1.0,'High — exact identifier',hits[0][0]
    # 2. Exact normalised title, preferably same brand.
    ct=norm(care['title']); cb=norm(care.get('brand',''))
    exact=[]
    for i,p in enumerate(ccs):
        if i in used:continue
        if norm(p['title'])==ct and (not cb or not norm(p.get('brand','')) or norm(p.get('brand',''))==cb): exact.append((i,p))
    if len(exact)==1:return exact[0][1],.98,'High — exact product name',exact[0][0]
    # 3. Score candidates using title, brand, product type and model-like tokens.
    best=None
    ca=tokens(care['title']); ctype=tokens(care.get('product_type',''))
    model_tokens={x for x in ca if any(ch.isdigit() for ch in x) or '-' in x}
    for i,p in enumerate(ccs):
        if i in used:continue
        pb=norm(p.get('brand','')); pt=tokens(p.get('product_type','')); pa=tokens(p['title'])
        if cb and pb and cb!=pb: continue
        inter=len(ca & pa); union=len(ca | pa) or 1
        jacc=inter/union
        seq=similarity(care['title'],p['title'])
        type_score=len(ctype & pt)/(len(ctype | pt) or 1) if ctype and pt else 0
        model_bonus=.20 if model_tokens and model_tokens.issubset(pa) else 0
        brand_bonus=.10 if cb and pb and cb==pb else 0
        score=.50*jacc+.30*seq+.10*type_score+model_bonus+brand_bonus
        if best is None or score>best[0]:best=(score,i,p,jacc,seq,type_score)
    if not best:return None,0,'No confident match',None
    score,i,p,jacc,seq,type_score=best
    # Conservative thresholds: require either strong title or model evidence.
    if score>=.84 and (seq>=.78 or model_tokens.issubset(tokens(p['title']))): status='High — strong product match'
    elif score>=.72 and seq>=.68: status='Review — likely equivalent'
    else:return None,score,'No confident match',None
    return p,score,status,i

def compare(progress=None):
    care,ccs=load_catalogues()
    rows=[]; used=set()
    if progress: progress('Matching',0,len(care))
    for n,c in enumerate(care,1):
        m,score,status,idx=match(c,ccs,used)
        if idx is not None:used.add(idx)
        d=round(c['price']-m['price'],2) if m else None
        rows.append({'careco':c,'complete':m,'match_score':score,'match_status':status,'difference':d,'cheaper':('Complete Care Shop' if d>0 else ('CareCo' if d<0 else 'Same price')) if d is not None else 'No match'})
        if progress and (n%25==0 or n==len(care)): progress('Matching',n,len(care))
    return rows
