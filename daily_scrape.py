import json, os
from datetime import datetime, timezone
from app import build_rows, load_history, history_path

history=load_history()
previous=history[-1]['rows'] if history else []
rows=build_rows(previous)
os.makedirs(os.path.dirname(history_path()),exist_ok=True)
history.append({'date':datetime.now(timezone.utc).isoformat(),'rows':rows})
history=history[-90:]
with open(history_path(),'w',encoding='utf-8') as f:json.dump(history,f,ensure_ascii=False,separators=(',',':'))
print(f'Daily price scrape complete: {len(rows)} products')
