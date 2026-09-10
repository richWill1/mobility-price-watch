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

# These words are not product identity. They must NEVER be enough to create
# a comparison. In particular, "Li" is a battery/variant descriptor, not a
# model name: Aeron Li and Ranger Li are different products.
GENERIC = {
    'ex', 'demo', 'display', 'used', 'refurbished', 'refurb', 'preowned', 'pre',
    'owned', 'second', 'hand', 'new', 'clearance', 'sale', 'portable', 'lightweight',
    'mobility', 'scooter', 'powerchair', 'power', 'chair', 'electric', 'folding',
    'foldable', 'premium', 'excellent', 'fair', 'good', 'li',
    'purple', 'black', 'blue', 'white', 'red', 'grey', 'gray',
    '2023', '2024', '2025', '2026',
    '4mph', '3mph', '4', '3'
}

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


def identity_tokens(name):
    """Extract only tokens that can safely contribute to model identity."""
    return tokens(name) - GENERIC


def brand_tokens(name):
    return tokens(name) & BRANDS


def confident_match(a, b):
    """Return a score only when the exact product identity is defensible.

    This is intentionally conservative. A shared brand, category word or
    suffix such as "Li" is never enough. Different model names (Aeron vs
    Ranger, Vector vs Evisu, etc.) are an explicit NO MATCH.
    """
    ta = identity_tokens(a)
    tb = identity_tokens(b)
    ba = brand_tokens(a)
    bb = brand_tokens(b)

    # If both expose a recognised brand, it must be the same brand.
    if ba and bb and not (ba & bb):
        return 0

    # Compare actual model identity after removing brand words.
    model_a = ta - ba
    model_b = tb - bb

    # There must be at least one distinctive model word on each side.
    alpha_a = {x for x in model_a if x.isalpha() and len(x) >= 5}
    alpha_b = {x for x in model_b if x.isalpha() and len(x) >= 5}
    if not alpha_a or not alpha_b:
        return 0

    # Exact overlap of a distinctive model token is mandatory.
    shared_model = alpha_a & alpha_b
    if not shared_model:
        return 0

    # If both listings contain multiple distinctive model words, all distinctive
    # words need to be compatible. This prevents e.g. "Vector Plus" matching
    # "Vector" when the extra model word identifies a different variant.
    if len(alpha_a) > 1 and len(alpha_b) > 1:
        if alpha_a != alpha_b:
            return 0

    # If one side has additional distinctive model words, do not guess that the
    # shorter title is the same product. Only accept a subset when the extra
    # words are clearly descriptive; otherwise omit the comparison.
    if alpha_a != alpha_b:
        return 0

    return 0.99


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
            s = confident_match(a['title'], b['title'])
            if s >= 0.99:
                candidates.append((s, i, b))

        # A product is shown only when there is one unique verified candidate.
        # If two Mobigo listings are equally plausible, do not guess.
        candidates.sort(key=lambda x: x[0], reverse=True)
        if candidates:
            best = candidates[0]
            tied = [c for c in candidates if c[0] == best[0]]
            if len(tied) == 1:
                s, i, b = best
                used.add(i)
                diff = round(a['price'] - b['price'], 2)
                matches.append({
                    'product': a['title'],
                    'gms': a,
                    'mobigo': b,
                    'difference': diff,
                    'match_score': s,
                    'match_status': 'Verified match',
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
