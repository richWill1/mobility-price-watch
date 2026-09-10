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

# Never use these words as evidence that two products are the same.
GENERIC = {
    'ex', 'demo', 'display', 'used', 'refurbished', 'refurb', 'preowned', 'pre',
    'owned', 'second', 'hand', 'new', 'clearance', 'sale', 'portable', 'lightweight',
    'mobility', 'scooter', 'powerchair', 'power', 'chair', 'electric', 'folding',
    'foldable', 'premium', 'excellent', 'fair', 'good',
    'purple', 'black', 'blue', 'white', 'red', 'grey', 'gray',
    '2024', '2025', '2026'
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
    """Extract product identity tokens only.

    Generic sales language is ignored, but model numbers/suffixes are retained.
    This is intentionally strict: an uncertain product is NOT compared.
    """
    return tokens(name) - GENERIC


def brand_tokens(name):
    return tokens(name) & BRANDS


def confident_match(a, b):
    """Return a confidence score only for a defensible exact-model match.

    Rules:
    1. Both listings must expose the same brand when both expose a known brand.
    2. At least one distinctive model token must be shared.
    3. If either listing contains model numbers, they must agree.
    4. Conflicting distinctive model names mean NO MATCH.
    5. Generic category/condition words can never create a match.

    This deliberately prefers false negatives over false positives.
    """
    ta = identity_tokens(a)
    tb = identity_tokens(b)
    ba = brand_tokens(a)
    bb = brand_tokens(b)

    if ba and bb and not (ba & bb):
        return 0

    # Product/model numbers are strong identity evidence. If either side has
    # numbers, require the same number(s) rather than treating them as noise.
    nums_a = {x for x in ta if x.isdigit()}
    nums_b = {x for x in tb if x.isdigit()}
    if (nums_a or nums_b) and nums_a != nums_b:
        return 0

    shared = ta & tb
    if not shared:
        return 0

    # Remove brand words from the model identity comparison.
    model_a = ta - ba
    model_b = tb - bb
    shared_model = model_a & model_b
    if not shared_model:
        return 0

    # A distinctive model name must agree. If both sides have multiple
    # distinctive words, require meaningful overlap rather than one accidental word.
    if len(shared_model) >= 2:
        return 0.99

    # Single-word model matches are accepted only when that word is distinctive
    # enough to identify a model (e.g. Stratus, Evisu, Aeron, Ranger, Vector).
    word = next(iter(shared_model))
    if len(word) < 5:
        return 0

    # If both sides have other distinctive alphabetic model words, a mismatch
    # is evidence that these are different models (Aeron vs Ranger).
    alpha_a = {x for x in model_a if x.isalpha() and len(x) >= 5}
    alpha_b = {x for x in model_b if x.isalpha() and len(x) >= 5}
    if alpha_a and alpha_b and not (alpha_a & alpha_b):
        return 0

    return 0.95


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
            if s >= 0.95:
                candidates.append((s, i, b))

        # Only accept a match when there is exactly one best candidate.
        # Ties/ambiguity are deliberately excluded.
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
