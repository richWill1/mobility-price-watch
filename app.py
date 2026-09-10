from flask import Flask, jsonify, render_template
import requests, re, time

app = Flask(__name__)

SITES = {
    'GMS Mobility': 'https://www.gmsmobility.co.uk',
    'Mobigo': 'https://mobigo.co.uk',
}

session = requests.Session()
session.headers.update({'User-Agent': 'Mozilla/5.0 (compatible; MobilityPriceWatch/1.0)'})
cache = {'ts': 0, 'products': []}

# Words that describe condition/category rather than the actual model.
GENERIC = {
    'ex', 'demo', 'display', 'used', 'refurbished', 'refurb', 'preowned', 'pre',
    'owned', 'second', 'hand', 'new', 'clearance', 'sale', 'portable', 'lightweight',
    'mobility', 'scooter', 'powerchair', 'power', 'chair', 'electric', 'folding',
    'foldable', 'premium', 'excellent', 'fair', 'good', 'purple', 'black', 'blue',
    'white', 'red', 'grey', 'gray', '2024', '2025', '2026', 'li', 'sle', '3', '4'
}

# Brand names are useful for identity, but are not enough to establish a match.
BRANDS = {'abilize', 'careco', 'li-tech', 'litech', 'i-go', 'igo', 'prolite', 'pride', 'drive', 'kymco', 'rascal', 'solax'}


def money(value):
    try:
        return float(value)
    except Exception:
        return None


def tokens(name):
    s = name.lower().replace('&', ' and ')
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return {x for x in s.split() if len(x) > 1}


def model_tokens(name):
    return tokens(name) - GENERIC


def brand_tokens(name):
    return tokens(name) & BRANDS


def match_score(a, b):
    """Conservative model matcher.

    A shared brand, 'li', '3', 'portable', etc. is NOT sufficient.
    The listings must share at least one distinctive model token such as
    'stratus', 'aeron', 'ranger', 'evisu' or 'vector'.
    """
    ma, mb = model_tokens(a), model_tokens(b)
    distinctive = ma & mb
    brands = brand_tokens(a) & brand_tokens(b)

    # No distinctive model word = no automatic comparison.
    if not distinctive:
        return 0

    # If both sides expose a brand, require the same brand.
    ba, bb = brand_tokens(a), brand_tokens(b)
    if ba and bb and not (ba & bb):
        return 0

    # One or more distinctive model tokens is strong evidence. Multiple is stronger.
    score = min(1.0, 0.72 + (0.10 * max(0, len(distinctive) - 1)))
    if brands:
        score += 0.08
    return round(min(score, 0.99), 3)


def fetch_shopify(base):
    products = []
    page = 1
    while page <= 10:
        try:
            r = session.get(base + f'/products.json?limit=250&page={page}', timeout=20)
            r.raise_for_status()
            data = r.json()
            batch = data.get('products', [])
            if not batch:
                break
            for p in batch:
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
            if len(batch) < 250:
                break
            page += 1
        except Exception:
            break
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
        candidates = []
        for i, b in enumerate(mobigo):
            if i in used:
                continue
            s = match_score(a['title'], b['title'])
            if s:
                candidates.append((s, i, b))

        candidates.sort(key=lambda x: x[0], reverse=True)
        if candidates and candidates[0][0] >= 0.80:
            s, i, b = candidates[0]
            used.add(i)
            diff = round(a['price'] - b['price'], 2)
            matches.append({
                'product': a['title'],
                'gms': a,
                'mobigo': b,
                'difference': diff,
                'match_score': s,
            })

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
