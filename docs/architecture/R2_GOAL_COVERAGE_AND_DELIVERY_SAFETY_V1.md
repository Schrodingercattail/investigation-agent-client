# R2 — Goal Coverage and Delivery Safety V1

- **Baseline: R2.2.3**（唯一权威设计版本，取代 R2.2.2/R2.2.1/R2.2 及更早草案）
- **Design status: FROZEN**（逻辑、条件、终态、优先级以本文件为准；变更需用户审批）
- **Implementation status: IN PROGRESS**（Phase 1 VERIFIED；Phase 2
  VERIFIED，见 Tracking；Phase 3 = WP-2 Version B：设计 FROZEN，实施
  进行中——S1 ACCEPTED、S2 COMPLETED(条件接受)、S3/S4 ACCEPTED —
  CLOSED、S5 ACCEPTED — CLOSED（带已接受限制）、S6 ACCEPTED — CLOSED
  （含延期项）、S7 验收归档 ACCEPTED — CLOSED；整体验证 NOT COMPLETE
  /整体验收 NOT ACCEPTED，详见 Tracking 与 kickoff §13.11）
- 本文档 = 设计基线 + 各阶段实施状态与验证证据的持续维护记录。
- 上游审计链：U00010 复合请求失败审计（TASK-0af949e1dd18 / ART-49f01b8157）→
  GU Fallback / LLM 可靠性 / 降级安全设计审计 → R2.2.1/R2.2.2/R2.2.3 局部校准。

---

## 1. 两项最终局部逻辑校准（R2.2.3）

### 1.1 accepted contract + proposal_source 缺失的生成前阻断

判定字段与持久化位置（全部在规划/执行前写入 state，VERIFIED）：

| 字段 | 位置 | 语义 |
|---|---|---|
| `state.goal_contract` | agent_loop.py:2078（GU/决策流内赋值） | None=无被接受契约；对象=被接受契约 |
| `state.goal_contract_decision.outcome` | :2120（取值 `"contract_accepted"`，:994 枚举）、:2143 持久化 | `"contract_accepted"` ⇔ :2078 同流赋值契约——accepted 与未接受可可靠区分 |
| `state.goal_representation.proposal_source` | goal_resolution.py:311/:2042/:2051；representation 经 agent_loop.py:2065 `state.goal_representation = produced.project()` 持久化 | `"llm"`=模型自有 outputs 构成最终提案；`"heuristic"`=确定性候选替换（"never a confirmed review"）；None/缺失=无提案 |

新增独立 BLOCK 条件（UNKNOWN 路径，防御性兜底）：

```
accepted_contract = (state.goal_contract is not None
                     ∧ state.goal_contract_decision.outcome == "contract_accepted")
BLOCK-UNKNOWN：accepted_contract
               ∧ state.goal_representation.proposal_source in (None, "")
               ∧ intent=ARTIFACT_*
               ∧ 下一待执行步 type=generate_artifact
  → BLOCK（生成前）→ goal_outcome=PARTIAL + stop_reason=CLARIFICATION_NEEDED
```

兼容性注记（2026-10-30 实施期修正）：原注记"新运行中该组合不应产生"已被
真实栈证据推翻——GU LLM 成功路径（如 COMPOUND 分支）可产出 accepted
contract 且 proposal_source 缺失（live 证据：TASK-0f2ade913a55，
docs/evaluations/g7-live-replay-u00010-20261030T040443Z.json）。该分支为
**真实生产路径**，G7''-U 的生成前阻断因此为必要防护而非纯防御；冻结规则
本身不变。

### 1.2 BLOCK 终态按原因区分 + 判定唯一性

- proposal miss 与 heuristic_single_goal 为**同一形态**（提案未命中必然记录为
  `outcome="heuristic_single_goal"`），合并为 None-B，不重复计数。
- 多类别匹配信号仅在**无契约分支**作为 none 的识别信号；契约被接受后边界已由
  契约定义，多匹配不改变 coverage 归类。
- none 与 candidate 判定有序：**先判 accepted contract，后判回退形态**——同一
  运行路径不可能同时归入 none 与 candidate。

分因终态：

| 阻断原因 | goal_outcome | stop_reason |
|---|---|---|
| None-B（heuristic_single_goal+ARTIFACT） | PARTIAL | CLARIFICATION_NEEDED |
| UNKNOWN（accepted+proposal_source 缺失） | PARTIAL | CLARIFICATION_NEEDED |
| None-C（UNRESOLVED/多类别冲突） | UNANSWERABLE/BLOCKED | CLARIFICATION_NEEDED |

三者共享 `LoopDecision.BLOCK` 生成前机制与终态接线，goal_outcome 按原因取值
（全部既有枚举）。

## 2. 唯一 coverage 分类与优先级规则

```
coverage(state) —— 判定顺序固定，先命中先返回；同一路径恰归一类：
  1. state.goal_contract is not None：                      # 被接受契约
       outcome=="contract_accepted"：
         proposal_source=="llm"       → reviewed
         proposal_source=="heuristic" → candidate
         proposal_source None/缺失    → UNKNOWN（禁止交付生成）
       outcome 为其他（invalid_proposal 等契约态） → none（契约未被接受）
  2. state.goal_contract is None：                          # 无被接受契约
       → none
       子形态（仅决定终态措辞，不改变 none）:
         B:  outcome=="heuristic_single_goal" ∧ intent=ARTIFACT_*
         C:  goal_resolution=="UNRESOLVED" ∨ request_text 匹配类别数 ≥2
         D:  intent 非 ARTIFACT（只读问答继续，不阻断）
```

多匹配信号仅在第 2 层消费；第 1 层内多匹配不降级（契约边界已定义）。
coverage 是**证据强度分级，不是覆盖证明**；request_completeness 当前恒为
UNKNOWN（系统不存在独立完整性证明机制，见 §7 限制）。

## 3. 完整生成前 BLOCK 条件及对应终态

| # | 条件（全部执行前可判定） | goal_outcome | stop_reason |
|---|---|---|---|
| G7''-B | contract=None ∧ outcome=heuristic_single_goal ∧ intent=ARTIFACT_* ∧ next=generate_artifact | PARTIAL | CLARIFICATION_NEEDED |
| G7''-C | contract=None ∧（goal_resolution=UNRESOLVED ∨ request_text 匹配类别数 ≥2）∧ intent=ARTIFACT_* ∧ next=generate_artifact | UNANSWERABLE/BLOCKED | CLARIFICATION_NEEDED |
| G7''-U | **accepted_contract** ∧ proposal_source None/缺失 ∧ intent=ARTIFACT_* ∧ next=generate_artifact | PARTIAL | CLARIFICATION_NEEDED |

机制共享（`LoopDecision.BLOCK` 语义的生成前阻断 + 终态接线），criteria 侧
`delivery_evidence_of` 保留为兜底，不承担生成前阻断。

## 4. 表 A：目标覆盖与完整性判定

| 既有证据状态 | 目标结构可靠 | 完整性充分证据 | 只读调查 | 生成交付 | criteria_satisfied | goal_outcome | stop_reason | 用户可见 |
|---|---|---|---|---|---|---|---|---|
| source=llm, proposal_source=llm, contract_accepted（reviewed） | ✓ | **审查级（非证明）** | ✓ | ✓（契约边界） | 全满足∧交付 verified→True；否则 False | **PARTIAL 上限**（completeness UNKNOWN） | goal_satisfied / NO_VIABLE_ACTION | 已识别目标完成+完整性未证披露 |
| source=heuristic 或 proposal_source=heuristic, contract_accepted（candidate） | ✓（边界内） | **否** | ✓ | ✓（仅声明方面边界） | 全满足∧交付 verified→True | **PARTIAL（上限）** | goal_satisfied | 按已确认范围交付+边界列明 |
| contract=None, 单类别 heuristic 命中 | ✗ | **否** | ✓ | **✗（G7''-B 生成前阻断）** | False | PARTIAL | CLARIFICATION_NEEDED | 已确认部分+澄清 |
| contract=None, 多类别冲突/UNRESOLVED | ✗ | **否** | ✓ | ✗（G7''-C） | False | UNANSWERABLE/BLOCKED | CLARIFICATION_NEEDED | 澄清框架 |
| contract=None, question 意图（无交付语义） | ✗ | 否（只读回答可自证） | ✓ | n/a | 按现行 question_answered | 现行 | 现行 | 现行问答+完整性未证框架句 |
| accepted contract ∧ proposal_source 缺失（UNKNOWN） | ✓ | **否** | ✓ | **✗（G7''-U 生成前阻断）** | False（交付语义） | PARTIAL | CLARIFICATION_NEEDED | 澄清+范围确认 |

**COMPLETE 必要合取（保留限制）**：coverage=reviewed ∧ 全输出 SATISFIED ∧
delivery verified ∧ **request_completeness=proven**。最后一项当前系统不存在
（UNKNOWN），故 **COMPLETE 当前不可达**——该限制为设计显式决定。

## 5. 表 B：交付证据判定（delivery verdict 与整体判定分离）

| 场景 | declared | actual | source_bound | parity | delivery verdict | 整体 criteria_satisfied |
|---|---|---|---|---|---|---|
| 合法 generic summary 导出（coverage≥candidate） | generic | FINDINGS_SUMMARY/NOTES | n/a | 不适用 | verified | True |
| generic 声明但 coverage=none | generic | 任意 | — | — | —（交付不生成） | False（覆盖不足） |
| 声明 records，实际 findings_summary | records | FINDINGS_SUMMARY | — | — | **unverified** | False |
| records 来源未绑定 | records | 任意 | ✗ | — | **unverified** | False |
| parity=false/缺失（records 类） | records | 任意 | ✓ | ✗/缺失 | **unverified** | False |
| 交付 verified 但原始请求完整性未知 | 按计划 | 匹配 | ✓ | ✓(records) | verified | True ∧ goal_outcome=PARTIAL |

## 6. 表 C：执行权限与完成权限矩阵

| coverage | 只读调查 | 交付规划 | 交付生成 | 最终完成判定 |
|---|---|---|---|---|
| reviewed | 允许 | 允许 | 允许（边界内） | 条件允许：全条件满足→PARTIAL 上限（COMPLETE 需完整性证明，当前不可达）；否则 PARTIAL |
| candidate | 允许 | 允许（仅声明方面） | 允许（仅声明边界） | **禁止 COMPLETE**：上限 PARTIAL |
| none(unknown) | 允许 | **禁止** | **禁止（G7'' 生成前阻断）** | **禁止**：clarify/PARTIAL/UNANSWERABLE |

## 7. 表 D：终态一致性（标注 IMPLEMENTATION GAP）

| 情形 | criteria_satisfied | goal_outcome | stop_reason | 保留局部交付 | 用户可见框架 |
|---|---|---|---|---|---|
| 完整性充分+全满足+交付通过 | True | PARTIAL（上限） | goal_satisfied | — | 已识别目标完成+完整性未证披露 |
| 交付验证失败 | False | PARTIAL | NO_VIABLE_ACTION | 是（已验证部分） | 部分交付+缺口 |
| 仅部分目标满足（契约内） | False | PARTIAL | NO_VIABLE_ACTION | 是 | 已答/未答列明 |
| 目标完整性未知（U00010 形态） | False（G7'' 恒 False） | PARTIAL | CLARIFICATION_NEEDED | **不生成交付** | 已确认部分+澄清 |
| 目标结构无法可靠识别 | False | UNANSWERABLE/BLOCKED | CLARIFICATION_NEEDED | ✗ | 澄清框架 |
| 有效局部交付+整体完整性未知（candidate 上限） | True | PARTIAL | goal_satisfied | 是 | 部分回答+边界列明 |
| 无交付且无法安全调查 | False | BLOCKED | CLARIFICATION_NEEDED | ✗ | 阻断+原因 |

IMPLEMENTATION GAP（阶段内接线项）：G1 contract=None 下 goal_outcome=PARTIAL
推导；G2 Planner 消费 decision.outcome→clarify；G3 composer coverage 感知
渲染；G4 coverage/delivery 纯函数；G5 交付意图 heuristic_single_goal 事后
clarify 接线；G6 derive 结果 coverage 封顶（共享推导点，:7364 与 :4429-4438
两赋值点）；G7'' 生成前 BLOCK（本阶段）。

## 8. 有界重试（Phase 2，冻结设计）

GU 应用层为唯一预算所有者：`generate()` 循环 (1,2)（llm_provider.py:131）新增
connection-class 重试一次；GU 保持 `with_options(timeout=240, max_retries=0)`
（:369-379）——物理请求 ≤2，最坏 ≤2×240s，无双层叠加。流式中途断连
（incomplete chunked read）发生在首字节后，SDK 重试不覆盖（UNKNOWN：待故障
注入实测钉住异常类型与边界）。`local_error` 新分类保留真实异常类型名，不伪装
service_unavailable（历史实证 10-17 三例 NameError 被误标）。429：GU 现状零
重试零观测，不另加退避。Planner 维持共享客户端 60s/SDK 默认 2 次重试不动。

## 9. 历史失败逐名清单（回归基线，只对比不清理）

`agent_loop_v1` test_5 / test_6（RE_PLAN planner 路由漂移，a52cb89 挂账）、
`agent_loop_v1` test_10_and_15（循环上界，首次观察未挂账）、
`signal_explain` ML evidence_missing（10-27/10-28 记录）、`display_cap`×6、
`compound_triage::r4_dual_stream`（10-27 记录）、
**10. `u00047_no_progress::test_replan_repeat_converges_with_verified_delivery`**
（2026-10-30 转入：completion-dominance 与场景脚本冲突，反事实验证非
Phase 1/G7 因果，见 Known limitations 2——待审批后重写场景或断言）。
每阶段 E2E 后逐名核对；任何清单外新增失败 → 停止并分类。

## 10. UNKNOWN（保留项）

① mid-stream 异常真实类型与分类实现（→Phase 2 故障注入钉住）；② SDK 流式
重试边界；③ ArtifactTypeV2 两值枚举对 records 的表达限制；④ PARTIAL 前端
映射现行为；⑤ clarify 的用户行为影响无数据；⑥ SDK 重试关闭的历史动机。

---

# Implementation Tracking

**Overall implementation status: IN PROGRESS**
（Phase 1: IN PROGRESS · Phase 2: NOT STARTED · Phase 3: Version B DESIGN FROZEN，未实施）

状态语义：NOT STARTED（未开始）/ IN PROGRESS（进行中）/ IMPLEMENTED（代码
已实现）/ VERIFIED（按 DoD 完成验证）/ BLOCKED（存在阻碍）。单元测试通过
不得直接标记 VERIFIED。

## Phase 1 — Agent Loop Delivery Safety Gate

- **Scope**：`backend/app/agent_loop.py` 内的 G7''-B/C/U 生成前交付安全阻断
  （coverage/delivery 纯函数、loop 级生成前拦截、终态接线、criteria 侧
  delivery 兜底）及对应测试与验收证据。
- **Implementation status**: **VERIFIED**（2026-10-30：G7'' 三分支 DoD 全部
  满足——单元/组件/API E2E/live replay/browser 证据齐全；8.1 阻塞解除
  3/4，剩余 1 项经反事实验证为既有失败、非 Phase 1 验收项（已转入历史
  失败清单第 10 项）。该转类判定属本轮审计结论，如审批不认可，Phase 1
  回退为 IMPLEMENTED。）
- **Changed files**:
  - `backend/app/agent_loop.py`：新增 `_artifact_delivery_intent` /
    `_goal_coverage_of` / `_delivery_evidence_of` / `_heuristic_category_count`
    / `_delivery_gate_block` / `_gate_clarification_response` 纯函数与渲染；
    `_run_loop` 步执行前 G7'' 门（生成前 BLOCK + 终态接线，先置
    agent_status/stop_reason 再 compose）；`criteria_satisfied` 交付兜底
    （contract=None + 交付意图 + coverage none/unknown → False）；
    `compose_loop_response` G7'' 澄清分支（置于 GSP 分支之前）；完成语
    "已完成/部分完成"按 outcome 值渲染（既有机制）
  - `backend/tests/test_delivery_gate_g7_v1.py`（新增 12 用例：三分支单元 +
    非阻断回归 + loop 级集成，经 AGENT_LOOP_SCRIPTED_SESSION_FILE 缝）
  - `backend/tests/test_delivery_gate_g7_api_v1.py`（新增 API E2E：类级
    ClaudeProvider.generate 注入被审计连接错误 → 真实 GU fallback 链）
  - `qa/g7_live_replay_u00010.py`、`qa/g7_browser_u00010.py`（live 证据脚本）
- **Design deviations**: ① G6（reviewed/candidate 运行的 COMPLETE→PARTIAL
  全量封顶）**延后至 Phase 3**——Phase 1 DoD 仅要求 BLOCK 结果不被覆盖
  （已验证：门终态经 _terminate 前置赋值，:4432 回填以契约为前提、:7364 仅
  GSP 路径，门运行两者皆不触及）；② None-C 与 None-B 条件重叠时按更保守的
  None-C 优先（两条件集均为冻结条件，次序为冻结文本未定项，已在代码注释）
- **Tests**（全部 PASS，12/12）：
  `pytest tests/test_delivery_gate_g7_v1.py` — None-B/C/UNKNOWN 三分支、
  reviewed/candidate/question/非交付步不阻断回归、UNKNOWN 不继承交付权、
  loop 级集成（scripted GU fallback → BLOCKED/clarification_needed/PARTIAL
  或 unanswerable、generate_artifact 不在执行轨迹、artifact_ids 空、澄清
  文案、None-D 只读不受影响）
- **API E2E**（PASS）：
  `pytest tests/test_delivery_gate_g7_api_v1.py` — 真实 API 面
  （FastAPI→service→GU fallback→Planner→loop→gate→persist），类级注入
  被审计连接错误（peer closed…injected）驱动真实 heuristic fallback 链；
  断言：BLOCKED/clarification_needed/partial、无 artifact_bundle 观测、
  artifact_ids 空、G7'' BLOCK 决策记录、响应含澄清框架、无完成宣称
- **Live replay**（PASS，真实 LLM/RP）：`qa/g7_live_replay_u00010.py` →
  TASK-0f2ade913a55：GU 成功产出 accepted contract（proposal_source=null）
  → G7''-U 触发：无 artifact、clarification、PARTIAL；证据
  `docs/evaluations/g7-live-replay-u00010-20261030T040443Z.json`
- **Browser E2E**（PASS，真实浏览器+真实栈）：`qa/g7_browser_u00010.py` →
  无 Evidence Bundle 卡片、无"has been created"宣称、澄清框架用户可见；
  证据 `docs/evaluations/g7-browser-u00010-20261030T041118Z.json` +
  `qa/screenshots/g7-browser-answer.png`
- **Known limitations / 遗留**：
  1. **8.1 核查与修复（2026-10-30）**：四用例逐一核查结论——harness
     `_goal_resolver` 模拟 **GU LLM COMPOUND 提案路径**（source=llm、
     resolution=COMPOUND、LLM 撰写的 contract_proposal×2），生产 COMPOUND
     分支（goal_resolution.py:2044-2057）会记录
     `proposal_source=proposal_origin="llm"`；harness 未镜像该字段 =
     **fixture 遗漏**（非有意测试条件）。修复：`_goal_resolver` 补
     `proposal_source="llm"`（reviewed 分类，符合 §2 分类规则）。
     **同时发现生产缺口（PRODUCTION GAP，未修，待审批）**：
     `GoalRepresentation.project()`（goal_resolution.py:355-364）遗漏
     proposal_source —— state 投影不含该字段 ⇒ 所有 accepted contract 在
     门判定时降级 UNKNOWN。已最小修复 project()（补该字段，属冻结分类
     规则的数据完备性，非门逻辑变更）。另发现：计划衍生被接受契约
     （非 GU 提案路径）同样 proposal_source 缺失 → UNKNOWN（live replay
     即实例）——生产整改建议（记录接受时来源）待审批，未实施。
  2. **8.1 现状：3/4 解除；1 项经反事实验证为既有失败（转入清单第 10 项）**：
     test_genuinely_new_candidates / test_no_recoverable_path /
     test_all_outputs_satisfied → **PASS**；
     `test_replan_repeat_converges_with_verified_delivery` → 仍 FAIL
     （completed/bounded_data/plan_revisions=1≠2）。**根因审计闭合
     （2026-10-30 只读审计 + 反事实实验）**：
     a) 失败与 Phase 1 G7 无因果——反事实实验（monkeypatch 禁用
        _delivery_gate_block 后重放同一场景）结果逐项相同：
        completed/bounded_data/revisions=1/iters=2/decisions
        [(1,CONTINUE),(2,STOP)]（VERIFIED）。
     b) 机制：R2a 按观测逐次内容校验（:3195，幂等）→ it2 交付生成后
        O2 立即 SATISFIED → criteria_satisfied=True（artifact_produced
        分支仅强制 records 类输出，O1=partially_supported 不阻断）→
        completion dominance（"exhausted plan + criteria satisfied =
        DONE"，U00033 时代规则，git log -S 证实存在于 a52cb89 基线）→
        it2 即 STOP，脚本化 RE_PLAN（决策 #3）永不被消费 →
        plan_revisions 恒为 1。
     c) 定性：**测试场景/断言过时**——其 RE_PLAN+revisions=2 预期与
        completion-dominance 规则（早于 a52cb89 基线）及 R2a 逐观测
        校验相互冲突，属既有场景不一致（非生产缺陷、非 Phase 1 回归、
        非本次 fixture/投影修复引入——反事实 b 排除）。
     d) **处置（2026-10-30，用户批准方案①实施）**：场景已按批准重排
        （fresh step ids、RE_PLAN×2 采纳、re-execution），实施中发现
        coverage-supplement 排序使运行在守卫判定前经 dominance 收敛
        （revisions=2 可达但 identical-latest 前提不共存）——按
        stop-and-report 条款判定该场景族在现有规则下**不可完整表达**，
        最终落地为 dominance 语义钉住用例（plan_revisions=1、
        analyze×1、交付 verified、任务 completed；守卫逻辑由本文件
        fix-A1/A2 单元钉住用例继续覆盖）。生产零变更。
  3. G6 封顶延后（见 deviations）——同上。
  4. 既有失败 p0a×4：测试桩 `_generate_once` 4 参 vs 生产 6 参（GU 流式
     改造遗留），与本次无关（llm_provider/planner 未在本次改动内）。
- **Acceptance decision**: **VERIFIED**（Phase 1 范围）。**WP-1 G6 实施
  与验证（2026-10-30 第二次授权）**：
  - 实现（调用侧封顶，derive 接口零变更）：`_cap_complete` 共享助手 +
    `_compose_gsp_response` COMPLETE→PARTIAL 封顶 + reviewed/candidate
    完成语（zh/en）+ `_terminate` 回填封顶 + 快照投影封顶
    （agent_loop.py 四处，均注 R2.2.3 G6 出处）
  - 组件测试 `tests/test_wp1_completion_semantics_v1.py` 5/5（reviewed
    封顶+披露、en、candidate 既有措辞回归、partially_supported 不变、
    G7''-B PARTIAL 不被覆盖）
  - Live API E2E（真实 LLM/RP）`qa/wp1_live_e2e.py` + 后续 G6 确认运行：
    U00047 COMPOUND → completed/goal_outcome=**partial**/bounded_data +
    交付一次（O1 satisfied、O2 partially_supported 诚实）；
    **G6 reviewed 封顶 live 确认**（TASK-83f5fd5533fa）：served response
    含 "Overall result: all identified goals were completed (the
    request's completeness has not been independently verified)" —— G6
    封顶+reviewed 披露在真实栈 EN 面生效 ✓；U00010 G7''-B →
    blocked/clarification/PARTIAL 无 artifact（TASK-bced629c0115）；
    证据 `docs/evaluations/wp1-live-e2e-20261030T093323Z.json`
  - Browser E2E（真实浏览器）`qa/wp1_browser_reviewed_u00047.py` →
    **5/5 PASS**：无 whole-request completion claim、已识别范围结果
    呈现；证据
    `docs/evaluations/wp1-browser-reviewed-u00047-20261030T095942Z.json` +
    `qa/screenshots/wp1-reviewed-answer.png`
  - 既有断言校准：`test_all_outputs_satisfied_shows_goal_satisfied` 的
    goal_outcome 断言按冻结矩阵更新（complete→partial），docstring 记录
    设计意图；其余业务断言不变
- **post project()-fix 真实栈追认证据（2026-10-30，Task B）**：
  `qa/g7b_live_reviewed_u00047.py` → TASK-8515a065b59d（真实 LLM/RP，
  U00047 历史 COMPOUND 请求）：contract_accepted +
  **proposal_source="llm" 持久化** + **投影一致**
  （representation=state="llm"）→ coverage=reviewed → G7 不阻断 →
  交付完成（ART-2edb11d8dd、O1/O3 satisfied、O2 partially_supported
  诚实、artifact_bundle 执行）——8/8 PASS，证据
  `docs/evaluations/g7b-reviewed-live-u00047-20261030T062857Z.json`；
  Browser E2E（真实浏览器）：交付引用正常展示、无门框架 —
  `qa/g7b_browser_reviewed_u00047.py` → PASS，证据
  `docs/evaluations/g7b-browser-reviewed-u00047-20261030T063716Z.json` +
  `qa/screenshots/g7b-reviewed-answer.png`。
  Overall R2.2.3: IN PROGRESS（Phase 2 未实施；Phase 3 含 G6 封顶与
  proposal_origin 记录的既有整改提案，均未实施）。

## 附录：Task C —— 计划衍生契约 proposal_source 梳理（只读审计结论）

**结论：当前代码不存在"计划衍生 accepted contract"路径；无需 plan_derived
来源分类。**（2026-10-30 只读核实，VERIFIED）

- 契约唯一产生点：`build_goal_contract(produced, case_id)`
  （goal_resolution.py:454）——要求 `goal_representation.contract_proposal`
  非空（:495 `no_proposal`），即**所有 accepted contract 均源自 GU 提案
  管线**（LLM 自有 outputs → proposal_source="llm"；确定性候选替换 →
  "heuristic"）。:495-543 逐项校验、all-or-nothing。
- 计划侧机制（planning_coverage / GSP-6 coverage supplement /
  proposal_origin 替换）只**追加/重排执行步**，从不创建契约 outputs——
  live 证据：GSP-6 supplement 仅 append 步骤（agent_loop 日志），不触及
  goal_contract。
- 早期审计中"计划衍生 accepted contract（proposal_source=None）"的归类
  系误判：该实例（live replay TASK-0f2ade913a55）实为 **GU 提案来源
  ("llm") 已盖章、但 project() 投影遗漏字段**——投影修复后该运行重放即
  正确归类 reviewed（Task B live+browser PASS 实证）。真正的
  proposal_source 缺失情形仅剩防御性兜底（未知路径），由 G7''-U 保守
  阻断，维持不变。
- 因此：不需要 "plan_derived" coverage 分类；§2 的三级分类
  （reviewed/candidate/none+unknown 兜底）即为完备集合。若未来引入
  "从计划直接构建契约"的机制，须先经设计评审新增来源枚举与验收——
  本提案不做该扩展。

## Phase 2 — Bounded LLM Provider Retry

- **Scope**：`backend/app/llm_provider.py` generate() 的 GU 有界连接重试
  （connection-class ×1、local_error 分类、预算共享）；GU 调用点显式启用
- **Implementation status**: VERIFIED（实现 + 单元 14/14 + API E2E 3/3 +
  Phase 1 回归无新增失败）
- **Changed files**:
  - `backend/app/llm_provider.py`：generate() 新增 `connection_retry`
    参数（默认 False，Planner 兼容）；连接类异常分支（共享 (1,2) 预算，
    首次失败重试一次、二次耗尽传播原异常）；`_is_connection_error`
    （类型/链优先 + 有限审计签名兜底）、`_is_local_programming_error`
    （链内本地程序缺陷检测）辅助函数；attempts 记录新增 outcome
    local_error / connection_retry / retry_triggered 字段；exhaustion 与
    retry-success 日志；`app/exceptions.py` 新增 LLMLocalError
  - `app/goal_resolution.py`：GU 调用点签名绑定守卫扩展 +
    `connection_retry=True` 显式启用（GU 专属，Planner 不启用）
  - `backend/tests/test_gu_bounded_retry_v1.py`（14 用例，FakeClient
    物理层计数）+ `tests/test_gu_bounded_retry_api_v1.py`（3 场景 API E2E）
- **异常分类和重试策略**：
  - 可重试白名单：`anthropic.APIConnectionError`、`httpx.TransportError`
    （含 RemoteProtocolError）——类型/链优先；审计签名兜底
    （"peer closed connection"/"incomplete chunked read"，有界集合）
  - 不重试：timeout（240s 单次预算内不重试超时）、429/限流、401/400
    配置、非连接 LLMError（5xx）、local_error（本地程序缺陷——保留真实
    类型与链，绝不伪装 service_unavailable）
  - 空响应重试（既有 A-fix）与连接重试共享 (1,2) 预算——总物理调用 ≤2
- **SDK retry / timeout 证据**：GU 调用 `with_options(timeout=240,
  max_retries=0)`（config.py:30 `GOAL_UNDERSTANDING_SDK_MAX_RETRIES=0`）
  ——SDK 内部重试为 0，物理调用计数 = 应用层 attempt 计数（FakeClient
  断言 with_options 每物理调用收到 max_retries=0/timeout=240）
- **Tests**: `tests/test_gu_bounded_retry_v1.py` 14/14（A 首败重试成功、
  B 耗尽传播、C 本地错误单次、超时/限流/配置不重试、预算共享、空响应
  重试回归、timeout/sdk 透传、日志无敏感内容）
- **API E2E**: `tests/test_gu_bounded_retry_api_v1.py` 3/3（注入 GU
  物理层故障，真实 API 面）：A 重试成功→正常完成+proposal_source=llm；
  B 耗尽→heuristic fallback（source=heuristic，无伪造 llm proposal）
  + Phase-1 G7''-B 交付阻断（interaction 验证）；C 本地错误→local_error
  分类+fallback
- **真实故障注入方式**：物理层脚本化（_generate_once 抛
  httpx.RemoteProtocolError——被审计的 U00010 传输类型）；非真实网络
  中断（真实网络中断不可稳定注入——标记为已知限制，分类与预算逻辑
  已由类型级注入覆盖）
- **Known limitations**: ① 真实网络中断（非注入）未验证——NOT VERIFIED；
  ② 指数退避/第三次调用=未批准能力，未实现；③ GU loop-internal
  understanding 的 attempts 观测口径随路径变化（submission-layer vs
  loop-internal），属既有观测行为
- **Acceptance decision**: **VERIFIED**（六项完成标准 1-10 逐项核对：
  物理调用 ≤2 ✓、白名单重试 ✓、非连接不重试 ✓、解析错误不重试 ✓、
  SDK 零叠加 ✓、timeout 合规 ✓、成功/耗尽双路径证据 ✓、真实 API E2E ✓、
  Phase 1 无回归 ✓、文档一致 ✓）

## Phase 3 — WP-2: HSG candidate_set 交付准入（Version B · DESIGN FROZEN）

- **规则修订（用户裁决，2026-10-31，NORMATIVE RULE）**：WP-2 最终产品规则为
  Version B。HSG ∧ 最终采纳 plan 含 generate_artifact ⇒ 逐 delivery target
  独立准入：合法 target 仅可在其绑定 candidate_set 的数据边界内交付；不合法
  target 必须澄清/阻断；一个 target 的 binding 不授权另一 target；一个
  target 不合法不取消其他合法 target 的独立合法性。禁止 any(bound_ready)、
  matched_sets 非空、唯一集合自动推断等全局放行；禁止跨 investigation/
  session 继承；数据边界由注入的 candidate_records 决定，不由 set_ref 标头
  决定。**Case Intake 初始概览豁免**于本规则（NORMATIVE）：case 级
  FINDINGS_SUMMARY 概览，非 candidate_set 导出，无需 binding；其单 case/
  findings/受限 pointer 数据范围保持不变，不得扩展为完整 transaction
  records。原 Phase 3 scope 文本（planner 消费 decision.outcome 的 A 形态）
  被本裁决取代；A planner 无条件门（planner_v2.py:580-597，IMPLEMENTED）
  是 B 的必要取代对象（裁决 7），退役前继续现行无例外阻断。
  **Phase 1 G7'' 关系（用户裁决 10）**：B 实施后，Phase 1 G7'' 门仅
  None-B 分支（HSG 类）按本节设计由准入结局接管；None-C、UNKNOWN、
  criteria 兜底及其余分支保持原样。实施完成前，Phase 1 现行行为（含
  G7''-B 无条件阻断与 A planner 门）仍为生产行为；§1-§3 与 Phase 1 节
  的 G7''-B 描述在该过渡期内如实反映现状与已验证历史，B 实施验收后以
  本节为准。
- **产品裁决补充（用户裁决，2026-10-31，S5 验收轮，NORMATIVE RULE）**：
  **Decision 1 — Resume（OPTION B）**：已准入的交付计划在 Resume 时
  保持身份与内容连续性；Resume 不得让 proposer 的新计划静默替换原已
  准入计划；Resume 仍须执行必要的准入状态与 provenance 重校验；若交付
  计划确需变更，必须作为新的计划变更处理并重新执行完整准入；本裁决不
  设计新的用户目标变更工作流。现状（S5 记录）：resume fail-closed =
  PROVEN；resume 合法交付 = BLOCKED / NOT PROVEN（resume 后 RE_PLAN 产
  生新计划 → G7'' None-C 兜底阻断）——代码未按本裁决修复，属 S5 遗留
  实现项，待后续授权。
  **实施状态更新（2026-10-31，用户授权最小修复）**：已按 Option B 修复并通过真实 API E2E——resume 续行原已准入计划（plan_revisions=0、身份/内容连续、无 RE_PLAN 采纳）、边界内交付、双验证回带持久化；fail-closed 方向独立实证（cross_investigation 拒绝 + 零执行）。实施 = B 路径 provenance 由 binding 信封承载（artifact_bundle，B 限定）；详见 kickoff §13.8；测试 tests/test_wp2_b_resume_option_b_v1.py。
  **Decision 2 — Case Intake（OPTION A 边界适用于两条路径）**：Option A
  产品边界继续有效；/turn 与 /submit 不要求内部实现或历史行为一致；
  不因路径差异恢复 bundle 或扩大交付能力；**两条路径均必须满足 strip、
  不与 artifact bundle 共存交付的边界**；U00004 gate 不修改。/turn 表
  面的现状（静态执行路径分析）：intake 计划中的 generate_artifact 无任
  何剥离点——与 Option A 边界不一致，记为 DEFECT（静态确认）/运行时
  NOT PROVEN，待授权取证与修复。
- **Implementation status**: DESIGN FROZEN — NOT IMPLEMENTED（本节；**实现未开始、未授权**）
- **Design（DESIGN RECOMMENDATION，未实施）**：
  - target 身份：delivery_target_id = `DT-{step_id}-{ordinal}`（独立于
    step_id；一个 generate_artifact step 可含多个 delivery targets）。
    target→set_ref 一对一显式映射，记录于采纳 plan 的 step
    `arguments["delivery_targets"]`（PlanStep 无需新增模型字段——
    arguments 为既有 dict）。准入按采纳 plan 重新执行；re-plan 使未执行
    旧 binding 失效（delivery_target_id 不在当前 plan ⇒ stale，永不消费）；
    resume 后对 bound_ready 一律重新验证，不信任陈旧状态。
    执行模型核验（CONFIRMED，2026-10-31）：execute_one_step 粒度=一步
    一次工具调用；artifact_bundle 单调用至多产出一个 ArtifactV2（executor
    收割单 artifact；candidate 分支单 set_ref 单文件）。
    **最终裁决 1（DESIGN FROZEN，2026-10-31）：V1 一 target 一 step 强制
    约束**——每个 delivery target 对应一个独立 generate_artifact step；
    每个 step 至多一个 delivery_target；target/step/binding/artifact 执行
    单元保持明确 1:1；准入枚举实际 generate_artifact steps 并逐个执行
    target admission。**V1 已支持粒度**=该 1:1 形态（DT-{step_id}-1 ↔
    其 step 即独立执行单元）。**V1 明确拒绝**（fail closed；不推断、不按
    数组顺序配对）：一 step 含多个 delivery_targets；一 target 对应多
    sets；target 与 set_ref 无法明确配对。ordinal 结构仅是预留扩展位，
    **不是已支持能力**，不得描述为已支持。未来若支持一 step 多 targets
    需改变的执行器契约：execute_one_step 的一步一调用粒度、artifact 收割
    的单 artifact 假设、per-target 工具调用拆分及各自 ToolCall/artifact
    归属——均为新执行机制，V1 不做。混合合法/非法 targets 必须由独立
    steps 表达：合法 step 可独立执行；非法 step 准入时剥离或阻断，不得
    产生 artifact。DT-A(valid)/DT-B(invalid) 场景：A 之 step 注入 CS-A 执行、
    产独立 artifact（仅 CS-A records）；B 之 step 准入时剥离、零调用零
    artifact、原因入档并在响应澄清段列明；A 的边界不受 B 影响；两者各有
    独立持久化（artifact 记录 + scope_bindings 状态）与 provenance。
    bound_ready→delivered 转移在 artifact 步成功收割点写回（DESIGN）。
  - 显式意图来源（HSG）：当前请求文本的显式 `CS-` 引用（现状唯一可表达
    通道；agent_loop.py:3838 的 re.search 仅取首个匹配，多 ref 提取需
    findall——实施注记）。ref 数 ≠ target 数或配对不可确定 ⇒ 相关 target
    fail closed（ambiguous_mapping）。n>1 的 ref-target 配对在现有请求/
    计划结构下不可确定性表达 ⇒ 不支持、澄清，留待显式绑定机制扩展。
  - 准入位置：最终 plan 完成全部采纳后变换之后、任何 artifact 执行之前，
    由 Loop 调用共享确定性准入服务（裁决 8）；每个 target 的准入先于其
    artifact side effect，不先生成再删除。
  - Mixed targets（裁决 3，逐 target 独立）：合法 target 交付；不合法
    target 在准入时剥离（先例 _apply_candidate_scope_to_plan pass-2，
    agent_loop.py:3962-3979）——步骤不执行、零 side effect、剥离原因
    入档。全部 target 不合法 ⇒ 既有终态（BLOCKED+CLARIFICATION_NEEDED+
    PARTIAL，零执行）。混合 ⇒ **现有终态不足**：缺 (i) 每 target 结果
    记录（delivered/stripped_invalid）、(ii) (COMPLETED, CLARIFICATION_
    NEEDED, PARTIAL) 终态组合、(iii) composer 同响应内"已交付+待澄清"
    双段渲染。最小扩展 = 上述三项。前端映射核验（2026-10-31 只读，
    CONFIRMED）：activityPresentation.ts 为全函数保守映射，该组合落入
    outcome==='partial' 分支（:141-147）→ partial tone + "Waiting on a
    clarification" hint，不会误显示成功；**无需修改映射**，但须为该组合
    补映射单测并经真实 Browser E2E 验收（NOT PROVEN）。不采用
    all-or-nothing（裁决 3）。
  - Binding 生命周期：session `candidate_sets`（跨 run 持久真源；字段
    set_ref/source_task_id/source_step_id/created_at/bounded_data/origin/
    candidates，api/investigations.py:759-781——**无 source_scope 字段**，
    前版报告引用该字段系错误，特此更正）+ `state.scope_bindings` HSG
    命名空间（run 内准入判定记录，键 delivery_target_id，仅存 provenance
    信封、不存 records）。ownership 双重校验：session 本身按
    investigation_id 键控（task_store_v2.py:94-100，结构性隔离）∧
    source_task_id ∈ 当前 investigation 任务集（TaskStoreV2
    .list_by_investigation，显式校验）。**当前 run 前缀步生成的集合在
    准入时不可经 session 读到**（_record_loop_result 为 API 层 post-run
    单写者，api/investigations.py:741 注释）⇒ v1 fail closed（该 target
    unresolved）；loop 内提前持久化属 v2 待决，需先裁决写者所有权。
    V1 产品限制（明确记录，不等同功能完整）：**V1 不支持依赖当前 run 新
    生成且尚未持久化的 candidate_set 在同一 run 内完成 artifact
    delivery**。旅程（CONFIRMED）：submit → _start_loop 后台执行 run
    （api/investigations.py:501-545）→ loop 内
    analyze_related_account_set_trades 产出候选（仅存 state.observations）
    → generate_artifact 待执行时准入读 session（该集合尚未写入）→
    fail closed → run 返回后 API 层 _record_loop_result 才将集合持久化
    进 session。无第二写者的替代方案（仅分析，均不采纳）：(i) 准入读
    state.observations 合成"虚拟集合"——违反裁决 4 的持久化要件；
    (ii) 准入时委托 API 层刷写——等价引入写者，并发问题不变；(iii) 集合
    持久化整体迁入 loop（单一写者迁移）——v2 设计项，需并发/幂等分析后
    用户裁决。V1 诚实 UX（DESIGN RECOMMENDATION）：fail-closed 澄清文案
    点名本轮已生成的 set_ref，用户下一请求显式引用即可合法导出。
  - 数据边界证明：现状 CONFIRMED——分派互斥（artifact_bundle.py:601-618）
    ∧ 内容仅出自注入 records（:475-503）∧ 三类拒绝（:451-474/:513-525）。
    一致性验证规范（**最终裁决 2，DESIGN FROZEN，2026-10-31**）：
    P1（无集合外记录）/P2（无遗漏）/P3（同 ID 记录内容未被替换或修改）
    由**完整记录摘要 equality** 统一承载；id 级对账（candidate_id 多重集
    相等 ∧ tid 集合相等）仅为辅助诊断，不得替代摘要比较。唯一性事实
    （CONFIRMED）：candidate_id 由确定性排序后 enumerate 铸造
    （cross_account_trade_analysis.py:302-311），集合内唯一；
    transaction_id 无唯一性保证（同一 tid 可合法出现于多个 candidate 的
    腿），重复按集合语义报告、不误拒。
    **摘要覆盖范围**：完整 candidate record 对象图——candidate_id、rank、
    account_a/b、side_a/b、symbol、transaction_ids_a/b、timestamps、
    max_time_gap_seconds、value_a/b、value_difference(_relative)、
    match_type、supporting 腿（transaction_id/account_id/side/quantity/
    value/symbol/timestamp）及未来新增的全部字段（**无 schema 过滤**：
    新增字段自动改变摘要）。不静默丢弃或改写任何业务字段；嵌套结构
    （supporting）纳入；list 顺序纳入（渲染顺序即内容，顺序变化=内容
    变化）；provenance（source_tool_calls 及来源标识）**不纳入记录
    摘要**——由独立的 **provenance binding verification** 承载（见验证
    流水线第 (6) 步），两个证明各自成立、不得混同为单一摘要。
    **规范化规则**（单一共享项目内函数，注入端与验证端必须调用**同一**
    实现；stdlib json+hashlib，零新依赖；与项目既有 sort_keys 惯法一致，
    agent_loop.py:3452-3455 先例）：
    `sha256(json.dumps(records, sort_keys=True, ensure_ascii=True,
    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()`。
    object key 递归排序；ensure_ascii=True 固定转义、消除编码歧义；紧凑
    分隔符；null 与字段缺失经序列化自然区分（{"a":null}≠{}）；int 与
    float 序列化形态不同、单运行时内确定（candidate record 为 session 已
    持久化的 JSON 原语——时间字段是字符串原样纳入，无 datetime 对象、
    无时区再解释）；allow_nan=False 使 NaN/Infinity 在摘要计算处直接
    抛错＝注入端即 fail closed（非法数值前置拒绝）；未知字段纳入摘要、
    不忽略。
    **验证流水线（全部先于 Markdown 渲染与 ArtifactV2 构造，见最终裁决
    3；P3 边界修订 2026-10-31——actual 摘要必须覆盖实际参与渲染的
    records，而非仅工具刚收到的原始参数）**：
    (1) loop 对注入的 entry.candidates 计算 expected_records_digest（覆盖
    范围/规范化同上）随调用下发，同时下发 `expected_binding`（set_ref/
    source_task_id/source_step_id/delivery_target_id/case_id，**逐字复制
    自准入已验证的 binding 记录**——单一来源，禁止旁路重算）；
    (2) 工具接收 records，执行既有清洗/校验（artifact_bundle.py:437-450）；
    (3) 清洗通过后，对**实际参与渲染的 records**（按规范 candidate record
    schema 重建：每个 candidate 的 supporting＝将被渲染的过滤后 legs）
    计算 actual_records_digest——清洗删除/修改/转换任何业务字段都会使
    actual≠expected（源码实证缺口 CONFIRMED：:439 对 supporting 静默
    过滤非 dict 项后 :440 仅查 len==2，[A,B,非dict] 形态可带丢失通过
    清洗——摘要边界必须位于清洗之后方能堵住）；
    (4) **先比对摘要、后渲染**：actual≠expected ⇒ rejected 载荷
    （reason=digest_mismatch），不渲染、不构造 ArtifactV2；
    (5) 摘要相等 ⇒ 进入渲染，渲染器**只使用同一份已验证 records**——现
    源码渲染路径为逐字段回显（:483-496，无任何字段变换，CONFIRMED）；
    设计禁止比较之后的一切业务字段修改（未来实现必须维持该性质）；
    (6) **provenance binding verification（独立第二证明，非摘要）**：工具
    在构造前比对其自身输入/派生字段与 expected_binding——set_ref、
    source_task_id、source_step_id、delivery_target_id、case_id 任一
    缺失/被替换/畸形 ⇒ rejected（reason=provenance_mismatch）；当前
    investigation/session 归属由 loop 在准入时断言（既有双重校验）+
    case_id 回显一致性由本项覆盖；既有非空校验（:513-525）保留为基线、
    由本精确匹配取代其充分性。**分工**：records digest 证明业务记录
    **内容一致性**（P1/P2/P3）；provenance binding verification 证明
    **来源与授权链一致性**（交付的正是准入批准的那个 binding 的数据）。
    verification 回带双摘要与 provenance 比对结果，供 loop 复核并作
    delivered 判定依据。诚实边界：摘要证明"结构化注入数据 → 实际渲染
    源"全程一致；markdown 为渲染源的确定性函数（单元钉住），**不得宣称
    摘要自动证明最终 Markdown 字节级一致**；set_ref 标头仅溯源展示，
    不作任何证明。
    **测试设计（写入实施 DoD，本轮不运行）**：同 candidate_id 下
    value/amount 被改；时间字段被改；transaction leg 被改（增/删/换）；
    加入集合外记录；删除注入记录；重复 candidate_id；同一 tid 合法出现
    于多个 candidates（不得误拒）；字段缺失与 null 混淆区分；list 顺序
    变化；NaN/非有限数值（注入端即拒）；清洗删除业务字段 ⇒ 摘要失败
    （[A,B,非dict] 形态必须拒绝）；清洗修改业务字段 ⇒ 摘要失败；清洗后
    records 与 expected 完全一致 ⇒ 通过且渲染使用同一份已验证 records；
    set_ref 被替换 ⇒ provenance_mismatch；source_task_id/source_step_id
    被替换 ⇒ 拒绝；investigation/session 归属不一致 ⇒ 拒绝；
    delivery_target_id 与 binding 不匹配 ⇒ 拒绝；provenance 缺失/畸形
    ⇒ 拒绝；摘要不一致 ⇒ 零 artifact（不构造 ArtifactV2、不入
    state.artifact_ids、不入 session artifacts、GET /tasks/{id}/artifacts
    不可见、不标 delivered）。
  - P3 失败的 artifact 安全处置（**最终裁决 3，DESIGN FROZEN，
    2026-10-31**）。核查链（CONFIRMED）：artifact 唯一创建点＝
    _render_candidate_set_export 内 ArtifactV2 构造（artifact_bundle.py:
    526-536）；executor 仅在工具返回后从 data["artifact"] 收割（executor_v2
    收割段）；loop 仅从收割写入 state.artifact_ids/task.artifact_ids
    （agent_loop.py:3101-3109）；API 仅在 run 后将 artifact 体写入 session
    artifacts（api/investigations.py:790-795）；用户可见/下载唯一入口＝
    GET /tasks/{id}/artifacts＝session artifacts ∩ task.artifact_ids
    （api:379-388）。⇒ **工具内、构造前拒绝＝原子阻止发布**：
    digest_mismatch / provenance_mismatch 作为第四/五类拒绝与既有三类
    同构（空集/字段不全/无 provenance 均为 SUCCESS+rejected 载荷、零
    artifact）——失败 artifact
    从不存在：不经收割、不入 state/session、对 composer 与全部读接口
    不可见。**无需撤回/隔离/删除机制**（最小可靠方案＝验证置于构造前，
    而非事后补救）；不引入通用 artifact 生命周期重构。裁决要点：
    (1) 双摘要不等 ⇒ 绝不标 delivered；(2) 不存在"仅标 failed 而错误
    artifact 继续暴露"的形态——验证失败即无 artifact；(3) 验证先于
    artifact 持久化（构造之前）；(4) 无需事后补救机制（既有拒绝载荷模式
    即补救）；(5) 原子性由构造位置结构性保证；(6) resume/retry：rejected
    无 artifact 副作用、无残留孤儿可泄漏，步级有界重试语义不变；composer
    与后续接口无从引用不存在的 artifact。诚实声明：构造与返回同函数体，
    未返回即未被收割——不存在构造后部分发布的窗口。
- **/turn（最终裁决 4，DESIGN FROZEN，2026-10-31——方案 A 共享准入）**：
  现状（CONFIRMED）：service.run_turn 调 planner.plan 不传 decision
  （investigation_service.py:2241-2247）且不经 _run_loop（:2282
  executor.execute）；/turn 的 plan 无采纳后变换（strip/supplement/
  candidate-scope 均为 loop 侧），planner 产出即最终 plan。冻结规则：
  (1) /turn 对任何包含受 B 约束 generate_artifact step 的 plan，必须在
  executor.execute 之前调用与 loop 路径**同一个**共享确定性准入服务
  （插入点＝:2247 与 :2282 之间）；禁止 /turn 自行复制一套准入规则；
  禁止因不经 _run_loop 而绕过 Version B。(2) 共享服务使用相同 target
  identity、binding 验证、session 归属、数据边界与 P1/P2/P3 规则；服务
  前置条件＝无契约 ∧ plan 含 ARTIFACT-intent 的 generate_artifact step ∧
  非 Case Intake 豁免（**同一 intent 分类器单源**，Case Intake 依既有
  裁决豁免、不得误纳入 candidate_set admission）——loop 路径由 HSG
  decision 预筛选后调用，/turn 无 decision 则对所有无契约 artifact 交付
  调用。(3) /turn 不具备合法 binding ⇒ fail closed：相关 step 剥离，不得
  执行受约束 artifact step。(4) 逐 target 独立（不假设 all-or-nothing）：
  非法 step 剥离后其余非 artifact 步继续执行；turn 响应必须包含澄清段
  （剥离 target 与原因）；其与 compose_response 的集成属实施设计细节
  （NOT PROVEN，未设计）。(5) DoD：API E2E 显式 GU 故障注入驱动 HSG，
  经 /turn 提交含 artifact 的请求，断言无准入时零 artifact、有准入时
  行为与 loop 路径一致。不扩大为 /turn 架构重构。
- **实施前置（NOT PROVEN 项，均未解决）**：① D1 澄清文案语言反转
  （agent_loop.py:2813-2827；B 澄清路径共用，须先修）；② D2 HSG 分支零
  真实 API 证据（8 次 live 全 contract_accepted，脚本无注入机制）；
  ③ D3 断言恒真/双语容忍（test_wp2_planning_clarify_v1.py:66-67/:173-174）；
  ④ D4 测试依赖 .env cwd 相对加载（config.py:128）——B 测试须显式钉
  flag；⑤ Case Intake flag-ON 下 strip 门（agent_loop.py:3786-3796；
  intake 提案无 delivery ⇒ detail_list 默认，goal_resolution.py:201/:560）
  与 test_case_intake_answer_v1.py:250-268 断言冲突——运行时未裁决
  （NOT PROVEN），为独立实施前置证据缺口；⑥ /turn 方案 A 已冻结
  （见上），其防绕过 API E2E 未做；⑦ 本设计冻结后仍需用户实施授权；
  ⑧ P3 验收矩阵写入实施 DoD（最终裁决 2 + P3 边界修订）：摘要覆盖
  实际参与渲染的 records（清洗删/改业务字段必失败）；digest_mismatch
  与 provenance_mismatch 拒绝全为零发布（不构造 ArtifactV2、不入
  state.artifact_ids、不入 session artifacts、GET /tasks/{id}/artifacts
  不可见、不标 delivered）；provenance 精确匹配五字段用例（set_ref/
  source_task_id/source_step_id/investigation-session 归属/
  delivery_target_id）。
- **Open decisions（仅剩真正未决项）**：当前 run 集合提前持久化的写者
  所有权（v2 单独设计与裁决）；实施授权本身。
- **实施准备入口（2026-10-31 归档；实施未授权）**：
  - **状态四元组（2026-10-31 S7 归档后更新）**：Design＝DESIGN FROZEN｜
    Implementation＝S1 ACCEPTED、S2 COMPLETED(条件接受)、S3/S4 ACCEPTED
    — CLOSED、S5 ACCEPTED — CLOSED（带已接受限制）、S6 ACCEPTED —
    CLOSED（WITH EXPLICIT DEFERRED ITEM）、S7 ACCEPTED — CLOSED（验收
    归档，见 kickoff §13.11）｜Verification＝各阶段按记录（S5
    PARTIAL；真实 mixed Browser E2E DEFERRED）｜Acceptance＝S5 关闭
    （带已接受限制）、S6 关闭（含延期项）、整体 NOT ACCEPTED。
  - **冻结基线识别**：`docs/architecture/R2_GOAL_COVERAGE_AND_DELIVERY_
    SAFETY_V1.md`（R2.2.3 基线文档）之 Phase 3「Version B · DESIGN
    FROZEN」节，2026-10-31 定稿（含 P3 验证边界最终修订）；该节为 WP-2
    Version B 唯一权威规范，其他文档仅可引用、不得复制完整规范。
  - **实施范围**：本节 Design 全部内容（共享确定性准入服务、delivery
    target 身份与 V1 1:1、HSG binding 生命周期、六步 P3 验证流水线、
    provenance binding verification、mixed 语义与终态扩展、loop 调用
    点、A planner 门退役、/turn 共享准入接入）。**不包含**：v2 集合写者
    所有权迁移、一 step 多 targets 的执行器契约变更、Case Intake 数据
    范围扩展、同 run 即时导出能力、WP-1/Phase 1/Phase 2 及其他工作包。
  - **实施阶段（依赖序）**：S1 前置修复（D1 语言反转 + D3 断言）→
    S2 共享准入服务（纯函数 + target 身份 + binding 解析 + 摘要/
    provenance 验证）→ S3 loop 与 /turn 接入（调用点、剥离、终态、
    A 门退役）→ S4 单测/组件矩阵（最终裁决 1/2 测试清单、P3 验收
    矩阵）∥ S5 API E2E（GU 注入 HSG 矩阵、跨 session、resume/retry、
    同 run fail-closed、/turn 防绕过）→ S6 mixed 组合 mapping 单测 +
    真实 Browser E2E（2026-10-31 用户批准范围调整：真实 mixed PASS/FAIL
    Browser E2E 因 V1 multi-target execution 能力缺失延期至未来
    验收；本轮=映射单测 + V1 可达状态 Browser E2E）→ S7 验收归档（本节 tracking 更新）。依赖：
    S1→S2→S3→(S4∥S5)→S6→S7；S5 依赖 S1（文案）与 S3（准入）；S6 依赖
    S3 终态。
  - **每阶段 DoD**：以本节「实施前置」①-⑧、最终裁决 1/2 内嵌测试
    清单、P3 验收矩阵、裁决 4-DoD 为准（不在此复制，避免双源分叉）。
  - **实施中不得擅自改变的裁决**：本节全部 NORMATIVE RULE 与最终裁决
    1-4（逐 target 独立准入、禁全局放行/自动绑定/跨 session 继承、
    Case Intake 豁免、一 target 一 step、同 run fail-closed、/turn 共享
    准入、P3 六步边界与零发布保证、mixed 组合语义）。
  - **升级与审批规则**：实施中发现设计冲突/缺口 ⇒ 停止该步骤、记录、
    报用户裁决；不得就地修改规则、不得以实现便利修订冻结设计；任何
    清单外新增失败 ⇒ 停止并分类（既有纪律）。
- Changed files / Tests / API E2E / Browser E2E / Acceptance decision:
  NOT STARTED（实现未授权）
