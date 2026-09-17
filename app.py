from flask import Flask, jsonify, render_template
import json, os, re, threading, time
from datetime import datetime, timezone
import requests

app = Flask(__name__)
SERP_ENDPOINT = "https://serpapi.com/search.json"
SERP_KEY = os.getenv("SERPAPI_KEY", "").strip()
LOCATION = os.getenv("SERP_LOCATION", "United Kingdom")
GOOGLE_DOMAIN = os.getenv("GOOGLE_DOMAIN", "google.co.uk")
session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; MobilityPriceWatch/10.0)"})
cache = {"ts": 0, "rows": [], "running": False, "error": None}

DEMO_RESULTS = {"Deluxe Fleece Support Pillow": [
    {"position":1,"title":"CareCo | Deluxe Fleece Support Pillow | Soft...","retailer":"CareCo","price":24.99,"old_price":39.99,"delivery":"+£3.95 delivery","url":"https://www.careco.co.uk/","badge":"PRICE DROP"},
    {"position":2,"title":"Soft Sherpa Fleece Support Pillow","retailer":"Temu","price":13.78,"old_price":None,"delivery":"+£2.00 delivery","url":"https://www.temu.com/","badge":""},
    {"position":3,"title":"Diana Cowpe Fleece Back Support Cushion","retailer":"Amazon.co.uk","price":18.99,"old_price":None,"delivery":"Free of charge","url":"https://www.amazon.co.uk/","badge":""},
    {"position":4,"title":"Homescapes Cotton Back Support Lumbar Cushion","retailer":"Homescapes","price":21.99,"old_price":None,"delivery":"+£4.50 delivery","url":"https://www.homescapesonline.com/","badge":""},
    {"position":5,"title":"Back Support Cushion","retailer":"Essential Aids","price":24.90,"old_price":None,"delivery":"+£4.99 delivery","url":"https://www.essentialaids.com/","badge":""},
]}

def money(v):
    try: return float(str(v).replace(',','').replace('£','').strip())
    except Exception: return None

def norm(s): return re.sub(r'\s+',' ',(s or '').strip())
def history_path(): return os.path.join(os.path.dirname(__file__),'data','serp_history.json')
def load_history():
    try:
        with open(history_path(),encoding='utf-8') as f: return json.load(f)
    except Exception: return []
def save_history(history):
    os.makedirs(os.path.dirname(history_path()),exist_ok=True)
    with open(history_path(),'w',encoding='utf-8') as f: json.dump(history[-90:],f,ensure_ascii=False,separators=(',',':'))
def load_watchlist():
    path=os.path.join(os.path.dirname(__file__),'watchlist.txt')
    if not os.path.exists(path): return []
    seen=set();out=[]
    for line in open(path,encoding='utf-8'):
        s=norm(line)
        if s and s not in seen: seen.add(s);out.append(s)
    return out

def serp_search(query):
    if not SERP_KEY: return None,'SERPAPI_KEY not configured'
    params={'api_key':SERP_KEY,'engine':'google_shopping','q':query,'location':LOCATION,'google_domain':GOOGLE_DOMAIN,'gl':'uk','hl':'en','device':'desktop'}
    r=session.get(SERP_ENDPOINT,params=params,timeout=45);r.raise_for_status();data=r.json()
    results=[]
    for item in data.get('shopping_results') or []:
        price=item.get('extracted_price')
        if price is None: price=money(item.get('price'))
        if price is None or not item.get('title'): continue
        results.append({'position':item.get('position'),'title':norm(item.get('title')),'retailer':item.get('source') or 'Unknown','price':price,'old_price':item.get('extracted_old_price'),'delivery':item.get('delivery') or item.get('shipping') or '','url':item.get('link') or item.get('product_link') or '','rating':item.get('rating'),'reviews':item.get('reviews'),'badge':item.get('tag') or '','condition':item.get('second_hand_condition') or 'New'})
        if len(results)>=5: break
    return results,None

def analyse(query,results,demo=False):
    results=results[:5];prices=[r['price'] for r in results if r.get('price') is not None]
    careco=next((r for r in results if 'careco' in r.get('retailer','').lower() or 'careco' in r.get('title','').lower()),None)
    competitors=[r for r in results if r is not careco];cp=[r['price'] for r in competitors if r.get('price') is not None]
    avg=round(sum(prices)/len(prices),2) if prices else None
    comp_avg=round(sum(cp)/len(cp),2) if cp else None
    vals=sorted(cp);n=len(vals);median=round(vals[n//2] if n%2 else (vals[n//2-1]+vals[n//2])/2,2) if vals else None
    cheapest=min(competitors,key=lambda x:x['price'],default=None)
    gap=round(careco['price']-comp_avg,2) if careco and comp_avg is not None else None
    gap_pct=round(gap/comp_avg*100,1) if gap is not None and comp_avg else None
    return {'product':query,'results':results,'top5_average':avg,'competitor_average':comp_avg,'competitor_median':median,'careco':careco,'cheapest_competitor':cheapest,'careco_gap':gap,'careco_gap_pct':gap_pct,'competitors_cheaper':sum(1 for r in competitors if careco and r['price']<careco['price']),'scrape_mode':'DEMO' if demo else 'LIVE','scraped_at':datetime.now(timezone.utc).isoformat()}

def scrape_term(query):
    live,err=serp_search(query)
    if live is not None: return analyse(query,live),None
    if query in DEMO_RESULTS: return analyse(query,DEMO_RESULTS[query],True),None
    return None,err

def scrape_all():
    rows=[];errors=[]
    for query in load_watchlist():
        try:
            row,err=scrape_term(query)
            if row: rows.append(row)
            elif err: errors.append({'product':query,'error':err})
        except Exception as exc: errors.append({'product':query,'error':str(exc)})
    return rows,errors

def refresh_worker():
    if cache['running']: return
    cache['running']=True;cache['error']=None
    try:
        rows,errors=scrape_all();cache['rows']=rows;cache['ts']=time.time();cache['error']=f'{len(errors)} terms failed' if errors else None
        h=load_history();h.append({'date':datetime.now(timezone.utc).isoformat(),'rows':rows,'errors':errors});save_history(h)
    except Exception as exc: cache['error']=str(exc)
    finally: cache['running']=False

@app.route('/')
def home(): return render_template('index.html')
@app.route('/api/prices')
def prices():
    if cache['rows']: return jsonify({'updated':cache['ts'],'matches':cache['rows'],'count':len(cache['rows']),'refreshing':cache['running'],'error':cache['error'],'live':bool(SERP_KEY)})
    h=load_history()
    if h:
        latest=h[-1];cache['rows']=latest.get('rows',[]);cache['ts']=datetime.fromisoformat(latest['date']).timestamp()
    elif not cache['running']: threading.Thread(target=refresh_worker,daemon=True).start()
    return jsonify({'updated':cache['ts'],'matches':cache['rows'],'count':len(cache['rows']),'refreshing':cache['running'],'error':cache['error'],'live':bool(SERP_KEY)})
@app.route('/api/run-daily',methods=['GET','POST'])
def run_daily():
    if not cache['running']: threading.Thread(target=refresh_worker,daemon=True).start()
    return jsonify({'ok':True,'live':bool(SERP_KEY)})
@app.route('/api/report/<path:term>')
def report_term(term):
    term=norm(term)
    for row in cache['rows']:
        if row['product'].lower()==term.lower(): return jsonify(row)
    for run in reversed(load_history()):
        for row in run.get('rows',[]):
            if row['product'].lower()==term.lower(): return jsonify(row)
    row,err=scrape_term(term)
    return jsonify(row) if row else (jsonify({'error':err or 'No result'}),404)
@app.route('/health')
def health(): return {'status':'ok','live_serp':bool(SERP_KEY),'count':len(cache['rows']),'refreshing':cache['running']}
if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.getenv('PORT','10000')))
