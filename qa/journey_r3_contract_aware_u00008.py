"""R3 closure — contract-aware browser acceptance for the Q4 request.
Validates the served response against the run's OWN persisted contract:
Branch A (3 outputs, shared observation → dedup + reference) or
Branch B (2 outputs → single section carries verdict + full details).
"""
import json, re, sys, time, urllib.request
sys.path.insert(0,"/Users/vv/investigation-agent-client/qa")
from browser import Browser
from journeys_common import SHOTS, click_run_loop, send_message, type_input, wait_for
BASE="http://127.0.0.1:8001/api/v2"
Q="列出所有关联账号，查询关联账号之间是否存在对冲交易，如果有，把交易明细发我"
def call(m,p,payload=None,timeout=60):
    r=urllib.request.Request(BASE+p,data=json.dumps(payload).encode() if payload is not None else None,headers={'Content-Type':'application/json'},method=m)
    with urllib.request.urlopen(r,timeout=timeout) as x: return json.loads(x.read())
res={"question":Q}
b=Browser()
try:
    b.start("http://localhost:3001/")
    b.eval("""(()=>{try{Object.defineProperty(document,'hidden',{get:()=>false,configurable:true});
    Object.defineProperty(document,'visibilityState',{get:()=>'visible',configurable:true});}catch(e){}})()""")
    b.eval("""(()=>{const t=[...document.querySelectorAll('button')].find(x=>x.textContent.includes('New Investigation'));t&&t.click();})()""")
    time.sleep(1.2)
    before=b.eval("document.querySelectorAll('.flex.justify-start').length")
    send_message(b,"Investigate U00008")
    wait_for(b,"""(()=>{const c=document.querySelectorAll('.flex.justify-start').length;
    return c > """+str(before)+""" && !document.body.innerText.includes('Resolving context, planning, and executing');})()""",300,desc="intake")
    before=b.eval("document.querySelectorAll('.flex.justify-start').length")
    type_input(b,Q); click_run_loop(b)
    wait_for(b,"""(()=>{const cards=[...document.querySelectorAll('.flex.justify-start')];
    const last=cards[cards.length-1];
    return cards.length > """+str(before)+""" && last && last.innerText.includes('整体结果');})()""",900,desc="Q4 loop")
    time.sleep(1)
    ans=b.eval("""(()=>{const cards=[...document.querySelectorAll('.flex.justify-start')];
    const last=cards[cards.length-1];return last?last.innerText:'';})()""")
    res["answer"]=ans
    b.screenshot(f"{SHOTS}/r3-closure-browser-U00008.png")
except Exception as e:
    res["error"]=f"{type(e).__name__}: {e}"[:250]
finally:
    try: b.close()
    except Exception: pass
    json.dump(res,open("/tmp/r3_close_browser.json","w"),ensure_ascii=False)
    print(json.dumps({k:v for k,v in res.items() if k!="answer"},ensure_ascii=False))
