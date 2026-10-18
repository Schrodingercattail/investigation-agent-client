import json, re, sys, time
sys.path.insert(0,"/Users/vv/investigation-agent-client/qa")
from browser import Browser
from journeys_common import SHOTS, click_run_loop, send_message, type_input, wait_for
Q="列出所有关联账号，查询关联账号之间是否存在对冲交易，如果有，把交易明细发我"
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
    cata=re.findall(r'CATA-0\d+',ans)
    res["checks"]={
      "related_list_kept": ("U00043" in ans or "关联账号" in ans or "Related" in ans),
      "existence_verdict_kept": ans.count("候选对冲交易关系")>=2,
      "detail_list_once": len(cata)==len(set(cata)) and len(cata)>0,
      "reference_line": "完整候选明细见" in ans,
      "limits_kept": "受预算限制" in ans or "预算" in ans,
      "hedge_framing": "不构成对交易意图的结论" in ans or "调查线索" in ans,
      "not_failed": "failed" not in ans.lower(),
    }
    res["cata_emitted"]=len(cata); res["distinct"]=len(set(cata))
    res["PASS"]=all(res["checks"].values())
    b.screenshot(f"{SHOTS}/r3-browser-U00008.png")
except Exception as e:
    res["error"]=f"{type(e).__name__}: {e}"[:250]; res["PASS"]=False
finally:
    try: b.close()
    except Exception: pass
    json.dump(res,open("/tmp/r3_browser.json","w"),ensure_ascii=False,indent=1)
    print(json.dumps(res,ensure_ascii=False,indent=1))
