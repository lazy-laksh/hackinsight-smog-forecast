"""A small local web page for the Insight Agent: python -m smog agent-ui, then open http://127.0.0.1:8000

Built with Python's standard library only. The Groq key stays on this computer (.env); the page
talks only to this local server.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Smog Insight Agent</title>
<style>
:root{--bg:#f8fafc;--card:#fff;--ink:#0f172a;--muted:#64748b;--line:#e2e8f0;--accent:#0e7490}
@media (prefers-color-scheme: dark){:root{--bg:#0b1120;--card:#111827;--ink:#e5e7eb;--muted:#94a3b8;--line:#1f2937;--accent:#22d3ee}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,"Segoe UI",sans-serif}
main{max-width:860px;margin:0 auto;padding:24px 16px 60px}h1{font-size:24px;margin:0 0 4px}
.muted{color:var(--muted);font-size:14px}form{display:flex;gap:8px;margin:16px 0}
input{flex:1;padding:12px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--ink);font:inherit}
button{padding:12px 18px;border-radius:10px;border:0;background:var(--accent);color:#fff;font-weight:700;cursor:pointer}
.qa{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px;margin:12px 0}
.q{font-weight:700;margin-bottom:6px}.a{white-space:pre-wrap}.src{font-size:13px;margin-top:10px}
.src li{margin:4px 0}.tag{font-size:12px;padding:2px 8px;border-radius:999px;margin-left:6px}
.ok{background:#dcfce7;color:#14532d}.bad{background:#fee2e2;color:#7f1d1d}
.ex button{background:none;border:1px solid var(--line);color:var(--ink);font-weight:400;padding:6px 10px;margin:4px 4px 0 0}
</style></head><body><main>
<h1>Smog Insight Agent</h1>
<p class="muted">Answers only from cited sources: tomorrow's forecasts and recent measurements (gold rows) and official
documents (WHO, US EPA, Pakistan NEQS, Punjab government). If the sources do not answer the question, it says so.
Ask in English or Urdu.</p>
<div class="ex" id="ex"></div>
<form id="f"><input id="q" placeholder="e.g. Should Lahore issue a smog advisory tomorrow?" autocomplete="off">
<button>Ask</button></form><div id="out"></div>
<script>
const examples=["What is tomorrow's PM2.5 forecast for Lahore?","Why is the forecast for Peshawar high?",
"What is the WHO 24-hour guideline for PM2.5?","How accurate is the model?","کل لاہور میں پی ایم 2.5 کتنا ہو گا؟",
"What is the forecast for Multan?"];
const esc=s=>String(s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
document.getElementById("ex").innerHTML=examples.map(e=>`<button type="button">${esc(e)}</button>`).join("");
document.querySelectorAll("#ex button").forEach(b=>b.onclick=()=>{document.getElementById("q").value=b.textContent;ask();});
document.getElementById("f").onsubmit=e=>{e.preventDefault();ask();};
async function ask(){
  const q=document.getElementById("q").value.trim(); if(!q) return;
  const box=document.createElement("div"); box.className="qa";
  box.innerHTML=`<div class="q">${esc(q)}</div><div class="a muted">Thinking…</div>`;
  document.getElementById("out").prepend(box);
  try{
    const r=await fetch("/ask",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({question:q})});
    const a=await r.json(); if(a.error) throw new Error(a.error);
    const rtl=a.language==="ur"?' dir="rtl"':"";
    const src=a.citations.map(c=>`<li><b>${esc(c.id)}</b> ${esc(c.title)} ${c.url?`<a href="${esc(c.url)}" target="_blank" rel="noopener">link</a>`:""}<br><span class="muted">${esc(c.text.slice(0,240))}</span></li>`).join("");
    box.innerHTML=`<div class="q">${esc(q)}</div><div class="a"${rtl}>${esc(a.answer)}</div>
      <div class="src"><span class="tag ${a.verified?"ok":"bad"}">${!a.verified?"the model's answer failed the check, so it was replaced by I don't know":a.idk?"no source answers this, so it says I don't know":"checked: every citation and number matches the sources"}</span>
      <span class="muted"> ${a.seconds}s · ${esc(a.model||"")}</span>${src?`<ol>${src}</ol>`:""}</div>`;
  }catch(err){ box.querySelector(".a").textContent="Error: "+err.message; }
}
</script></main></body></html>"""


def serve(port: int = 8000) -> None:
    from smog.agent import InsightAgent
    print("loading sources (documents + latest published gold rows)...")
    agent = InsightAgent()
    print(f"{len(agent.docs)} document chunks, {len(agent.facts)} data facts loaded")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, kind: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")

        def do_POST(self):
            if self.path != "/ask":
                return self._send(404, b"{}", "application/json")
            try:
                length = int(self.headers.get("Content-Length", 0))
                question = json.loads(self.rfile.read(length) or b"{}").get("question", "").strip()[:500]
                if question:
                    from smog.agent import is_idk
                    result = agent.ask(question)
                    body = {**asdict(result), "idk": is_idk(result.answer)}
                else:
                    body = {"error": "empty question"}
            except Exception as error:      # show the problem in the page instead of a broken connection
                body = {"error": str(error)}
            self._send(200, json.dumps(body, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def log_message(self, *args):     # keep the terminal quiet; answers are logged by the agent
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Insight Agent running at http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
