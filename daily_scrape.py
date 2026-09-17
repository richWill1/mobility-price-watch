from app import scrape_all, save_history, load_history
from datetime import datetime, timezone

rows, errors = scrape_all()
history = load_history()
history.append({
    'date': datetime.now(timezone.utc).isoformat(),
    'rows': rows,
    'errors': errors,
})
save_history(history)
print(f'Daily SERP scrape complete: {len(rows)} terms, {sum(len(r.get("results", [])) for r in rows)} products, {len(errors)} errors')
