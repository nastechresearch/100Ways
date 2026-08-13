"""100Ways dashboard - the live web control room.

Serves a single-page dashboard over ``http.server`` (stdlib, no framework):

  GET  /                 the HTML page (inline JS, polls the APIs)
  GET  /api/state        repo, branch, behind/ahead, gate summary
  GET  /api/gaps         live gap analysis (errors 404/82/83/84 + explanations)
  GET  /api/logs         recent event log lines
  GET  /api/rules        branding rules (base + admin overrides)
  POST /api/rules        add/replace a rule override (admin token required)
  GET  /api/events       Server-Sent Events stream of live pipeline events

Admin permission: every POST requires ``HUNDREDWAYS_ADMIN_TOKEN`` (bearer).
Without it the dashboard is read-only.  All views share one thread-safe
state object so the page stays live even while the watcher runs.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .achievements import Achievements
from .analyzer import analyze
from .codes import code_for
from .rules import BrandingRules, tokens_from_overrides
from .security import DEFAULT_ADMIN_PASS, compile_token, verify_token
from .verify import _git_ok

DEFAULT_BIRTH = "0cafd22fb"
DEFAULT_PARENT = "03fa32c92"


class DashboardState:
    """Thread-safe in-memory state shared across requests and the watcher."""

    def __init__(self) -> None:
        self.events: queue.Queue = queue.Queue()
        self._snapshot: dict = {}
        self._lock = threading.Lock()

    def push_event(self, event: dict) -> None:
        self.events.put(event)
        with self._lock:
            self._snapshot = event

    def latest(self) -> dict:
        with self._lock:
            return dict(self._snapshot)


def _git(repo: str, *args: str) -> str:
    return _git_ok(repo, *args)


def repo_state(repo: str) -> dict:
    """Repo summary: branch, HEAD, behind/ahead vs the upstream ref."""
    out = {
        "repo": repo,
        "error": None,
    }
    try:
        out["branch"] = _git(repo, "branch", "--show-current").strip() or "(detached)"
        out["head"] = _git(repo, "rev-parse", "HEAD").strip()[:12]
    except Exception as exc:
        out["error"] = str(exc)
        out["branch"] = out["head"] = None
        return out
    try:
        _git(repo, "fetch", "upstream", "--quiet")
        upstream = _git(repo, "rev-parse", "upstream/main").strip()
        out["upstream_head"] = upstream[:12]
        out["behind"] = int(_git(repo, "rev-list", "--count", f"HEAD..{upstream}").strip())
        out["ahead"] = int(_git(repo, "rev-list", "--count", f"{upstream}..HEAD").strip())
    except Exception:
        out["behind"] = out["ahead"] = None
    return out


def gap_payload(repo: str, rules: BrandingRules) -> dict:
    """Live gap analysis as JSON (codes + explanations per file)."""
    try:
        _git(repo, "fetch", "upstream", "--quiet")
        upstream = _git(repo, "rev-parse", "upstream/main").strip()
        head = _git(repo, "rev-parse", "HEAD").strip()
        report = analyze(upstream, head, repo, rules)
    except Exception as exc:
        return {"error": str(exc)}
    entries = [
        {
            "path": e.path,
            "status": e.status,
            "code": e.code,
            "code_name": code_for(e.code).name,
            "violations": e.brand_violations,
            "explanation": e.explanation,
            "locked": e.locked,
            "asset": e.upstream_type,
        }
        for e in report.entries
        if e.code != 0
    ]
    return {
        "upstream": report.upstream_commit[:12],
        "nastech": report.nastech_commit[:12],
        "summary": report.summary,
        "code_counts": {code_for(c).name: n for c, n in sorted(report.code_counts().items(), reverse=True)},
        "entries": entries,
        "counts": {
            "missing": len(report.upstream_only()),
            "violations": len(report.violations()),
            "drift": len(report.changed()),
            "extras": len(report.nastech_only()),
        },
    }


class DashboardHandler(BaseHTTPRequestHandler):
    state: DashboardState = None  # class attr set at construction
    repo: str = ""
    home: str = ""
    admin_token: str = ""
    rules_override: str = ""
    birth: str = DEFAULT_BIRTH
    parent: str = DEFAULT_PARENT

    # -- helpers ------------------------------------------------------------

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, code: int = 200) -> None:
        self._send(code, json.dumps(payload).encode())

    def _rules(self) -> BrandingRules:
        return BrandingRules(tokens=tokens_from_overrides(self.rules_override))

    def _is_admin(self) -> bool:
        # Admin gate: the compiled form of DEFAULT_ADMIN_PASS is the default;
        # an operator-set token (HUNDREDWAYS_ADMIN_TOKEN) overrides it.  Typing
        # either the password or its compiled form grants access (see security).
        auth = self.headers.get("Authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else ""
        return verify_token(token, self.admin_token)

    def log_message(self, fmt: str, *args) -> None:  # quieter default
        pass

    # -- routes --------------------------------------------------------------

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, _HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/state":
            try:
                self._json({"state": repo_state(self.repo), "latest_event": self.state.latest()})
            except Exception as exc:
                self._json({"error": str(exc)}, 500)
        elif path == "/api/gaps":
            self._json(gap_payload(self.repo, self._rules()))
        elif path == "/api/rules":
            rules = tokens_from_overrides(self.rules_override)
            self._json({"tokens": [{"match": t.match, "replace": t.replace, "anchored": t.anchored} for t in rules]})
        elif path == "/api/achievements":
            ach = Achievements(self.home)
            self._json({"achievements": [{"name": k, "description": v.description, "emoji": v.emoji, "unlocked": u} for k, v, u in ach.list_all()]})
        elif path == "/api/logs":
            log_dir = os.path.join(self.home, "logs")
            lines = []
            for fn in ("agent.log", "errors.log", "events.jsonl"):
                fp = os.path.join(log_dir, fn)
                if os.path.exists(fp):
                    lines.extend([f"[{fn}] {l.rstrip()}" for l in _tail(fp, 200)])
            self._json({"logs": lines[-300:]})
        elif path == "/api/events":
            self._stream_events()
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/rules":
            self._json({"error": "not found"}, 404)
            return
        if not self._is_admin():
            self._json({"error": "forbidden: admin token required"}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode())
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "bad json"}, 400)
            return
        os.makedirs(os.path.dirname(self.rules_override), exist_ok=True)
        base = BrandingRules()
        base_matches = {t.match for t in base.tokens}
        current = tokens_from_overrides(self.rules_override)
        tokens = [{"match": t.match, "replace": t.replace, "anchored": t.anchored} for t in current if t.match not in base_matches]
        if payload.get("action") == "add":
            tokens.append({"match": payload["match"], "replace": payload.get("replace", ""), "anchored": bool(payload.get("anchored"))})
        elif payload.get("action") == "remove":
            tokens = [t for t in tokens if t["match"] != payload.get("match")]
        elif payload.get("action") == "replace":
            for t in tokens:
                if t["match"] == payload.get("match"):
                    t["replace"] = payload.get("replace", "")
        else:
            self._json({"error": "unknown action"}, 400)
            return
        with open(self.rules_override, "w", encoding="utf-8") as fh:
            json.dump({"tokens": tokens}, fh, indent=2)
        ach = Achievements(self.home)
        unlocked = ach.apply_event("admin_edit")
        self.state.push_event({"kind": "rules-edit", "ts": time.time(), "user": "admin"})
        self._json({"ok": True, "tokens": len(tokens), "unlocked": unlocked})

    # -- helpers --------------------------------------------------------------

    def _stream_events(self) -> None:
        """SSE: push new events and a heartbeat so the browser stays live."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            while True:
                try:
                    event = self.state.events.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def _tail(path: str, n: int) -> list[str]:
    """Last n lines of a file (cheap on large logs)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.readlines()[-n:]
    except OSError:
        return []


_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>100Ways dashboard</title>
<style>
  body{font-family:ui-monospace,Menlo,Consolas,monospace;background:#0d1117;color:#c9d1d9;margin:0;padding:24px}
  h1{color:#58a6ff;font-size:20px} h2{color:#79c0ff;font-size:15px;margin-top:28px}
  table{border-collapse:collapse;width:100%;font-size:12px}
  th,td{border:1px solid #30363d;padding:4px 8px;text-align:left}
  th{background:#161b22;color:#8b949e;position:sticky;top:0}
  .badge{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;font-weight:700}
  .ok{background:#1f6f2b}.warn{background:#9e6a03}.err{background:#b62324}.info{background:#1f6feb}
  .card{display:inline-block;background:#161b22;border:1px solid #30363d;border-radius:8px;padding:10px 16px;margin:4px 8px 4px 0}
  #filters{position:sticky;top:0;background:#0d1117;padding:8px 0}
  input[type=text]{background:#161b22;border:1px solid #30363d;color:#c9d1d9;padding:6px;border-radius:6px}
  button{background:#238636;color:#fff;border:0;padding:6px 12px;border-radius:6px;cursor:pointer;margin-left:6px}
  button.danger{background:#b62324}
  #gaps td.c404{color:#f85149}#gaps td.c82{color:#d29922}#gaps td.c83{color:#a5d6ff}#gaps td.c84{color:#7ee787}
  .admin{display:none} .admin.show{display:block}
</style></head><body>
<h1>🏗️ 100Ways dashboard <span id="adm" class="badge info" style="display:none">admin</span></h1>
<div id="state"></div>
<h2>Filters <button onclick="admin()">admin</button></h2>
<div id="filters">
  <input id="q" type="text" placeholder="filter path..." oninput="render()">
  <select id="c" onchange="render()">
    <option value="">all codes</option>
    <option value="MISSING">404 missing</option>
    <option value="VIOLATION">82 violation</option>
    <option value="DRIFT">83 drift</option>
    <option value="EXTRA">84 extra</option>
  </select>
</div>
<div id="admin" class="admin">
  <input id="tok" type="text" placeholder="admin token" size="40">
  <input id="m" type="text" placeholder="match" size="16">
  <input id="r" type="text" placeholder="replace" size="16">
  <button onclick="addRule()">add rule</button>
  <span id="admmsg"></span>
</div>
<h2>Gaps (live) <span id="sum"></span></h2>
<table><thead><tr><th>code</th><th>path</th><th>why</th></tr></thead><tbody id="gaps"></tbody></table>
<h2>Rules (<span id="rcount"></span>)</h2>
<div id="rules" style="font-size:12px"></div>
<h2>Live events</h2>
<div id="events" style="font-size:12px;max-height:220px;overflow:auto"></div>
<h2>Logs (tail)</h2>
<div id="logs" style="font-size:11px;color:#8b949e;white-space:pre-wrap;max-height:300px;overflow:auto"></div>
<script>
let DATA={gaps:[],counts:{}}, TOK=null;
async function get(u){try{const r=await fetch(u);return await r.json()}catch(e){return {error:String(e)}}}
async function load(){
  const st=await get('/api/state'); const g=await get('/api/gaps'); const rw=await get('/api/rules');
  DATA.gaps=g.entries||[]; DATA.counts=g.counts||{}; DATA.rules=rw.tokens||[]; DATA.state=st.state||{};
  document.getElementById('sum').textContent=g.summary||'';
  document.getElementById('rcount').textContent=DATA.rules.length;
  let s='';
  const d=DATA.state;
  s+=`<span class="card">branch <b>${d.branch||'-'}</b></span>`;
  s+=`<span class="card">HEAD <b>${d.head||'-'}</b></span>`;
  s+=`<span class="card">behind <b>${d.behind ?? '-'}</b></span><span class="card">ahead <b>${d.ahead ?? '-'}</b></span>`;
  s+=`<span class="card">missing <b>${DATA.counts.missing||0}</b></span>`;
  s+=`<span class="card">violations <b>${DATA.counts.violations||0}</b></span>`;
  s+=`<span class="card">drift <b>${DATA.counts.drift||0}</b></span>`;
  s+=`<span class="card">extras <b>${DATA.counts.extras||0}</b></span>`;
  document.getElementById('state').innerHTML=s;
  render(); renderRules();
}
function render(){
  const q=document.getElementById('q').value.toLowerCase();
  const c=document.getElementById('c').value;
  const rows=DATA.gaps.filter(g=>(!c||g.code_name===c)&&(!q||g.path.toLowerCase().includes(q)));
  document.getElementById('gaps').innerHTML=rows.map(g=>{
    const name=g.code_name==='MISSING'?'404':g.code_name==='VIOLATION'?'82':g.code_name==='DRIFT'?'83':'84';
    const why=g.violations&&g.violations.length?g.violations.join(', '):(g.explanation||g.status);
    return `<tr><td class="c${name}"><b>${name}</b></td><td>${g.path}</td><td>${why}</td></tr>`;
  }).join('')||'<tr><td colspan=3 style="color:#8b949e">clean - no gaps</td></tr>';
}
function renderRules(){
  document.getElementById('rules').innerHTML=DATA.rules.map(r=>`<span>${r.match} → ${r.replace}</span> &nbsp;`).join('');
}
async function admin(){
  document.getElementById('admin').classList.add('show');
  TOK=document.getElementById('tok').value;
  const r=await get('/api/achievements');
  document.getElementById('events').textContent=(r.achievements||[]).filter(a=>a.unlocked).map(a=>a.emoji+' '+a.name).join('  ')||'no achievements yet';
}
async function addRule(){
  TOK=document.getElementById('tok').value;
  const m=document.getElementById('m').value, r=document.getElementById('r').value;
  if(!m){return}
  const res=await fetch('/api/rules',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+TOK},
    body:JSON.stringify({action:'add',match:m,replace:r})});
  const j=await res.json();
  document.getElementById('admmsg').textContent=res.ok?('ok, '+j.tokens+' tokens'):j.error;
  if(res.ok){load()}
}
async function logs(){const j=await get('/api/logs');document.getElementById('logs').textContent=(j.logs||[]).join('\n')}
const es=new EventSource('/api/events');
es.onmessage=e=>{
  const box=document.getElementById('events');
  box.innerHTML=JSON.stringify(JSON.parse(e.data))+'<br>'+box.innerHTML.slice(0,4000);
};
load(); logs(); setInterval(load,8000); setInterval(logs,15000);
</script></body></html>"""


def serve(
    repo: str,
    host: str = "127.0.0.1",
    port: int = 8333,
    home: str | None = None,
    admin_token: str | None = None,
    rules_override: str | None = None,
) -> None:
    """Run the dashboard until Ctrl-C."""
    home = home or os.path.join(repo, "..", "100ways-state")
    if not admin_token:
        admin_token = os.getenv("HUNDREDWAYS_ADMIN_TOKEN") or compile_token(DEFAULT_ADMIN_PASS)
    rules_override = rules_override or os.path.join(home, "config", "rules_override.json")
    state = DashboardState()
    handler = type("Handler", (DashboardHandler,), {
        "state": state, "repo": repo, "home": home,
        "admin_token": admin_token, "rules_override": rules_override,
    })
    server = ThreadingHTTPServer((host, port), handler)
    print(f"100Ways dashboard on http://{host}:{port}  (repo {repo})")
    print(f"  admin: password '{DEFAULT_ADMIN_PASS}' or its compiled token "
          f"'{compile_token(DEFAULT_ADMIN_PASS)}'")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
