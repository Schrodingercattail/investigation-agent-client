"""QA-01 verification journey: fresh investigation, focus F2, run loop.
Captures the exact goal POST body + final activity. Logs to stdout; run
in background, results in /tmp/qa01_verify.log"""
import sys, time, json
sys.path.insert(0, '../qa')
from browser import Browser

b = Browser(port=9233)
b.start('http://localhost:3001/')
time.sleep(2.5)
b.eval("[...document.querySelectorAll('button')].find(x => x.textContent.trim()==='New Investigation')?.click()")
time.sleep(1)
b.eval('''(() => { const i=document.querySelector('input'); const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set; s.call(i,'Investigate U00299'); i.dispatchEvent(new Event('input',{bubbles:true})); })()''')
b.eval("document.querySelector('form').requestSubmit()")
deadline = time.time() + 90
while time.time() < deadline and not b.eval("document.body.innerText.includes('F2')"):
    time.sleep(0.5)
print("INTAKE-DONE:", b.eval("document.body.innerText.includes('F2')"), flush=True)

clicked_f2 = b.eval("[...document.querySelectorAll('button')].find(e => e.innerText && e.innerText.includes('F2') && e.clientHeight>0 && e.clientHeight<140)?.click()")
print("F2-CLICK:", clicked_f2, flush=True)
time.sleep(1)

b.eval('''(() => { const i=document.querySelector('input'); const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set; s.call(i,'find cross-account opposite trades for this finding'); i.dispatchEvent(new Event('input',{bubbles:true})); })()''')
time.sleep(0.3)
loop = b.eval("[...document.querySelectorAll('button')].find(x => (x.title||'').includes('bounded Agent Loop'))?.click()")
print("RUN-LOOP:", loop, flush=True)

# wait for the goal POST and capture its body
goal = None
deadline = time.time() + 20
while time.time() < deadline and goal is None:
    for r in b.requests:
        if '/goal' in r.get('url', '') and r.get('body'):
            goal = r
    time.sleep(0.3)
print("GOAL-BODY:", goal and goal.get('body'), flush=True)

# find the created task via the investigation API (latest loop task)
inv_match = None
deadline = time.time() + 30
task_id = None
while time.time() < deadline and not task_id:
    try:
        invs = json.loads(b.eval("fetch('/api/v2/investigations').then(r => r.json()).then(j => JSON.stringify(j))", timeout=20) or 'null')
        latest = invs['investigations'][0]
        detail = json.loads(b.eval(f"fetch('/api/v2/investigations/{latest['investigation_id']}').then(r => r.json()).then(j => JSON.stringify(j))", timeout=20) or 'null')
        loops = [t for t in detail['tasks'] if t['type'] == 'agent_loop']
        if loops:
            task_id = loops[-1]['task_id']
            inv_match = latest['investigation_id']
    except Exception as e:
        pass
    time.sleep(0.5)
print("TASK-ID:", task_id, flush=True)

# poll activity to terminal
deadline = time.time() + 240
activity = None
while time.time() < deadline:
    try:
        activity = json.loads(b.eval(f"fetch('/api/v2/tasks/{task_id}/activity').then(r => r.json()).then(j => JSON.stringify(j))", timeout=20), timeout=10)
    except Exception:
        pass
    if activity and activity.get('is_terminal'):
        break
    time.sleep(1)
print("TERMINAL:", activity and (activity['agent_status'], activity['stop_reason']), flush=True)
print("FOCUS:", activity and activity['focused_finding_id'], flush=True)
print("PLAN:", activity and [(s['type'], s['status']) for s in activity['plan_steps']], flush=True)
b.screenshot('../qa/screenshots/25-qa01-verify.png')
b.close()
print("DONE", flush=True)
