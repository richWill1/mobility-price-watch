from flask import Flask, jsonify, render_template
import requests, re, time
from difflib import SequenceMatcher

app = Flask(__name__)

SITES = {
    'GMS Mobility': 'https://www.gmsmobility.co.uk',
    'Mobigo': 'https://mobigo.co.uk',
}

session = requests.Session()
session.headers.update({'User-Agent': 'Mozilla/5.0 (compatible; MobilityPriceWatch/1.0)'})
cache = {'ts': 0, 'products': []}


def money(value):
    try:
        return float(value)
    except Exception:
        return None


def normalise(name):
    s = name.lower()
    s = re.sub(r'\b(ex[- ]?demo|used|refurbished|pre[- ]?owned|second hand|second-hand|mobility scooter|powerchair|power chair)\b', ' ', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return set(x for x in s.split() if len(x) > 1)


def score(a, b):
    sa, sb = normalise(a), normalise(b)
    if not sa or not sb:
        return 0
    overlap = len(sa & sb) / max(1, min(len(sa), len(sb)))
    seq = SequenceMatcher(None, ' '.join(sorted(sa)), ' '.join(sorted(sb))).ratio()
    return round((overlap * 0.65 + seq * 0.35), 3)


def fetch_shopify(base):
    products = []
    for endpoint in ['/products.json?limit=250']:
        try:
            r = session.get(base + endpoint, timeout=20)
            r.raise_for_status()
            data = r.json()
            for p in data.get('products', []):
                variants = p.get('variants') or []
                prices = [money(v.get('price')) for v in variants if money(v.get('price')) is not None]
                if not prices:
                    continue
                products.append({
                    'title': p.get('title', '').strip(),
                    'price': min(prices),
                    'url': base + '/products/' + p.get('handle', ''),
                    'image': ((p.get('images') or [{}])[0].get('src')),
                    'vendor': p.get('vendor', ''),
                })
        except Exception:
            pass
    return products


def scrape():
    all_products = []
    for retailer, base in SITES.items():
        for p in fetch_shopify(base):
            p['retailer'] = retailer
            all_products.append(p)
    gms = [p for p in all_products if p['retailer'] == 'GMS Mobility']
    mobigo = [p for p in all_products if p['retailer'] == 'Mobigo']
    matches = []
    used = set()
    for a in gms:
        candidates = sorted(((score(a['title'], b['title']), i, b) for i, b in enumerate(mobigo) if i not in used), reverse=True)
        if candidates and candidates[0][0] >= 0.52:
            s, i, b = candidates[0]
            used.add(i)
            diff = round(a['price'] - b['price'], 2)
            matches.append({'product': a['title'], 'gms': a, 'mobigo': b, 'difference': diff, 'match_score': s})
    matches.sort(key=lambda x: abs(x['difference']), reverse=True)
    return matches

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/prices')
def prices():
    now = time.time()
    if now - cache['ts'] > 900 or not cache['products']:
        cache['products'] = scrape()
        cache['ts'] = now
    return jsonify({'updated': cache['ts'], 'matches': cache['products']})

@app.route('/health')
def health():
    return {'status': 'ok'}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
