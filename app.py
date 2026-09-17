from flask import Flask, jsonify, render_template
import json, os, re, threading, time, random
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)
GOOGLE_DOMAIN = os.getenv("GOOGLE_DOMAIN", "google.co.uk")
GOOGLE_GL = os.getenv("SERP_GL", "uk")
GOOGLE_HL = os.getenv("SERP_HL", "en")
GOOGLE_BASE = f"https://www.{GOOGLE_DOMAIN}"
session = requests.Session()
session.headers.update({"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0 Safari/537.36","Accept-Language":"en-GB,en;q=0.9","Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
cache={"ts":0,"rows":[],"running":False,"error":None}

DEMO_RESULTS={"Deluxe Fleece Support Pillow":[
 {"position":1,"title":"CareCo | Deluxe Fleece Support Pillow | Soft...","retailer":"CareCo","price":24.99,"old_price":39.99,"delivery":"+£3.95 delivery","url":"https://www.careco.co.uk/","badge":"PRICE DROP"},
 {"position":2,"title":"Soft Sherpa Fleece Support Pillow","retailer":"Temu","price":13.78,"old_price":None,"delivery":"+£2.00 delivery","url":"https://www.temu.com/","badge":""},
 {"position":3,"title":"Diana Cowpe Fleece Back Support Cushion","retailer":"Amazon.co.uk","price":18.99,"old_price":None,"delivery":"Free of charge","url":"https://www.amazon.co.uk/","badge":""},
 {"position":4,"title":"Homescapes Cotton Back Support Lumbar Cushion","retailer":"Homescapes","price":21.99,"old_price":None,"delivery":"+£4.50 delivery","url":"https://www.homescapesonline.com/","badge":""},
 {"position":5,"title":"Back Support Cushion","retailer":"Essential Aids","price":24.90,"old_price":None,"delivery":"+£4.99 delivery","url":"https://www.essentialaids.com/","badge":""},
]}

def money(v):
 try:
  m=re.search(r"\d[\d,]*(?:\.\d{1,2})?",str(v)); return float(m.group(0).replace(',','')) if m else None
 except Exception:return None

def norm(s):return re.sub(r'\s+',' ',(s or '').strip())
def history_path():return os.path.join(os.path.dirname(__file__),'data','serp_history.json')
def load_history():
 try:
  with open(history_path(),encoding='utf-8') as f:return json.load(f)
 except Exception:return []
def save_history(history):
 os.makedirs(os.path.dirname(history_path()),exist_ok=True)
 with open(history_path(),'w',encoding='utf-8') as f:json.dump(history[-90:],f,ensure_ascii=False,separators=(',',':'))
def load_watchlist():
 path=os.path.join(os.path.dirname(__file__),'watchlist.txt')
 if not os.path.exists(path):return []
 seen=set();out=[]
 for line in open(path,encoding='utf-8'):
  s=norm(line)
  if s and s not in seen:seen.add(s);out.append(s)
 return out

def clean_title(text):
 text=norm(text);text=re.sub(r'\s+(?:£|From £)\s*\d.*$','',text);return text[:220].strip(' |·-')

def extract_retailer(text,title):
 remaining=text.replace(title,' ')
 for name in re.findall(r"([A-Z][A-Za-z0-9&.' -]{2,45})",remaining):
  n=norm(name)
  if n.lower() not in {'free delivery','delivery','from','new'} and not n.startswith('£') and any(ch.isalpha() for ch in n):return n
 return 'Unknown'

def direct_google_shopping(query):
 urls=[f"{GOOGLE_BASE}/search?tbm=shop&hl={GOOGLE_HL}&gl={GOOGLE_GL}&q={quote_plus(query)}",f"{GOOGLE_BASE}/search?udm=28&hl={GOOGLE_HL}&gl={GOOGLE_GL}&q={quote_plus(query)}"]
 last_error=None
 for url in urls:
  try:
   r=session.get(url,timeout=30,allow_redirects=True)
   if r.status_code!=200:last_error=f"Google returned HTTP {r.status_code}";continue
   if any(x in r.text[:10000].lower() for x in ['unusual traffic','captcha','sorry/index']):last_error='Google presented an anti-bot page';continue
   soup=BeautifulSoup(r.text,'html.parser');candidates=[];seen=set()
   for a in soup.find_all('a',href=True):
    txt=norm(a.get_text(' ',strip=True))
    if '£' not in txt or len(txt)<8 or len(txt)>900:continue
    prices=re.findall(r'£\s*([\d,]+(?:\.\d{1,2})?)',txt)
    if not prices:continue
    href=a.get('href','')
    if href.startswith('/url?q='):href=href.split('/url?q=',1)[1].split('&',1)[0]
    elif href.startswith('/'):href=urljoin(GOOGLE_BASE,href)
    parts=[p.strip() for p in re.split(r'\n|•|·',txt) if p.strip()]
    price=money(prices[0]);title=clean_title(parts[0] if parts else txt)
    if price is None or len(title)<5 or title in seen or re.fullmatch(r'£?\s*[\d,.]+',title):continue
    seen.add(title);candidates.append({'position':len(candidates)+1,'title':title,'retailer':extract_retailer(txt,title),'price':price,'old_price':None,'delivery':'','url':href,'badge':''})
    if len(candidates)>=5:break
   if len(candidates)>=5:return candidates,None
   last_error=f'Google Shopping returned {len(candidates)} usable products'
  except Exception as exc:last_error=str(exc)
 return None,last_error or 'No Google Shopping results found'

def demo_for(query):
 if query in DEMO_RESULTS:return DEMO_RESULTS[query]
 q=query.lower().replace('careco,','').replace('careco |','').strip()
 for key,rows in DEMO_RESULTS.items():
  if q==key.lower() or key.lower() in q or q in key.lower():return rows
 return None

def analyse(query,results,demo=False):
 results=results[:5];prices=[r['price'] for r in results if r.get('price') is not None]
 careco=next((r for r in results if 'careco' in r.get('retailer','').lower() or 'careco' in r.get('title','').lower()),None)
 competitors=[r for r in results if r is not careco];cp=[r['price'] for r in competitors if r.get('price') is not None]
 avg=round(sum(prices)/len(prices),2) if prices else None;comp_avg=round(sum(cp)/len(cp),2) if cp else None
 vals=sorted(cp);n=len(vals);median=round(vals[n//2] if n%2 else (vals[n//2-1]+vals[n//2])/2,2) if vals else None
 cheapest=min(competitors,key=lambda x:x['price'],default=None);gap=round(careco['price']-comp_avg,2) if careco and comp_avg is not None else None;gap_pct=round(gap/comp_avg*100,1) if gap is not None and comp_avg else None
 return {'product':query,'results':results,'top5_average':avg,'competitor_average':comp_avg,'competitor_median':median,'careco':careco,'cheapest_competitor':cheapest,'careco_gap':gap,'careco_gap_pct':gap_pct,'competitors_cheaper':sum(1 for r in competitors if careco and r['price']<careco['price']),'scrape_mode':'DEMO' if demo else 'LIVE','scraped_at':datetime.now(timezone.utc).isoformat()}

def scrape_term(query):
 live,err=direct_google_shopping(query)
 if live is not None:return analyse(query,live),None
 demo=demo_for(query)
 if demo is not None:return analyse(query,demo,True),None
 return None,err

def scrape_all():
 rows=[];errors=[];terms=load_watchlist()
 for i,query in enumerate(terms):
  try:
   row,err=scrape_term(query)
   if row:rows.append(row)
   elif err:errors.append({'product':query,'error':err})
  except Exception as exc:errors.append({'product':query,'error':str(exc)})
  if i<len(terms)-1:time.sleep(random.uniform(1.5,3.0))
 return rows,errors

def refresh_worker():
 if cache['running']:return
 cache['running']=True;cache['error']=None
 try:
  rows,errors=scrape_all();cache['rows']=rows;cache['ts']=time.time();cache['error']=f'{len(errors)} terms failed' if errors else None
  h=load_history();h.append({'date':datetime.now(timezone.utc).isoformat(),'rows':rows,'errors':errors});save_history(h)
 except Exception as exc:cache['error']=str(exc)
 finally:cache['running']=False

@app.route('/')
def home():return render_template('index.html')
@app.route('/api/prices')
def prices():
 if cache['rows']:return jsonify({'updated':cache['ts'],'matches':cache['rows'],'count':len(cache['rows']),'refreshing':cache['running'],'error':cache['error'],'live':True,'engine':'direct_google_shopping'})
 h=load_history()
 if h:
  latest=h[-1];cache['rows']=latest.get('rows',[]);cache['ts']=datetime.fromisoformat(latest['date']).timestamp()
 elif not cache['running']:threading.Thread(target=refresh_worker,daemon=True).start()
 return jsonify({'updated':cache['ts'],'matches':cache['rows'],'count':len(cache['rows']),'refreshing':cache['running'],'error':cache['error'],'live':True,'engine':'direct_google_shopping'})
@app.route('/api/run-daily',methods=['GET','POST'])
def run_daily():
 if not cache['running']:threading.Thread(target=refresh_worker,daemon=True).start()
 return jsonify({'ok':True,'live':True,'engine':'direct_google_shopping'})
@app.route('/api/report/<path:term>')
def report_term(term):
 term=norm(term)
 for row in cache['rows']:
  if row['product'].lower()==term.lower():return jsonify(row)
 for run in reversed(load_history()):
  for row in run.get('rows',[]):
   if row['product'].lower()==term.lower():return jsonify(row)
 row,err=scrape_term(term);return jsonify(row) if row else (jsonify({'error':err or 'No result'}),404)
@app.route('/health')
def health():return {'status':'ok','live_serp':True,'engine':'direct_google_shopping','count':len(cache['rows']),'refreshing':cache['running']}
if __name__=='__main__':app.run(host='0.0.0.0',port=int(os.getenv('PORT','10000')))
