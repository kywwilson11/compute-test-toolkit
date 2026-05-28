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

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
except ImportError as e:  # pragma: no cover
    raise SystemExit("dashboard needs FastAPI: pip install fastapi uvicorn") from e

from computetest.results import ResultStore  # noqa: E402

log = logging.getLogger("computetest.dashboard")

DB = os.environ.get("COMPUTETEST_DB", ":memory:")


def _warn_if_in_memory(db: str) -> bool:
    """Each request opens a fresh connection, so an in-memory DB is per-request and
    empty — the dashboard would show nothing. Warn loudly (don't crash: the module
    must stay importable for tests and `--help`). Returns True if the DB is :memory:."""
    if db == ":memory:":
        log.warning(
            "COMPUTETEST_DB is ':memory:' — each request gets a fresh EMPTY in-memory "
            "database, so the dashboard will show no data. Point it at a shared file, "
            "e.g.  COMPUTETEST_DB=results.db uvicorn dashboard.app:app")
        return True
    return False


_DB_IN_MEMORY = _warn_if_in_memory(DB)
app = FastAPI(title="Compute Test Dashboard")


def _store() -> ResultStore:
    return ResultStore(DB)


@app.get("/api/summary")
def summary():
    s = _store()
    try:
        out = {"summary": s.summary(), "stations": s.stations(),
               "yield_by_test": s.yield_by_test()}
        if _DB_IN_MEMORY:
            out["db_warning"] = ("COMPUTETEST_DB=':memory:' — data is per-request and "
                                 "empty; set COMPUTETEST_DB to a file path.")
        return out
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
// Escape user/DB-controlled strings before they reach .innerHTML. Without this,
// any operator (or anyone who can write a plan config) can store HTML/JS in
// fields like target/test_name/message/station/subsystem and have it execute on
// every dashboard viewer's machine (stored XSS). Status/numeric fields are still
// validated below to a fixed allowlist.
function esc(v){
  return String(v).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}
async function refresh(){
 const s=await (await fetch('/api/summary')).json();
 const r=await (await fetch('/api/results?limit=40')).json();
 const sm=s.summary;
 document.getElementById('yield').textContent=(sm.yield*100).toFixed(1)+'%';
 document.getElementById('yield').className='big '+(sm.yield>=0.99?'pass':'fail');
 document.getElementById('counts').textContent=`${sm.passed} pass / ${sm.failed} fail / ${sm.total} total`;
 document.getElementById('stations').innerHTML=s.stations.map(x=>
   `<div><span class=dot style="background:${x.age_s<15?'#3fb950':'#f85149'}"></span>${esc(x.station)} — ${esc(x.status)} (${Number(x.age_s)}s ago)</div>`).join('')||'(none)';
 document.getElementById('ytable').innerHTML='<tr><th>subsystem</th><th>test</th><th>yield</th><th>n</th></tr>'+
   s.yield_by_test.map(y=>`<tr><td>${esc(y.subsystem)}</td><td>${esc(y.test)}</td><td>${(y.yield*100).toFixed(0)}%</td><td>${Number(y.n)}</td></tr>`).join('');
 // status is one of {pass, fail, skip}; allowlist before using as a CSS class to
 // prevent CSS-injection if a future record carries a hostile status string.
 const cls = v => (v==='pass'||v==='fail') ? v : '';
 document.getElementById('recent').innerHTML='<tr><th>subsystem</th><th>target</th><th>test</th><th>status</th><th>msg</th></tr>'+
   r.results.map(x=>`<tr><td>${esc(x.subsystem)}</td><td>${esc(x.target)}</td><td>${esc(x.test_name)}</td><td class=${cls(x.status)}>${esc(x.status)}</td><td>${esc(x.message||'')}</td></tr>`).join('');
}
refresh(); setInterval(refresh,5000);
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE
