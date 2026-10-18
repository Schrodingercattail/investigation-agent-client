import json, re, sys, time, urllib.request
sys.path.insert(0,"/Users/vv/investigation-agent-client/qa")
from browser import Browser
from journeys_common import SHOTS, click_run_loop, send_message, type_input, wait_for
BASE="http://127.0.0.1:8001/api/v2"
def call(m,p,payload=None,timeout=60):
    r=urllib.request.Request(BASE+p,data=json.dumps(payload).encode() if payload is not None else None,headers={'Content-Type':'application/json'},method=m)
    with urllib.request.urlopen(r,timeout=timeout) as x: return json.loads(x.read())
CASES=[("A","U00008","给我生成00008这个账户的提现明细和交易明细文件"),
       ("B","U00047","列出所有关联账号，查询关联账号之间是否存在对冲交易，如存在，列出对冲交易明细给我，并生成一份交易明细文件")]
res={}
b=Browser()
try:
    b.start("http://localhost:3001/")
    b.eval("""(()=>{try{Object.defineProperty(document,'hidden',{get:()=>false,configurable:true});
    Object.defineProperty(document,'visibilityState',{get:()=>'visible',configurable:true});}catch(e){}})()""")
    for name, case, Q in CASES:
        b.eval("""(()=>{const t=[...document.querySelectorAll('button')].find(x=>x.textContent.includes('New Investigation'));t&&t.click();})()""")
        time.sleep(1.2)
        before=b.eval("document.querySelectorAll('.flex.justify-start').length")
        send_message(b,f"Investigate {case}")
        wait_for(b,"""(()=>{const c=document.querySelectorAll('.flex.justify-start').length;
        return c > """+str(before)+""" && !document.body.innerText.includes('Resolving context, planning, and executing');})()""",300,desc=f"intake {case}")
        before=b.eval("document.querySelectorAll('.flex.justify-start').length")
        type_input(b,Q); click_run_loop(b)
        wait_for(b,"""(()=>{const cards=[...document.querySelectorAll('.flex.justify-start')];
        const last=cards[cards.length-1];
        return cards.length > """+str(before)+""" && last && last.innerText.includes('ART-');})()""",900,desc=f"{name} artifacts")
        time.sleep(1)
        ans=b.eval("""(()=>{const cards=[...document.querySelectorAll('.flex.justify-start')];
        const last=cards[cards.length-1];return last?last.innerText:'';})()""")
        ids=sorted(set(re.findall(r'ART-[0-9a-f]+',ans)))
        cata=re.findall(r'CATA-0\d+',ans)
        res[name]={"task_answer_artifacts":ids,
         "checks":{
           "A_records_no_risk_features": (name!="A") or ("W000" in ans and "Risk features (ML" not in ans),
           "B_candidates_once": (name!="B") or (len(cata)==len(set(cata)) and len(cata)>0),
           "scope_stated": (name=="A" and ("提现" in ans and "交易" in ans)) or (name=="B" and ("集合内部" in ans or "候选" in ans)),
         }}
        res[name]["checks"]["not_failed"]="failed" not in ans.lower()
        res[name]["PASS"]=all(res[name]["checks"].values())
        b.screenshot(f"{SHOTS}/ascope-browser-{name}.png")
except Exception as e:
    res["error"]=f"{type(e).__name__}: {e}"[:250]
finally:
    try: b.close()
    except Exception: pass
    json.dump(res,open("/tmp/ascope_browser.json","w"),ensure_ascii=False,indent=1)
    print(json.dumps({k:v for k,v in res.items() if k!="answer"},ensure_ascii=False,indent=1))
