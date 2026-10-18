import json, sys, time
sys.path.insert(0,"/Users/vv/investigation-agent-client/qa")
from browser import Browser
from journeys_common import SHOTS, click_run_loop, send_message, type_input, wait_for
Q="高频提现的判定阈值是多少？提现到新地址是全部提现到同一地址还是不同新地址？整理提现明细给我"
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
    return cards.length > """+str(before)+""" && last && (last.innerText.includes('整体结果'));})()""",900,desc="Q2 loop")
    time.sleep(1)
    ans=b.eval("""(()=>{const cards=[...document.querySelectorAll('.flex.justify-start')];
    const last=cards[cards.length-1];return last?last.innerText:'';})()""")
    res["answer"]=ans[:1500]
    res["checks"]={
      "f2_threshold": "withdrawal_frequency_24h" in ans,
      "destination": ("同一" in ans or "不同" in ans),
      "details": ("提现明细" in ans or "W0" in ans),
      "f3_kept": "F3" in ans or "First withdrawal" in ans or "首次提现" in ans,
      "disclosure_if_degraded": (True if "未能" not in ans and "服务" not in ans else ("无法确认" in ans or "未能成功" in ans or "解析" in ans)),
      "no_unfounded_service_claim": "服务持续不可用" not in ans and "服务暂时不可用" not in ans,
      "no_fake_unfinished": not ("部分项目未能完整回答" in ans and "已识别的交付项均已回答" in ans),
      "not_failed": "failed" not in ans.lower(),
    }
    res["PASS"]=all(res["checks"].values())
    b.screenshot(f"{SHOTS}/q2c-browser-U00008.png")
except Exception as e:
    res["error"]=f"{type(e).__name__}: {e}"[:250]; res["PASS"]=False
finally:
    try: b.close()
    except Exception: pass
    json.dump(res,open("/tmp/q2c_browser.json","w"),ensure_ascii=False,indent=1)
    print(json.dumps({k:v for k,v in res.items() if k!="answer"},ensure_ascii=False,indent=1))
    print("HEAD:",res.get("answer","")[:500])
