from flask import Flask, jsonify, render_template, Response
import csv, io, threading, time, re
import requests
from rapidfuzz import fuzz, process

app=Flask(__name__)
SHEET_ID='1dG7BtpO48dln4R30tLNK3uMO6T9LFkFpxmmJOUr4gCc'
CARECO_GID='0'
CCS_GID='1199687859'
state={'rows':[],'updated':0,'running':False,'error':None,'stage':'Idle','progress':0,'total':0}
lock=threading.Lock()

def sheet_csv(gid):
    url=f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={gid}'
    r=requests.get(url,timeout=60)
    r.raise_for_status()
    return list(csv.DictReader(io.StringIO(r.text)))

def norm(s):
    s=str(s or '').lower().replace('&amp;','and')
    s=re.sub(r'[^a-z0-9]+',' ',s)
    return re.sub(r'\s+',' ',s).strip()

def price(row):
    for k in ('sale price','price'):
        v=str(row.get(k,'')).replace('£','').replace(',','').strip()
        if v:
            try:return float(v)
            except:pass
    return None

def compare_sheets(progress=None):
    if progress: progress('Downloading CareCo',0,1)
    care=sheet_csv(CARECO_GID)
    if progress: progress('Downloading Complete Care Shop',0,1)
    ccs=sheet_csv(CCS_GID)
    choices=[norm(x.get('title')) for x in ccs]
    rows=[]
    total=len(care)
    for i,r in enumerate(care,1):
        title=norm(r.get('title'))
        candidates=process.extract(title,choices,scorer=fuzz.token_set_ratio,limit=8,score_cutoff=45)
        best=None
        for _,ts,j in candidates:
            c=ccs[j]
            brand=fuzz.ratio(norm(r.get('brand')),norm(c.get('brand'))) if r.get('brand') and c.get('brand') else 0
            typ=fuzz.token_set_ratio(norm(r.get('product type')),norm(c.get('product type'))) if r.get('product type') and c.get('product type') else 0
            # Strong title similarity is the main signal; brand/type help distinguish variants.
            score=ts*0.72+typ*0.18+brand*0.10
            if best is None or score>best[0]: best=(score,ts,typ,brand,j)
        if best:
            score,ts,typ,brand,j=best; c=ccs[j]
            # Conservative thresholds to avoid false equivalents.
            conf='High' if score>=82 and ts>=78 else ('Review' if score>=70 and ts>=65 else 'No confident match')
        else:
            score=ts=typ=brand=0; c={}; conf='No confident match'
        matched=conf!='No confident match'
        cp=price(r); mp=price(c) if matched else None
        diff=(cp-mp) if cp is not None and mp is not None else None
        cheaper='No CCS match' if mp is None else ('CCS' if diff>0 else ('CareCo' if diff<0 else 'Same price'))
        rows.append({'careco':{'title':r.get('title',''),'sku':r.get('mpn') or r.get('id',''),'price':cp or 0,'url':r.get('link','')},
                     'complete':({'title':c.get('title',''),'sku':c.get('mpn') or c.get('id',''),'price':mp or 0,'url':c.get('link','')} if matched else None),
                     'difference':diff,'cheaper':('Complete Care Shop' if cheaper=='CCS' else cheaper),
                     'match_status':conf,'match_score':score/100 if score else 0})
        if progress and (i==1 or i%25==0 or i==total): progress('Matching products',i,total)
    return rows

def worker():
    with lock:
        if state['running']: return
        state.update(running=True,error=None,stage='Starting',progress=0,total=0)
    try:
        rows=compare_sheets(progress)
        with lock: state.update(rows=rows,updated=time.time(),stage='Complete',progress=len(rows),total=len(rows))
    except Exception as e:
        with lock: state.update(error=str(e),stage='Error')
    finally:
        with lock: state['running']=False

@app.route('/')
def home(): return render_template('index.html')

@app.route('/api/data')
def data():
    with lock: snapshot=dict(state); snapshot['count']=len(state['rows'])
    return jsonify(snapshot)

@app.post('/api/refresh')
def refresh():
    threading.Thread(target=worker,daemon=True).start()
    return jsonify({'ok':True})

@app.get('/api/export.csv')
def export_csv():
    out=io.StringIO(); w=csv.writer(out)
    w.writerow(['CareCo Product','CareCo SKU','CareCo Price','CareCo URL','Complete Care Product','Complete Care SKU','Complete Care Price','Complete Care URL','Difference','Cheaper','Match Status','Match Score'])
    with lock: rows=list(state['rows'])
    for r in rows:
        c=r['careco']; m=r['complete'] or {}
        w.writerow([c.get('title',''),c.get('sku',''),c.get('price',''),c.get('url',''),m.get('title',''),m.get('sku',''),m.get('price',''),m.get('url',''),r.get('difference',''),r.get('cheaper',''),r.get('match_status',''),r.get('match_score','')])
    return Response(out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=careco-complete-care-comparison.csv'})

@app.get('/health')
def health(): return {'status':'ok','running':state['running'],'count':len(state['rows']),'error':state['error']}

# Load the Google Sheet automatically when the service starts.
threading.Thread(target=worker,daemon=True).start()

if __name__=='__main__': app.run(host='0.0.0.0',port=10000)
