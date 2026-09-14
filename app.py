from flask import Flask, jsonify, render_template
import requests, re, time, json, threading

app = Flask(__name__)
SITES = {'GMS Mobility': 'https://www.gmsmobility.co.uk', 'Mobigo': 'https://mobigo.co.uk'}
session = requests.Session()
session.headers.update({'User-Agent': 'Mozilla/5.0 (compatible; MobilityPriceWatch/6.0)'})
cache = {'ts': 0, 'products': [], 'running': False, 'error': None}

GENERIC = {
    'ex','demo','display','used','refurbished','refurb','preowned','pre','owned','second','hand','new',
    'clearance','sale','portable','lightweight','mobility','scooter','scooters','powerchair','powerchairs',
    'power','chair','chairs','electric','folding','foldable','buggy','wheelchair','transportable','road',
    'pavement','comfort','comforter','car','boot','colour','color','offer','offers','fantastic','value',
    'popular','brilliant','stunning','super','excellent','fair','good','premium','quality','fully','checked',
    'model','late','bought','sold','inc','including','lithium','carbon','fibre','fiber','for','the','with','and',
    'from','to','2020','2021','2022','2023','2024','2025','2026','2027','purple','black','blue','white','red',
    'grey','gray','teal','dune','yellow','orange','green','silver','gold','cream','navy','pink','light','dark',
    'price','special','actual','item','manufacturer','images','image','condition','grade','version','edition','series'
}
BRAND_ALIASES = {
    'careco':'careco','care-co':'careco','abilize':'abilize','li-tech':'litech','litech':'litech',
    'i-go':'igo','igo':'igo','prolite':'prolite','pro-lite':'prolite','pride':'pride','drive':'drive',
    'kymco':'kymco','rascal':'rascal','solax':'solax','quickie':'quickie','movinglife':'movinglife',
    'scooterpac':'scooterpac','quingo':'quingo','motion':'motion','tuni':'tuni','komfi':'komfi',
    'x-go':'xgo','xgo':'xgo','efoldi':'efoldi','e-foldi':'efoldi','one':'one','monarch':'monarch',
    'muick':'muick','muicksandy':'muicksandy'
}

def money(v):
    try: return float(str(v).replace(',','').replace('£','').strip())
    except Exception: return None

def words(text): return re.findall(r'[a-z0-9]+', (text or '').lower())

def brand_key(title, vendor=''):
    for arr in (words(vendor), words(title)):
        for i in range(len(arr)-1):
            pair = arr[i] + '-' + arr[i+1]
            if pair in BRAND_ALIASES: return BRAND_ALIASES[pair]
        for w in arr:
            if w in BRAND_ALIASES: return BRAND_ALIASES[w]
    return ''

def model_identity(title, vendor=''):
    ws = words(title)
    brand = brand_key(title, vendor)
    out, i = [], 0
    while i < len(ws):
        pair = ws[i] + '-' + ws[i+1] if i+1 < len(ws) else ''
        if pair in BRAND_ALIASES:
            i += 2; continue
        if ws[i] in BRAND_ALIASES or ws[i] in GENERIC:
            i += 1; continue
        out.append(ws[i]); i += 1
    model = tuple(w for w in out if w.isdigit() or len(w) >= 4)[:5]
    return brand, model

def confident_match(a, b):
    ba, ma = model_identity(a['title'], a.get('vendor',''))
    bb, mb = model_identity(b['title'], b.get('vendor',''))
    if not ma or not mb or ma != mb: return False
    if ba and bb and ba != bb: return False
    return True

def extract_condition(text, retailer='', tags=None, options=None):
    raw = re.sub(r'<[^>]+>', ' ', text or '')
    raw = re.sub(r'\s+', ' ', raw).strip()
    low = raw.lower()
    tags = tags or []
    options = options or []
    combined = ' '.join([raw] + [str(x) for x in tags] + [str(x) for x in options])
    clow = combined.lower()

    # Strong exact phrases first, especially the GMS wording found in descriptions.
    phrases = [
        ('very good', 'Very Good'),
        ('excellent condition', 'Excellent'),
        ('excellent cosmetic condition', 'Excellent'),
        ('cosmetic condition is excellent', 'Excellent'),
        ('cosmetic condition: excellent', 'Excellent'),
        ('cosmetic condition - excellent', 'Excellent'),
        ('good condition', 'Good'),
        ('good cosmetic condition', 'Good'),
        ('cosmetic condition is good', 'Good'),
        ('cosmetic condition: good', 'Good'),
        ('cosmetic condition - good', 'Good'),
        ('fair condition', 'Fair'),
        ('fair cosmetic condition', 'Fair'),
        ('cosmetic condition is fair', 'Fair'),
        ('cosmetic condition: fair', 'Fair'),
        ('cosmetic condition - fair', 'Fair'),
    ]
    for needle, grade in phrases:
        if needle in low:
            return grade

    # Retailer/product feeds often expose the grade as a tag, option, or title word.
    for grade in ('Excellent','Very Good','Good','Fair'):
        if re.search(r'\b' + re.escape(grade.lower()) + r'\b', clow):
            # Avoid interpreting general sales copy such as "good value" as condition.
            if grade.lower() == 'good' and any(x in clow for x in ('good value', 'good choice', 'good for')):
                continue
            return grade

    # Keep the original commercial stock state separately where useful.
    if re.search(r'\bex[- ]?demo\b|\bex[- ]?display\b', clow):
        return 'Ex-Demo'
    if re.search(r'\brefurbished\b|\brefurb\b', clow):
        return 'Refurbished'
    if re.search(r'\bused\b', clow):
        return 'Used'
    return 'Unknown'

def fetch_catalog_page(base, page):
    url = f'{base}/products.json'
    try:
        r = session.get(url, params={'limit': 250, 'page': page}, timeout=20)
        r.raise_for_status()
        return r.json().get('products', [])
    except Exception:
        return []

def scrape_site(retailer, base):
    products = []
    seen = set()
    for page in range(1, 21):
        batch = fetch_catalog_page(base, page)
        if not batch: break
        for p in batch:
            handle = p.get('handle') or ''
            if handle in seen: continue
            seen.add(handle)
            variants = p.get('variants') or []
            prices = [money(v.get('price')) for v in variants if money(v.get('price')) is not None]
            if not prices or not p.get('title'): continue
            option_values = []
            for v in variants:
                for k, vval in v.items():
                    if isinstance(vval, str): option_values.append(vval)
            tags = p.get('tags') or []
            body_html = p.get('body_html') or p.get('body') or ''
            condition = extract_condition(body_html + ' ' + p.get('title',''), retailer, tags, option_values)
            products.append({
                'title': p['title'].strip(),
                'price': min(prices),
                'url': f"{base}/products/{handle}",
                'image': (p.get('images') or [{}])[0].get('src'),
                'vendor': p.get('vendor') or '',
                'retailer': retailer,
                'condition': condition,
            })
        if len(batch) < 250: break
    return products

def scrape_all():
    catalog = {retailer: scrape_site(retailer, base) for retailer, base in SITES.items()}
    grouped = {}
    for retailer, items in catalog.items():
        for p in items:
            brand, model = model_identity(p['title'], p.get('vendor',''))
            if not model: continue
            grouped.setdefault((brand, model), {}).setdefault(retailer, []).append(p)

    matches = []
    for (brand, model), byret in grouped.items():
        g = byret.get('GMS Mobility', [])
        m = byret.get('Mobigo', [])
        if not g or not m: continue
        valid_g = [x for x in g if any(confident_match(x, y) for y in m)]
        valid_m = [y for y in m if any(confident_match(x, y) for x in g)]
        if not valid_g or not valid_m: continue
        gp = min(valid_g, key=lambda x: x['price'])
        mp = min(valid_m, key=lambda x: x['price'])
        matches.append({
            'product': ' '.join(model).title(),
            'brand': brand,
            'gms': gp,
            'mobigo': mp,
            'difference': round(gp['price'] - mp['price'], 2),
            'match_score': 1.0,
            'match_status': 'Verified exact model',
            'gms_stock': len(valid_g),
            'mobigo_stock': len(valid_m),
        })
    return sorted(matches, key=lambda x: x['product'].lower())

def refresh_worker():
    if cache['running']: return
    cache['running'] = True
    cache['error'] = None
    try:
        products = scrape_all()
        cache['products'] = products
        cache['ts'] = time.time()
    except Exception as exc:
        cache['error'] = str(exc)
    finally:
        cache['running'] = False

@app.route('/')
def home(): return render_template('index.html')

@app.route('/api/prices')
def prices():
    if (not cache['products'] or time.time() - cache['ts'] > 900) and not cache['running']:
        threading.Thread(target=refresh_worker, daemon=True).start()
    return jsonify({
        'updated': cache['ts'],
        'matches': cache['products'],
        'count': len(cache['products']),
        'refreshing': cache['running'],
        'error': cache['error']
    })

@app.route('/health')
def health(): return {'status':'ok','refreshing':cache['running'],'count':len(cache['products'])}

if __name__ == '__main__': app.run(host='0.0.0.0', port=10000)
