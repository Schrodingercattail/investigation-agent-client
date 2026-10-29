"""Browser E2E — U00004 combo delivery (chat detail + file export).

Real browser journey: fresh U00004 investigation → combo zh request →
verify chat detail appears once (no duplicate full set) + file brief.
"""
import json, re, sys, time, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from browser import Browser

results = {"checks": {}}

def check(name, ok, detail=""):
    results["checks"][name] = {"ok": bool(ok), "detail": str(detail)[:300]}
    print(("PASS " if ok else "FAIL ") + name + "  " + str(detail)[:110])

def main():
    br = Browser()
    br.start("http://localhost:3001/")
    time.sleep(3)
    # Click "New Investigation"
    br.eval("document.querySelectorAll('button').forEach(b => {"
            " if (b.textContent.includes('New Investigation')) b.click() })")
    time.sleep(2)
    # Type intake in textarea
    ta = br.eval("(() => { const t = document.querySelector('textarea')"
                 " || document.querySelector('input[type=text]');"
                 " if (t) { t.focus(); return 'ok' } return 'none' })()")
    print("textarea:", ta)
    # Use native setter + dispatch event for React
    br.eval("""
(() => {
  const t = document.querySelector('textarea')
         || document.querySelector('input[type=text]');
  if (!t) return;
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype, 'value')?.set
    || Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value')?.set;
  setter.call(t, 'Investigate U00004');
  t.dispatchEvent(new Event('input', {bubbles: true}));
})()""")
    time.sleep(1)
    # Click submit
    br.eval("document.querySelector('form button[type=submit]')?.click()")
    time.sleep(60)  # wait for intake
    # Submit combo ask
    br.eval("""
(() => {
  const t = document.querySelector('textarea')
         || document.querySelector('input[type=text]');
  if (!t) return;
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype, 'value')?.set
    || Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value')?.set;
  setter.call(t, '列出该账号所有提现地址给我。把提现记录生成一份文件。');
  t.dispatchEvent(new Event('input', {bubbles: true}));
})()""")
    time.sleep(1)
    br.eval("document.querySelector('form button[type=submit]')?.click()")
    time.sleep(90)  # wait for response
    # Read chat
    chat = br.eval(
        "(() => { const nodes = document.querySelectorAll("
        "'[class*=message], [class*=prose], [class*=markdown]');"
        " return [...nodes].map(n => n.innerText).join('\\n'); })()")
    check("chat_captured", len(chat or "") > 100, f"len={len(chat or '')}")
    check("no_dup_records", (chat or "").count("完整提现记录") <= 1,
          f"dup_count={chat.count('完整提现记录') if chat else 0}")
    check("has_file_confirm", "文件" in (chat or "") and "生成" in (chat or ""))
    br.screenshot("qa/screenshots/u00004_delivery_brief.png")
    with open("qa/screenshots/u00004_delivery_brief_result.json", "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
