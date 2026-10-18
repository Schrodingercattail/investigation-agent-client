# Baseline Integrity Audit — AL-2 production diff vs 4d00ea4 — v1

**Date**: 2026-10-04
**Mode**: READ-ONLY provenance / baseline integrity audit
**Verdict**: **CASE B** — 14 lines belong to AL-2.12–2.14 final behaviour
but were omitted from the `4d00ea4` commit.

---

## 1. Scope / Method

`git diff 4d00ea4 -- backend/app/` + `git log -S` + `git show` + QA
artifact cross-reference. All conclusions tagged [observed] /
[code-traced] / [inferred].

## 2. Baseline integrity check

| File | in 4d00ea4 | diff from 4d00ea4 |
|---|---|---|
| `agent_loop.py` | ✓ | **14 lines** ← ONLY file with changes |
| `risk_platform.py` | ✓ | 0 |
| `context_resolution.py` | ✓ | 0 |
| `investigation_service.py` | ✓ | 0 |
| `skills.py` | ✓ | 0 |
| `config.py` | ✓ | 0 |
| `domain_tools/related_account_analysis.py` | ✓ | 0 |
| `domain_tools/signal_explain.py` | ✓ | 0 |
| `domain_tools/cross_account_trade_analysis.py` | ✓ | 0 |
| `domain_tools/finding_drilldown.py` | ✓ | 0 |
| `domain_tools/policy_lookup.py` | ✓ | 0 |
| `domain_tools/risk_case_fetch.py` | ✓ | 0 |
| `domain_tools/artifact_bundle.py` | ✓ | 0 |
| `investigation_concepts.py` | ✓ | 0 |
| `semantic_resolution.py` | ✓ | 0 |
| `candidate_metadata.py` | ✓ | 0 |
| `finding_lexicon.py` | NOT PRESENT | correctly deleted |

**ONLY `agent_loop.py` has a diff from 4d00ea4** (14 lines). All other
production files match exactly.

## 3. The 14 lines — exact content

### 3a. Observation whitelist (2 lines)
```python
+  # AL-2.14: Feature-type explanation fields (F7-class)
+  "feature_facts": explanation.get("features") or [],
```

### 3b. Feature branch in `_signal_answer_lines` (12 lines)
```python
+  if s.get("feature_facts"):
+      feats = s["feature_facts"]
+      shown = ", ".join(
+          f"{_feature_label(f.get('name', ''))} = {f.get('value', '')}"
+          for f in feats[:5] if isinstance(f, dict) and f.get("name"))
+      more = (f"（共 {len(feats)} 项）" if len(feats) > 5 else "")
+      lines.append("风险信号:行为特征检测发现该账号存在异常行为模式 — "
+                   + (f"观测到 {shown}{more}。" if shown else ""))
+      lines.append("该账号的实际行为特征值是 Risk Platform 标记此风险"
+                   "发现的依据。当前 RP 数据不提供特征归因,因此不能"
+                   "断言某一个具体特征贡献了多少分。")
+      return lines
```

## 4. Provenance

- Created during: AL-2.14 corrective session (「为什么说高频交易是风险项」
  S06 combined scenario exposed the missing Feature-type rendering on
  the loop surface)
- `git log -S 'feature_facts'` → **no commits found** (never committed)
- `git log -S 'feature_signal_payload'` → `4d00ea4` (the signal_explain
  Feature fallback WAS committed, but the loop rendering was not)
- `qa/journey_al214_detail_source.py` (committed in 4d00ea4) runs S06
  and asserts "Trade frequency (24h) = 54" — this REQUIRES the Feature
  branch to be present in the Agent runtime

## 5. Classification: **CASE B**

These 14 lines belong to **AL-2.12–2.14 final production behaviour**
but were accidentally omitted from the `4d00ea4` commit. Evidence:

- `feature_signal_payload` in `signal_explain.py` **was committed** in
  4d00ea4 (the Feature fallback generates the payload)
- AL-2.14 QA journey (committed in 4d00ea4) tests S06 and asserts
  "Trade frequency (24h) = 54"
- AL-2.14 screenshots show the Feature explanation rendering correctly
- Only the loop-surface rendering in `agent_loop.py` was omitted from
  the commit

**NOT CASE A** (not a temporary change — it's genuine production
behavior needed by S06). **NOT CASE C** (not evaluation-only — it
renders user-visible investigation results).

## 6. Recommended action

Commit the 14 lines + the AL-2.14 QA journey/screenshot updates as an
"AL-2 finalization correction" commit. Then redefine the AL-2
production baseline to this new commit. AL-3 baseline evaluation uses
the corrected baseline.
