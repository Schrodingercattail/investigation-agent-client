import json, re, sys, time
sys.path.insert(0,"/Users/vv/investigation-agent-client/qa")
from browser import Browser
from journeys_common import SHOTS, click_run_loop, send_message, type_input, wait_for
Q="给我生成00008这个账户的提现明细和交易明细文件"
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
    return cards.length > """+str(before)+""" && last && last.innerText.includes('ART-');})()""",900,desc="Q3 file request")
    time.sleep(1)
    ans=b.eval("""(()=>{const cards=[...document.querySelectorAll('.flex.justify-start')];
    const last=cards[cards.length-1];return last?last.innerText:'';})()""")
    ids=sorted(set(re.findall(r'ART-[0-9a-f]+',ans)))
    res["artifact_ids_visible"]=ids
    res["checks"]={
      "two_artifact_ids": len(ids)>=2,
      "withdrawal_scope": ("提现" in ans and ("W000" in ans or "提现记录" in ans)),
      "transaction_scope": ("交易" in ans and ("T0" in ans or "交易记录" in ans)),
      "record_counts_stated": bool(re.search(r'\d+\s*(条|records)',ans)),
      "not_failed": "failed" not in ans.lower(),
    }
    res["PASS"]=all(res["checks"].values())
    b.screenshot(f"{SHOTS}/p1-browser-U00008.png")
except Exception as e:
    res["error"]=f"{type(e).__name__}: {e}"[:250]; res["PASS"]=False
finally:
    try: b.close()
    except Exception: pass
    json.dump(res,open("/tmp/p1_browser.json","w"),ensure_ascii=False,indent=1)
    print(json.dumps(res,ensure_ascii=False,indent=1))
