"""
Station results dashboard — the Zoox-scale version of the heartbeat dashboard the
user built at 2G. Stations write results to a shared SQLite DB (results.ResultStore);
this serves a live view: per-station heartbeats, fleet yield, and the slowest-yielding
tests (where to aim continuous improvement).

Run:
    pip install fastapi uvicorn
    COMPUTETEST_DB=results.db uvicorn dashboard.app:app --reload
    # then open http://127.0.0.1:8000

Kept dependency-light: if FastAPI isn't installed, importing this module raises a
clear message rather than breaking the rest of the toolkit.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
except ImportError as e:  # pragma: no cover
    raise SystemExit("dashboard needs FastAPI: pip install fastapi uvicorn") from e

from computetest.results import ResultStore  # noqa: E402

DB = os.environ.get("COMPUTETEST_DB", ":memory:")
app = FastAPI(title="Compute Test Dashboard")


def _store() -> ResultStore:
    return ResultStore(DB)


@app.get("/api/summary")
def summary():
    s = _store()
    try:
        return {"summary": s.summary(), "stations": s.stations(),
                "yield_by_test": s.yield_by_test()}
    finally:
        s.close()


@app.get("/api/results")
def results(limit: int = 100):
    s = _store()
    try:
        return {"results": s.recent(limit)}
    finally:
        s.close()


PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>Compute Test Dashboard</title>
<style>
 body{font:14px system-ui;margin:24px;background:#0f1115;color:#e6e6e6}
 h1{font-size:20px} .grid{display:flex;gap:24px;flex-wrap:wrap}
 .card{background:#1a1d24;border:1px solid #2a2f3a;border-radius:8px;padding:16px;min-width:260px}
 .big{font-size:32px;font-weight:700} .pass{color:#3fb950} .fail{color:#f85149}
 table{border-collapse:collapse;width:100%;margin-top:8px} td,th{padding:4px 8px;text-align:left;border-bottom:1px solid #2a2f3a}
 .dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}
</style></head><body>
<h1>Compute Test Dashboard</h1>
<div class=grid>
 <div class=card><div>Fleet yield</div><div id=yield class=big>—</div><div id=counts></div></div>
 <div class=card><div>Stations</div><div id=stations></div></div>
</div>
<div class=card style=margin-top:24px><div>Lowest-yielding tests (aim CI here)</div><table id=ytable></table></div>
<div class=card style=margin-top:24px><div>Recent results</div><table id=recent></table></div>
<script>
async function refresh(){
 const s=await (await fetch('/api/summary')).json();
 const r=await (await fetch('/api/results?limit=40')).json();
 const sm=s.summary;
 document.getElementById('yield').textContent=(sm.yield*100).toFixed(1)+'%';
 document.getElementById('yield').className='big '+(sm.yield>=0.99?'pass':'fail');
 document.getElementById('counts').textContent=`${sm.passed} pass / ${sm.failed} fail / ${sm.total} total`;
 document.getElementById('stations').innerHTML=s.stations.map(x=>
   `<div><span class=dot style="background:${x.age_s<15?'#3fb950':'#f85149'}"></span>${x.station} — ${x.status} (${x.age_s}s ago)</div>`).join('')||'(none)';
 document.getElementById('ytable').innerHTML='<tr><th>subsystem</th><th>test</th><th>yield</th><th>n</th></tr>'+
   s.yield_by_test.map(y=>`<tr><td>${y.subsystem}</td><td>${y.test}</td><td>${(y.yield*100).toFixed(0)}%</td><td>${y.n}</td></tr>`).join('');
 document.getElementById('recent').innerHTML='<tr><th>subsystem</th><th>target</th><th>test</th><th>status</th><th>msg</th></tr>'+
   r.results.map(x=>`<tr><td>${x.subsystem}</td><td>${x.target}</td><td>${x.test_name}</td><td class=${x.status=='pass'?'pass':'fail'}>${x.status}</td><td>${x.message||''}</td></tr>`).join('');
}
refresh(); setInterval(refresh,5000);
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE
