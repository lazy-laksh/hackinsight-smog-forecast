"""A small local web page for the Insight Agent: python -m smog agent-ui, then open http://127.0.0.1:8000

Built with Python's standard library only. The Groq key stays on this computer (.env); the page
talks only to this local server.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Ask about the smog forecast</title>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,400..800&family=Noto+Nastaliq+Urdu:wght@400;700&display=swap" rel="stylesheet">
<style>
:root{--paper:#e3e8eb;--sheet:#f5f7f8;--ink:#1c2630;--muted:#5b6974;--rule:#c5ced4;--good:#4f9d5d;--bad:#c4423a;--usg:#e0873a;
--sans:"Archivo",system-ui,"Segoe UI",sans-serif;--urdu:"Noto Nastaliq Urdu",serif}
@media (prefers-color-scheme: dark){:root{--paper:#161f27;--sheet:#1d2831;--ink:#e3e9ec;--muted:#97a6b0;--rule:#2f3d48;--good:#5fb06c;--bad:#d9574e}}
*{box-sizing:border-box}html,body{height:100%}
body{margin:0;background:var(--paper);color:var(--ink);font:400 16px/1.55 var(--sans)}
.wrap{max-width:820px;margin:0 auto;padding:0 20px;display:flex;flex-direction:column;min-height:100%}
header{padding:28px 0 14px;border-bottom:2px solid var(--ink)}
h1{font-size:clamp(28px,5vw,44px);line-height:1;margin:0;font-weight:780;font-variation-settings:"wdth" 118}
header p{margin:10px 0 0;max-width:64ch;color:var(--muted)}
#thread{flex:1;padding:8px 0 24px}
.empty{padding:24px 0}.empty h2{font-size:19px;margin:0 0 10px;font-weight:700}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chips button{background:var(--sheet);border:1.5px solid var(--rule);color:var(--ink);padding:8px 14px;border-radius:999px;font:inherit;font-size:15px;cursor:pointer;text-align:left}
.chips button:hover{border-color:var(--ink)}
.chips button[lang=ur]{font-family:var(--urdu);font-size:15px;line-height:1.9}
.turn{padding:18px 0;border-bottom:1px solid var(--rule)}
.q{font-weight:700;font-size:19px;margin:0 0 8px}
.q[dir=rtl],.a[dir=rtl]{font-family:var(--urdu);line-height:2.2}
.a{font-size:18px;max-width:68ch;margin:0}
.a sup a{display:inline-block;min-width:20px;padding:0 5px;margin-left:2px;border-radius:5px;background:var(--ink);color:var(--paper);
  font:700 12px/18px var(--sans);text-align:center;text-decoration:none;vertical-align:2px}
.meta{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;margin-top:10px;font-size:13px;color:var(--muted)}
.badge{padding:2px 10px;border-radius:999px;font-weight:650;border:1.5px solid currentColor}
.badge.ok{color:var(--good)}.badge.bad{color:var(--bad)}.badge.idk{color:var(--muted)}
.sources{list-style:none;margin:14px 0 0;padding:0;display:grid;gap:8px}
.sources li{display:grid;grid-template-columns:28px 1fr;gap:10px;background:var(--sheet);border-radius:8px;padding:10px 12px;font-size:14px}
.sources .n{font-weight:800;font-size:15px}
.sources .kind{font-weight:650}.sources .ttl{color:var(--muted)}
.sources .txt{margin-top:2px;color:var(--muted)}
.sources a{color:inherit}
.thinking{color:var(--muted);font-size:17px}
.thinking::after{content:"";display:inline-block;width:1.2em;text-align:left;animation:dots 1.4s steps(4) infinite}
@keyframes dots{0%{content:""}25%{content:"."}50%{content:".."}75%{content:"..."}}
@media (prefers-reduced-motion:reduce){.thinking::after{animation:none;content:"..."}}
.followups{margin-top:12px}.followups .chips button{font-size:14px;padding:5px 12px}
form{position:sticky;bottom:0;background:var(--paper);padding:12px 0 18px;border-top:2px solid var(--ink);display:flex;gap:8px}
input{flex:1;padding:13px 14px;border-radius:8px;border:1.5px solid var(--ink);background:var(--sheet);color:var(--ink);font:inherit;font-size:17px}
input:focus-visible,button:focus-visible{outline:3px solid var(--usg);outline-offset:2px}
form button{padding:12px 22px;border-radius:8px;border:0;background:var(--ink);color:var(--paper);font:inherit;font-weight:700;cursor:pointer}
form button:disabled{opacity:.5;cursor:default}
</style></head><body><div class="wrap">
<header><h1>Ask about the smog forecast</h1>
<p>Answers come only from cited sources: tomorrow's forecasts and recent measurements, and official documents from
WHO, US EPA, the Government of Pakistan and the Government of Punjab. Every citation and number is checked before you
see it. If the sources do not answer, it says so. English or Urdu.</p></header>
<section id="thread" aria-live="polite">
  <div class="empty" id="empty"><h2>Try a question</h2><div class="chips" id="starters"></div></div>
</section>
<form id="f"><input id="q" placeholder="Ask about tomorrow's smog, the data or the rules" autocomplete="off" aria-label="Your question">
<button id="ask">Ask</button></form>
</div>
<script>
const STARTERS=["Should Lahore issue a smog advisory tomorrow?","Why is the forecast for Peshawar high?",
"Which city will have the worst air tomorrow?","How accurate is the model?","What is Pakistan's 24-hour PM2.5 standard?",
"کل لاہور میں پی ایم 2.5 کتنا ہو گا؟","What is the forecast for Multan?"];
const FOLLOW={pred:["Is the data fresh right now?"],cityday:["What is tomorrow's forecast?"],
report:["Is the model better than persistence in Karachi?"],doc:["What did Punjab tell schools to do about smog?","Who should avoid heavy exertion on an unhealthy day?"]};
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const isUrdu=s=>/[\\u0600-\\u06FF]/.test(s);
function kind(id){
  if(id.startsWith("row:pred|"))return "Tomorrow's forecast (gold row)";
  if(id.startsWith("row:cityday|"))return "Measured day (gold row)";
  if(id.startsWith("row:freshness|")||id.startsWith("row:pipeline"))return "Pipeline status";
  if(id.startsWith("report:"))return "Model test report";
  if(id.startsWith("doc:project-methods")||id.startsWith("doc:model-card"))return "Project method";
  return "Official source";
}
function chipRow(list,cls){return `<div class="chips ${cls||""}">${list.map(t=>`<button type="button"${isUrdu(t)?' lang="ur" dir="rtl"':""}>${esc(t)}</button>`).join("")}</div>`;}
function wire(el){el.querySelectorAll(".chips button").forEach(b=>b.onclick=()=>{document.getElementById("q").value=b.textContent;ask();});}
document.getElementById("starters").outerHTML=chipRow(STARTERS);wire(document.getElementById("empty"));
document.getElementById("f").onsubmit=e=>{e.preventDefault();ask();};
function render(a,q){
  // Turn [id] citations into numbered markers that point to the source list below the answer.
  const order=[];let text=esc(a.answer);
  text=text.replace(/\\[((?:doc|row|report):[^\\]]+)\\]/g,(m,id)=>{let n=order.indexOf(id);if(n<0){order.push(id);n=order.length-1;}
    return `<sup><a href="#s-${a._k}-${n+1}" aria-label="source ${n+1}">${n+1}</a></sup>`;});
  const byId=Object.fromEntries(a.citations.map(c=>[c.id,c]));
  const src=order.filter(id=>byId[id]).map((id,i)=>{const c=byId[id];return `<li id="s-${a._k}-${i+1}"><span class="n">${i+1}</span><div>
    <span class="kind">${esc(kind(id))}</span> <span class="ttl">${esc(c.title)}${c.url?` · <a href="${esc(c.url)}" target="_blank" rel="noopener">open</a>`:""}</span>
    <div class="txt">${esc(c.text.slice(0,260))}${c.text.length>260?"…":""}</div></div></li>`;}).join("");
  const badge=!a.verified?`<span class="badge bad">The model's answer failed our check, so it says "I don't know"</span>`
    :a.idk?`<span class="badge idk">No source answers this</span>`:`<span class="badge ok">Checked: every citation and number matches its source</span>`;
  const types=[...new Set(order.map(id=>id.startsWith("row:pred|")?"pred":id.startsWith("row:cityday|")?"cityday":id.startsWith("report:")?"report":"doc"))];
  const NAMES={lahore:"Lahore",islamabad_rawalpindi:"Islamabad/Rawalpindi",peshawar:"Peshawar",karachi:"Karachi"};
  const predId=order.find(id=>id.startsWith("row:pred|")),city=predId?NAMES[predId.split("|")[1]]:null;
  const cityFollow=city?[`Why is ${city}'s forecast what it is?`,`What was ${city}'s PM2.5 over the last few days?`]:[];
  const follow=a.language==="ur"?[]:[...new Set([...cityFollow,...types.flatMap(t=>FOLLOW[t]||[])])].filter(t=>t!==q).slice(0,3);
  return `<p class="a"${a.language==="ur"?' dir="rtl"':""}>${text}</p>
    <div class="meta">${badge}<span>${a.seconds}s</span>${a.model?`<span>model ${esc(a.model)}</span>`:""}</div>
    ${src?`<ol class="sources">${src}</ol>`:""}
    ${follow.length?`<div class="followups">${chipRow(follow)}</div>`:""}`;
}
let k=0;
async function ask(){
  const input=document.getElementById("q"),q=input.value.trim();if(!q)return;
  const btn=document.getElementById("ask");btn.disabled=true;input.value="";
  const empty=document.getElementById("empty");if(empty)empty.remove();
  const turn=document.createElement("article");turn.className="turn";
  turn.innerHTML=`<p class="q"${isUrdu(q)?' dir="rtl"':""}>${esc(q)}</p><p class="thinking">Searching the sources and checking every number</p>`;
  document.getElementById("thread").append(turn);turn.scrollIntoView({behavior:"smooth",block:"start"});
  try{
    const r=await fetch("/ask",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({question:q})});
    const a=await r.json();if(a.error)throw new Error(a.error);a._k=++k;
    turn.innerHTML=`<p class="q"${isUrdu(q)?' dir="rtl"':""}>${esc(q)}</p>`+render(a,q);wire(turn);
  }catch(err){turn.querySelector(".thinking").outerHTML=`<p class="a">Could not get an answer: ${esc(err.message)}. Check that GROQ_API_KEY is in .env and try again in a minute.</p>`;}
  btn.disabled=false;input.focus();
}
</script></body></html>"""


def serve(port: int = 8000, host: str = "127.0.0.1") -> None:
    """host 127.0.0.1 = only this computer; 0.0.0.0 = reachable from outside (used on Render)."""
    import os
    import time
    from collections import defaultdict, deque
    from smog.agent import InsightAgent
    port = int(os.getenv("PORT", port))                 # hosting services tell the app which port to use
    recent: dict[str, deque] = defaultdict(deque)       # simple protection for a public, free API key
    LIMIT, WINDOW = 8, 60.0                              # at most 8 questions per minute per visitor
    print("loading sources (documents + latest published gold rows)...")
    agent = InsightAgent()
    print(f"{len(agent.docs)} document chunks, {len(agent.facts)} data facts loaded")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, kind: str) -> None:
            self.send_response(code)
            self.send_header("Access-Control-Allow-Origin", "*")     # the dashboard (another site) may call /ask
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):                 # browser pre-flight check before a cross-site POST
            self._send(204, b"", "text/plain")

        def do_GET(self):
            if self.path.startswith("/health"):
                return self._send(200, json.dumps({"ok": True, "facts": len(agent.facts)}).encode(), "application/json")
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")

        def do_POST(self):
            if self.path != "/ask":
                return self._send(404, b"{}", "application/json")
            who = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
            q = recent[who]
            while q and time.time() - q[0] > WINDOW:
                q.popleft()
            if len(q) >= LIMIT:
                return self._send(429, json.dumps({"error": "Too many questions in a minute; please wait a little."}).encode(),
                                  "application/json")
            q.append(time.time())
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

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Insight Agent running at http://{'127.0.0.1' if host == '127.0.0.1' else host}:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":       # used by the hosting service: python -m smog.agent_ui --host 0.0.0.0
    import argparse
    ap = argparse.ArgumentParser(description="Insight Agent web page and /ask API")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    serve(a.port, a.host)
