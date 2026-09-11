from flask import Flask, jsonify, render_template, Response
import csv, io, threading, time
from scraper import compare

app=Flask(__name__)
state={'rows':[],'updated':0,'running':False,'error':None,'stage':'Idle','progress':0,'total':0}
lock=threading.Lock()

def worker():
    with lock:
        if state['running']: return
        state.update(running=True,error=None,stage='Starting',progress=0,total=0)
    def progress(stage,done,total):
        with lock: state.update(stage=stage,progress=done,total=total)
    try:
        rows=compare(progress=progress)
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
def health(): return {'status':'ok','running':state['running'],'count':len(state['rows'])}

if __name__=='__main__': app.run(host='0.0.0.0',port=10000)
