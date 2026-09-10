from flask import Flask, jsonify, render_template
import requests, re, time
from xml.etree import ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)

SITES = {
    'GMS Mobility': 'https://www.gmsmobility.co.uk',
    'Mobigo': 'https://mobigo.co.uk',
}

session = requests.Session()
session.headers.update({'User-Agent': 'Mozilla/5.0 (compatible; MobilityPriceWatch/2.0)'})
cache = {'ts': 0, 'products': []}

GENERIC = {
    'ex', 'demo', 'display', 'used', 'refurbished', 'refurb', 'preowned', 'pre', 'owned',
    'second', 'hand', 'new', 'clearance', 'sale', 'portable', 'lightweight', 'mobility',
    'scooter', 'powerchair', 'power', 'chair', 'electric', 'folding', 'foldable', 'buggy',
    'carbon', 'fibre', 'fiber', 'wheelchair', 'transportable', 'road', 'pavement',
    'comfort', 'comforter', 'car', 'boot', 'mph', 'colour', 'color', 'on', 'off',
    'offer', 'offers', 'fantastic', 'value', 'popular', 'brilliant', 'stunning', 'super',
    'excellent', 'fair', 'good', 'premium', 'quality', 'fully', 'checked', 'model',
    'late', 'bought', 'sold', '2023', '2024', '2025', '2026', '2022', '2021', '2020',
    'purple', 'black', 'blue', 'white', 'red', 'grey', 'gray', 'teal', 'dune', 'yellow',
}

BRAND_ALIASES = {
    'careco': 'careco', 'care-co': 'careco',
    'abilize': 'abilize',
    'li-tech': 'litech', 'litech': 'litech', 'li': 'litech',
    'i-go': 'igo', 'igo': 'igo',
    'prolite': 'prolite', 'pro-lite': 'prolite',
    'pride': 'pride', 'drive': 'drive', 'kymco': 'kymco', 'rascal': 'rascal',
    'solax': 'solax', 'quickie': 'quickie', 'movinglife': 'movinglife',
    'scooterpac': 'scooterpac', 'quingo': 'quingo', 'motion': 'motion',
    'tuni': 'tuni', 'one': 'one', 'komfi': 'komfi', 'x-go': 'xgo',
}


def money(value):
    try:
        return float(value)
    except Exception:
        return None


def words(text):
    return re.findall(r'[a-z0-9]+', (text or '').lower().replace('&', ' and '))


def brand_key(title, vendor=''):
    # Prefer an explicit vendor when it looks like a manufacturer.
    vt = words(vendor)
    if vt:
        joined = '-'.join(vt[:2])
        if joined in BRAND_ALIASES:
            return BRAND_ALIASES[joined]
        if vt[0] in BRAND_ALIASES:
            return BRAND_ALIASES[vt[0]]
    ws = words(title)
    # Check 2-word brand aliases first.
    for i in range(min(2, len(ws))):
        pair = '-'.join(ws[i:i+2])
        if pair in BRAND_ALIASES:
            return BRAND_ALIASES[pair]
    if ws and ws[0] in BRAND_ALIASES:
        return BRAND_ALIASES[ws[0]]
    return ws[0] if ws else ''


def model_key(title, url='', vendor=''):
    """Canonical exact-model key.

    Keeps model suffixes/numbers such as LI, 2, 3 and SLE. Removes only sales,
    condition, colour, category and date language. The resulting ordered phrase
    must be identical on both retailers for an automatic match.
    """
    ws = words(title)
    brand = brand_key(title, vendor)

    # Remove known brand tokens/aliases from the title.
    cleaned = []
    i = 0
    while i < len(ws):
        pair = '-'.join(ws[i:i+2])
        if pair in BRAND_ALIASES:
            i += 2
            continue
        if ws[i] in BRAND_ALIASES:
            i += 1
            continue
        if ws[i] in GENERIC:
            i += 1
            continue
        cleaned.append(ws[i])
        i += 1

    # URL slug is useful as a second identity signal, especially when titles are verbose.
    slug = ''
    if '/products/' in (url or ''):
        slug = (url.split('/products/', 1)[1].split('?', 1)[0] or '').lower()
        slug_words = [w for w in words(slug) if w not in GENERIC and w not in BRAND_ALIASES]
    else:
        slug_words = []

    title_key = ' '.join(cleaned).strip()
    slug_key = ' '.join(slug_words).strip()

    # Most Shopify product titles already contain the exact model. Prefer that;
    # URL key is used to reject obvious conflicting model names.
    return title_key, slug_key, brand


def confident_match(a, b):
    ta, ua, ba = model_key(a['title'], a['url'], a.get('vendor', ''))
    tb, ub, bb = model_key(b['title'], b['url'], b.get('vendor', ''))

    if not ta or not tb:
        return 0
    if ba and bb and ba != bb:
        return 0

    # Exact canonical model phrase is required.
    if ta != tb:
        return 0

    # If both URLs expose a model slug, their canonical URL identities must also agree.
    if ua and ub and ua != ub:
        # Permit harmless extra words in the URL by requiring one to be a complete prefix
        # of the other, but never permit different model names.
        ca, cb = set(ua.split()), set(ub.split())
        if not (ca.issubset(cb) or cb.issubset(ca)):
            return 0

    return 1.0


def parse_product_json(text, url):
    try:
        data = __import__('json').loads(text)
        title = data.get('title') or ''
        vendor = data.get('vendor') or ''
        variants = data.get('variants') or []
        prices = [money(v.get('price')) for v in variants if money(v.get('price')) is not None]
        image = None
        if data.get('featured_image'):
            image = data.get('featured_image')
        elif data.get('images'):
            image = data.get('images')[0]
        if title and prices:
            return {'title': title.strip(), 'price': min(prices), 'url': url, 'image': image, 'vendor': vendor}
    except Exception:
        pass
    return None


def fetch_product(url):
    try:
        # Shopify JSON endpoint gives clean structured product data where enabled.
        for suffix in ['.js', '.json']:
            r = session.get(url + suffix, timeout=10)
            if r.ok and 'json' in (r.headers.get('content-type') or ''):
                p = parse_product_json(r.text, url)
                if p:
                    return p
        r = session.get(url, timeout=10)
        if not r.ok:
            return None
        html = r.text
        title_m = re.search(r'<meta[^>]+property=[\"\']og:title[\"\'][^>]+content=[\"\']([^\"\']+)', html, re.I)
        price_m = re.search(r'<meta[^>]+property=[\"\']product:price:amount[\"\'][^>]+content=[\"\']([^\"\']+)', html, re.I)
        if not title_m:
            title_m = re.search(r'<title>(.*?)</title>', html, re.I | re.S)
        if title_m and price_m:
            return {'title': re.sub(r'<.*?>', '', title_m.group(1)).strip(), 'price': money(price_m.group(1)), 'url': url, 'image': None, 'vendor': ''}
    except Exception:
        return None
    return None


def sitemap_urls(base):
    urls = []
    try:
        r = session.get(base + '/sitemap.xml', timeout=15)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        ns = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        locs = [x.text.strip() for x in root.findall('.//sm:loc', ns) if x.text]
        child = [u for u in locs if 'sitemap_products_' in u]
        if not child:
            child = [u for u in locs if '/sitemap' in u]
        if child:
            for sm in child[:20]:
                try:
                    rr = session.get(sm, timeout=15)
                    rr.raise_for_status()
                    rt = ET.fromstring(rr.content)
                    for x in rt.findall('.//sm:loc', ns):
                        if x.text and '/products/' in x.text and x.text not in urls:
                            urls.append(x.text.strip())
                except Exception:
                    continue
        else:
            urls = [u for u in locs if '/products/' in u]
    except Exception:
        pass
    return urls


def scrape_site(retailer, base):
    urls = sitemap_urls(base)
    products = []
    # Limit to 500 recent/catalogue product URLs to keep refresh practical.
    with ThreadPoolExecutor(max_workers=12) as ex:
        futures = [ex.submit(fetch_product, u) for u in urls[:500]]
        for f in as_completed(futures):
            p = f.result()
            if p:
                p['retailer'] = retailer
                products.append(p)
    return products


def scrape():
    site_products = {}
    for retailer, base in SITES.items():
        site_products[retailer] = scrape_site(retailer, base)

    gms = site_products.get('GMS Mobility', [])
    mobigo = site_products.get('Mobigo', [])

    # Index Mobigo products by exact model identity, then only match an identity
    # when there is exactly one candidate on each side.
    mob_index = {}
    for b in mobigo:
        mk = model_key(b['title'], b['url'], b.get('vendor', ''))
        mob_index.setdefault(mk, []).append(b)

    gms_index = {}
    for a in gms:
        mk = model_key(a['title'], a['url'], a.get('vendor', ''))
        gms_index.setdefault(mk, []).append(a)

    matches = []
    for mk, gs in gms_index.items():
        bs = mob_index.get(mk, [])
        if len(gs) != 1 or len(bs) != 1:
            continue
        a, b = gs[0], bs[0]
        s = confident_match(a, b)
        if s == 1.0:
            matches.append({
                'product': mk[0],
                'gms': a,
                'mobigo': b,
                'difference': round(a['price'] - b['price'], 2),
                'match_score': 1.0,
                'match_status': 'Verified exact model',
            })

    matches.sort(key=lambda x: abs(x['difference']), reverse=True)
    return matches


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/api/prices')
def prices():
    now = time.time()
    # Refresh every 15 minutes; full-catalogue crawling is deliberately cached.
    if now - cache['ts'] > 900 or not cache['products']:
        cache['products'] = scrape()
        cache['ts'] = now
    return jsonify({'updated': cache['ts'], 'matches': cache['products']})


@app.route('/health')
def health():
    return {'status': 'ok'}


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
