# FEATURE_FLAG_INVENTORY.md — Investigation Agent Client Feature Flag 台账

**建立**: 2026-11-02(GSP-7 命名纠偏 + M2 Rescope 历史设计恢复 + 全量盘点轮)
**证据约定**: 运行进程内存级 Settings 无法直接内省——运行时实际值以**进程环境
(ps -E)+ env_file(.env)+ 代码默认值链 + 行为探针**交叉核验,并如实标注
`in_process_verified=false`。Git 历史证据以 `git log -S`(皮帽搜索)为准。
**维护规则**: 新增 Feature Flag 必须登记;默认值变更必须先更新本文件"建议
默认值"并经用户批准;每次验收前对照本文件核验运行时实际值。

## 0. 盘点完整性与命名纠偏

- 全量扫描:`backend/app/config.py` 全部 `ENABLE_*`/`bool` 设置 + 全仓
  `grep -rn "<flag>" app/ tests/` 调用面核对 + `git log -S`(皮帽搜索)。
- 共 **7 个 ENABLE_* Flag** + `DEBUG`(运维开关)+ 参数型设置
  (GOAL_UNDERSTANDING_MAX_TOKENS / PLANNER_MAX_TOKENS /
  TRADE_ANALYSIS_* / AGENT_LOOP_* / TARGET_REFINEMENT_MIN_CONFIDENCE
  ——参数非开关,不逐项展开)。
- **命名纠偏(重要)**:`ENABLE_TARGET_VALIDITY_GUARD` 的 config 注释
  "GSP-7/QA" 为误标。该 flag 实为 **AL-3.5.1 G1 目标有效性准入门**
  (模块 docstring:app/target_validity.py:1 "AL-3.5.1 G1 — Target
  Validity Guard (Layer B)";设计文档 docs/plans/2026-10-05-al3-5-1-
  target-validity-guard-design-v1.md)。项目分解中真正的 **WP-GSP-7**
  是 RE_PLAN / Loop Preservation(2026-10-07 GSP 计划 :420-440:digest
  机器事实、采用期确定性过滤)——**常开、无 flag、与本开关无关**。
- RP(:8000)为独立项目,其 flags 不在本仓库可见(独立项目)。
- 前端无 flag 机制(纯展示层)。

---

## 1. ENABLE_GOAL_SEMANTIC_CONTRACT

| 项 | 值 |
|---|---|
| 能力 | GSP 目标语义契约体系(WP-GSP-2…7):GU LLM 提案→确定性校验/规范替换→契约接受→coverage admission→per-output postconditions→Composer 分节 |
| 设计目的 | 目标从自由文本升级为可校验、可逐项对账的结构化契约 |
| 实现位置 | config.py:115(本次校准 True);读取点 agent_loop.py:2893/2995、goal_resolution.py `_gsp_contract_enabled`、composer 分派 |
| 默认值 | **True**(2026-11-02 项目级校准;此前 False,.env=true) |
| 运行时实际值 | True(env_file+默认值链一致;in_process=false) |
| OFF/ON 行为 | OFF=legacy byte-compat 单技能路由;ON=全部已验收契约路径 |
| 验收证据 | 47 测试文件;全部已验收旅程(U00005/08/10/29/33/47/58、GJ-001/002/003)均在 ON 下通过 |
| 治理分类 | A 正常产品能力(核心) |
| 发布默认值建议 | **ON**(维持) |
| 尚未证明 | flag-off legacy 回归面近年少有新测试 |

## 2. ENABLE_SEMANTIC_FINDING_RESOLUTION

| 项 | 值 |
|---|---|
| 能力 | AL-2.8 语义解析基础(Concept Registry + SemanticNormalizer) |
| 默认值 | **True**(原始即 True) |
| 运行时实际值 | True(默认值链;白名单外,in_process NOT verified) |
| OFF/ON 行为 | OFF=无语义解析基础的 legacy;ON=现行已验收路径 |
| 验收证据 | AL-2.8 验收(memory: al28 semantic foundation state) |
| 治理分类 | A 正常产品能力 |
| 发布默认值建议 | **ON**(维持) |

## 3. ENABLE_BOUNDED_PARTIAL_STOP

| 项 | 值 |
|---|---|
| 能力 | bounded partial STOP:输出全部处于已披露数据边界内时接受 STOP(completed/BOUNDED_DATA/partial),消除无效 re-plan 循环 |
| 设计目的 | 修复真实事故 TASK-14dcda628770(O1 partially_supported 被规则 2 两次强制 RE_PLAN) |
| 实现位置 | config.py:129(本次校准 True);agent_loop.py `_bounded_partial_stop_allowed`/`_uncovered_actionable_outputs`/`_has_boundary_disclosure`,接线于 LoopPolicy.enforce 规则 2 |
| 默认值 | **True**(2026-11-02 校准;真实 API E2E + 3-browser-journey 验收 PASS 后) |
| 运行时实际值 | True(默认值链;in_process=false) |
| OFF/ON 行为 | OFF=legacy coerce 逐字节不变(事故形状可复现);ON=有界部分终态 |
| 验收证据 | 单元 16/16、API E2E flag on/off 对照、Browser 3 旅程(cluster-once 断言) |
| 已知风险 | 非 why 形式复合问边界(独立已登记) |
| 治理分类 | A 正常产品能力(Agent Loop 终止语义) |
| 发布默认值建议 | **ON**(维持) |

## 4. ENABLE_SAFE_SELECT_ADMISSIBILITY

| 项 | 值 |
|---|---|
| 能力 | WI-5 follow-up/chip 准入门 |
| 实现位置 | config.py:118(本次校准 True);investigation_service.py:1976/2072 |
| 默认值 | **True**(2026-11-02 校准;此前 False) |
| 运行时实际值 | **True**(默认值链,校准后启动的 PID 43661/51642 已加载;当前 listener 同) |
| OFF/ON 行为 | OFF=legacy follow-up(无准入门);ON=准入裁决后的 follow-up/chip 呈现 |
| 验收证据 | test_admissibility_gate_wi5_v1(12)+test_safe_select_e2e_wi5_v1+test_safe_select_clarification_e2e_wi6(单元+E2E);独立 Browser/用户验收报告无(测试级证据) |
| 治理分类 | A 正常产品能力 |
| 发布默认值建议 | **ON**(维持) |
| 尚未证明 | 独立用户验收 NOT PROVEN |

## 5. ENABLE_CANDIDATE_METADATA_COVERAGE

| 项 | 值 |
|---|---|
| 能力 | WI-2 候选元数据覆盖(候选导出链元数据完整性+缺口披露) |
| 默认值 | **False(维持——本次校准回退)** |
| 运行时实际值 | False(当前 listener PID 51642) |
| OFF/ON 行为 | OFF=legacy 导出;ON=元数据覆盖声明+缺口披露 |
| 验收证据 | test_metadata_coverage_e2e_wi2_v1 + test_candidate_metadata_storage_v1 + test_metadata_seeding_wi3_v1(单元+E2E) |
| **校准事故(2026-11-02)** | 默认 True 校准后,**U00010-shape intake 触发 planning_failure**(candidate.metadata.coverage 分支对非 seed case 产生 invalidated/gone 级联,致 GU 提案被拒)——已回退 False 并在运行环境复验恢复 |
| 治理分类 | **B 已实现、单元/E2E 测试在案,但 default-ON 存在未验证的 intake 兼容缺口** |
| 发布默认值建议 | **OFF(维持)**;post-acceptance 验证轮(覆盖 U00010/U00058/U00033 混合 intake 形状)全绿后再评估翻转 |
| 尚未证明 | 失败的精确根因(metadata 分支级联细节)未定位——仅定位到形状级 |

## 6. ENABLE_TARGET_VALIDITY_GUARD(⚠ 命名纠偏:非 GSP-7)

| 项 | 值 |
|---|---|
| **命名纠偏** | config 注释 "GSP-7/QA" 为误标。实为 **AL-3.5.1 G1 目标有效性准入门**(app/target_validity.py:1;设计文档 docs/plans/2026-10-05-al3-5-1-target-validity-guard-design-v1.md, 2026-10-05)。真正的 WP-GSP-7 = RE_PLAN/Loop Preservation(常开无 flag) |
| **产品能力** | 目标有效性准入门(Layer B):阻止"LLM 提议的 target 绑定缺乏确定性支持证据"进入 finding 级调查 |
| **原始设计目的**(S11 事故): | 用户问「虚拟币洗钱」(case 不存在)→ LLM 提议解析到 F1 ML Pattern Detection → 4 次 finding 级执行 → 以 F1 证据自信作答。错误链 wrong target → wrong evidence → confident wrong-topic answer。精确定义:*缺少 target-validity admission control:无法展示确定性证据关联用户主题与所提议 finding 的语义提案仍可被采纳* |
| **保护边界** | 目标**有效性**(用户主题↔finding 的确定性关联);非语义完整性、非可执行性、非契约完整性 |
| **实现位置/调用路径** | app/target_validity.py(`decide()`;状态 TARGET_VALID/AMBIGUOUS/UNGROUNDED/INVALID;证据类 explicit_selection/context_continuation/title_match/concept_grounding/event_match/validated_assist);调用点 ① agent_loop.py:2801-2852(loop 顶唯一 choke point,planning 前、tool call 前,覆盖 resume):UNGROUNDED→BLOCKED/context_unresolved+诚实澄清;INVALID→BLOCKED+澄清;② context_resolution.py:150/155/1069(语义阶段准入门) |
| **git 历史** | flag 与模块**同源引入于 a52cb89**(2026-10-29 已验收 checkpoint,git log -S 全史唯一引入点);此后模块**零改动**(git log --follow 空除自身) |
| **引入即 False 的原因(AL-3.5.1 纪律)** | 护栏按 scenario flag-ON 验证、验证后恢复 OFF 的既定纪律(GSP 计划:"restored to default OFF afterwards (AL-3.5.1 discipline)") |
| **验收证据** | test_al351_target_validity_guard_v1(**41 测试**:S11 再现、explicit selection、context continuation、resume 兼容、UNGROUNDED/INVALID 分支);后续 GSP 套件扩展面引用(test_cg3/al351 suite) |
| **E2E-at-ON 证据** | 【2026-11-03 更新,原记录"无(纪律性 OFF)"为 2026-11-02 前历史】ON 验证轮已执行(独立隔离栈,2026-11-03):N1×3 安全判据全过、P1-P6/P4 resume 合法路径无回归、GJ-001(post-fix)/002/003/B1 浏览器 PASS;缺口如实登记(见"剩余验证限制") |
| **ON 对已验收旅程的影响** | 【2026-11-03 实测更新】已覆盖合法旅程在 ON 下无回归(GJ-001/002/003/B1 PASS;P3.5 目录锚定路径 P3 leg2 正常执行);确定性路径(title match/explicit selection/context continuation)不受影响;未覆盖的负向分支(UNGROUNDED/INVALID/AMBIGUOUS 真实现场)仍为已知限制 |
| **与 bounded/F5 修复的关系** | 无交互(ml_signal_explanation 输出 subject=case,不触发 finding 锚定;guard 只作用于 finding focus 准入) |
| **治理分类** | 【历史:C/D 安全护栏:已实现、单测充分(41)、E2E-at-ON 证据缺口】→ 2026-11-03:缺口经 ON 验证轮部分收窄,负向分支现场证据仍缺(已知限制),正式启用 |
| **发布默认值建议** | 【历史建议"OFF(维持)"已于 2026-11-02 记录】→ **2026-11-03 正式启用 ON(用户批准;接受剩余验证限制,详见上方状态段与状态变更日志)** |
| **尚未证明** | ON 状态全旅程拦截行为;P3.5×guard 交互;UNGROUNDED/INVALID/AMBIGUOUS 真实运行时现场(已知验证限制,未证明≠失败≠已通过) |

## 7. ENABLE_TARGET_GOAL_REFINEMENT(M2 Rescope / WP-M2-1)

| 项 | 值 |
|---|---|
| **产品能力** | M2 rescope 目标精化:AMBIGUOUS 目标解析(保留候选集)时,一轮有界 goal-aware 精化(LLM 裁决 SELECT/CLARIFY,阈值 0.7),代替直接澄清 |
| **M2 rescope 是什么**(ADR docs/baselines/2026-09-21-target-goal-refinement-and-m2-rescope-v1.md,2026-09-21,"Accepted for M2 implementation planning"): | M2 re-baseline 将"目标精化"从单一 SELECT 策略重定义为有边界的目标裁决流程。**不缩小/拆分/替换用户目标、不改用户原文**;产出 refined_target_id(裁决)+refinement_evidence(审计);单轮、无迭代放大、无用户确认环节(SP-M2-1 发现:强制 SELECT 在真模糊集上不安全→以 clarify 收尾);与 Goal Contract 无关(契约由 GU/coverage 独立构建);与 RE_PLAN 无关(精化在规划前) |
| **解决的问题** | AMBIGUOUS 解析只澄清不裁决的体验缺口;清晰集稳定正确 SELECT(R-C/R-H/FX-1/FX-3 实证) |
| **历史关闭原因(有据)** | SP-M2-1 **CLOSED 2026-09-24: Discovery Complete / Current Contract Rejected**(:182/:215/:229:"genuinely ambiguous sets produce unsafe forced SELECT; threshold alone is not a safe boundary";Safe Select Policy "confidence ≥ 0.7 → SELECT" 被拒为 WP-M2-1 基线)→ flag 引入即 False(commit **cc56544** 2026-09-27 "complete governed investigation agent track",`git show` 证实 `+ ENABLE_TARGET_GOAL_REFINEMENT: bool = False`) |
| **是否被替代** | **部分覆盖,未完全替代**:清晰集由确定性解析覆盖;真模糊集由既有澄清路径收尾;safe contract(WP-M2-1,2026-09-24-wp-m2-1-safe-target-refinement-contract-v1.md)设计完成但**实现未启动且 PMO 门控未放行**(:232 "implementation NOT started; PMO approval gates implementation") |
| **实现位置/调用路径** | config.py:104(False);investigation_service.py:1919-1960(AMBIGUOUS+candidates 时 flag OFF→skip="flag_disabled"→澄清;ON→refine_target 裁决);生产接线 app/api/investigations.py:152(`refinement_resolver=_default_refinement_resolver()`→app/target_refinement.py `refine_target`,min_confidence=0.7)。**当前代码可真实触发**(非残留) |
| **target_refinement.py** | **存在**(cc56544 引入,4d00ea4 最后修改);`refine_target(request_text, candidates, goal, llm, min_confidence)`;决策 ∈ {SELECT, CLARIFY};docstring "never a forced pick" |
| **OFF/ON 行为** | OFF=AMBIGUOUS+candidates→skip="flag_disabled"→既有澄清;ON=裁决分支启用 |
| **验收证据** | SP-M2-1 spike(Layer-B 5-10 runs)+SP-M2-1C+safe-contract 设计;**独立正式验收报告:无**(spike/discovery 级) |
| **对已验收旅程的影响(若 ON)** | POTENTIAL IMPACT:AMBIGUOUS 形状旅程(U00008/U00005 澄清形状)将先走精化裁决;真模糊集 unsafe SELECT 风险已被 SP-M2-1 关闭结论确认 → OFF 是安全默认 |
| **治理分类** | **C 已设计、实现未启动(PMO 门控)、spike 合同被拒** |
| **发布默认值建议** | **OFF(维持)**;翻转前提 = WP-M2-1 safe contract 实现 + 独立验收(非本轮) |
| **尚未证明** | safe contract 实现后的裁决安全性 |

## 8. DEBUG

运维开关,默认 False,运行 False。分类:临时开发/运维。建议 OFF(维持)。

## 9. 其他记录(非开关)

TARGET_REFINEMENT_MIN_CONFIDENCE=0.7(参数,随 #7);GOAL_UNDERSTANDING_MAX_TOKENS=8192 / PLANNER_MAX_TOKENS=8192 / GOAL_UNDERSTANDING_TIMEOUT_SECONDS=240(Q2 闭环校准容量参数);TRADE_ANALYSIS_*(预算/容差,baseline §12b.3 已决);AGENT_LOOP_MAX_ITERATIONS=8/MAX_RETRIES_PER_STEP=1(有界循环参数)。**未发现其他未入册 ENABLE_/bool 开关**(全量扫描)。

---

## 10. 运行环境快照(2026-11-02 20:07 校准重启后;详细见验收报告)

- :8001 PID 50822(20:07:39,当前树全部修复+校准默认值);
- :3001 PID 16741 / :8000 PID 23055 原样;
- HEAD 9625dbc + 未提交修改(全部保留,零 Git 写操作)。

⚠ **运维注记(如实)**:由 Claude 会话托管的后端任务存在被会话回收停止的
既往模式(本日 4 次)。**持久启动命令(用户终端执行)**:
`cd backend && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001`
——config.py 校准后默认值已承载全部基线,**无需内联 flag 变量**。

---

## Feature Flag Governance / Acceptance Principles(2026-11-02 固化)

1. **缺 E2E 证据不得永久关闭重要护栏**——登记为验收缺口并安排 ON 验收轮;
   单测 ≠ E2E;E2E 通过前不得盲目开启影响运行的护栏。
2. **ON 的一次回归不得直接接受 OFF**——必须调查 OFF 的功能损失、核对其他
   安全保证;ON/OFF 各有场景问题时寻找两全修复;关 Flag 只能有记录的临时
   止损,不是最终设计。
3. **一个场景通过不得掩盖另一场景退化**——正反场景同时纳入验收矩阵;整体
   不得 PASS;组合/依赖必须做影响分析。

完整治理台账见上文各 Flag 条目;默认值变更必须先更新本文件并经用户批准。

---

## Current Feature Flag Decisions & Acceptance Status

**(2026-11-02 治理轮正式记录;本节为项目当前 Flag 决策的权威摘要)**

> **台账维护规则**:
> 1. 本节状态是**当前批准的决策**,不是永久不变的设计事实。
> 2. 每次状态变化必须记录:日期 / Flag / 原状态 / 新状态 / 决策依据 /
>    相关代码·文档·测试证据 / 审批人或审批来源 / 验收状态。
> 3. 方案完成、测试完成、E2E 通过、用户批准、默认值翻转是**不同状态**,
>    不得混写。
> 4. 未经用户明确批准,不得修改 Flag 默认值。
> 5. 不得因为单个场景通过就宣称整个 Flag 安全。
> 6. 不得通过关闭功能或减少场景覆盖制造绿色测试结果。
> 7. Feature Flag 的状态、决策和验收进展**必须同步更新本文件**——不得只在
>    对话、任务报告或临时文档中更新状态。

### ENABLE_TARGET_GOAL_REFINEMENT

| 项 | 值 |
|---|---|
| 当前状态 | **OFF** |
| 正式决策 | 继续 OFF;不恢复旧 confidence threshold SELECT 策略;Safe Contract 保留为未来候选能力;未来实施须重新审批并独立验收;当前不安排旧策略重启 |

**已确认事实**:旧策略 confidence ≥0.7 → SELECT 被 SP-M2-1 证据否定(安全/不安全候选 confidence 分布重叠;真模糊集 unsafe forced SELECT);SP-M2-1 于 2026-09-24 CLOSED(Current Contract Rejected);Safe Contract 设计完成但 implementation NOT STARTED(PMO approval gates implementation);**未找到 PMO 未批准的具体原因记录,不能推断为安全问题或测试失败**;现有 clarification path 有代码和部分测试/旅程证据,多轮澄清有界性及部分分支运行级证据不完整。

**后续状态更新条件**:PMO 审批(记录依据/范围/DoD/验收结果)→ 否则维持 OFF,不自动推进。

### ENABLE_TARGET_VALIDITY_GUARD

| 项 | 值 |
|---|---|
| 当前状态 | **ON / ENABLED(2026-11-03 正式启用,默认 True,进入 Agent 正常运行路径)** |
| 正式决策 | 用户批准正式启用(2026-11-03):S11 真实风险 + 已实现 + 已覆盖合法路径 ON 验证无回归 + decide() 41 单测 + **接受剩余验证限制并启用**。启用基线:HEAD 9625dbc(main)+ 工作区未提交变更(含本次 config.py G1 行修改;未执行 Git 写操作)。显式 env 覆盖能力保留 |
| **剩余验证限制(启用时接受;未证明 ≠ 失败 ≠ 已通过)** | ① UNGROUNDED:真实 API/Browser 阻断证据不足(ON 验证轮 N1 未见 Guard 拦截形状——解析从未提议绑定,LLM 天气);② INVALID:真实 API/Browser 运行时证据不足(维持 decide() 单测级);③ AMBIGUOUS:真实输入未稳定触发,运行时证据不足。上述为**已知验证限制**——不代表 G1 未实现,不构成本次启用的阻断条件;不伪造为已通过;持续以运行遥测观察 |
| 启用后验证(2026-11-03) | 配置层:settings 默认 True,.env/进程 env/docker 无覆盖;进程层::8001 重启后进程 env 无 G1 键(默认派生),preflight `--expect-flag ENABLE_TARGET_VALIDITY_GUARD=true` 经进程等价链(settings_default,listener cwd)matches=true,FRESH;最小真实 API E2E 合法场景在 G1(默认派生 ON)真实 listener 通过;:3001→:8001 代理链与 RP :8000 未受影响。证据:backend/docs/evaluations/g1-enable-*.json |

**2026-11-03 ON 验证轮结果(隔离 :8011/:3011/独立 /tmp store,RP :8000 只读;preflight FRESH ×2)**:

| 维度 | 结果 | 证据 |
|---|---|---|
| 隔离 | :8011 经 launcher(qa/al351_isolated_backend.py)注入独立 SQLite;种子 8 候选+10 概念与生产一致;启动时上会话对共享验收 DB 的 24 行污染已备份+清除 | al351-g1on-preflight-20261103.json;al351-shared-db-pollution-backup-20261103.json |
| Guard 安全(N1×3,真实 LLM/RP) | 全部安全:零 finding 级工具、零 F1 focus、零替换回答、诚实非主张、persisted goal_outcome=unanswerable×3;终态 completed/bounded_data = Guard 未介入的既有 case 级路由(解析未提议 finding 绑定,LLM 天气),**非设计期待的 clarify family,如实登记为形状偏差** | al351-g1on-matrix-20261103T130*.json |
| 合法旅程无误拦 | P1(U00058 F5-ask 与当日基线运行逐字段同形,无 Guard 干扰)/P2/P3(leg2 evidence_kind=title_match)/P5(双 output 契约)/P6(replans=2,goal/契约/工具参数全保持)/P4 resume(中断→重启→恢复,无误拦) | 同上 + al351-g1on-p4-resume-20261103.json |
| Guard 拦截路径(UNGROUNDED/INVALID 分支真实现场) | N1 真实输入未产生 Guard 拦截(解析从未提议绑定)→ 拦截路径本轮由 41 单测覆盖(loop 级 flag-ON),真实 API 现场NOT PROVEN;P7 AMBIGUOUS 两真实措辞未触发 → API NOT PROVEN;N2 INVALID 维持单测级 | design §AC 批准的证据等级 |
| 浏览器 E2E | GJ-001(post-fix 复跑)/GJ-002/GJ-003/B1 全 PASS(卡片/契约/双目标);n1ui(S11)截图证明诚实澄清正确渲染,但其 harness 卡片断言 FAIL(前端历史 ActivityBlock 一次性 fetch 水合悬挂,非 Guard 回归,产品不修);validui(S06 形状)planner 天气失败×2 → NOT PROVEN | al351-gj-ratio-postfix-*.json、al351-u00010-mlscore-browser-isolated-20261103.json、al351-g1on-browser-*.json、al351-wrapper-evidence-path-fix-diff-20261103.md |

**已确认事实**:AL-3.5.1 G1 Target Validity Admission Gate(保护 S11:case 不存在主题被错误映射到现有 finding 并错误回答);41 单测含 loop 级 S11 形状;2026-11-03 独立隔离 ON 验证轮按批准矩阵执行(Guard 准入正确性:合法旅程零误拦;安全判据全过;业务目标维度 P1 薄弱回答为既有行为、与基线同形;拦截分支真实现场未触发=NOT PROVEN)。

**后续状态更新条件(每阶段单独记录,不得混写)**:方案完成 ✓ → 用户批准执行 ✓ → 隔离环境准备 ✓ → API E2E-at-ON ✓ → Browser E2E-at-ON ✓(缺口登记) → 正反场景+既有旅程回归 ✓(S06 形状 UI 旅程除外,NOT PROVEN) → 用户验收 ✓(2026-11-03 正式启用任务) → 最终 Flag 决策 ✓(**ON,2026-11-03**;历史状态与验证记录全部保留,未删除未重写)。

### ENABLE_CANDIDATE_METADATA_COVERAGE

| 项 | 值 |
|---|---|
| 当前状态 | **OFF — FROZEN, KEEP OFF** |
| 正式决策 | 继续 OFF;主题**暂时冻结**,不进入修复实施;不自动恢复 ON;不自动安排后续工作 |

**冻结原因**:
1. Coverage ON 已有隔离实验复现的 planning_failure(explain_signal ∉ case_intake planning_steps → STEP_NOT_ALLOWED),存在明确规划失败风险;
2. 直接失败机制已确认(check_plan 拒绝),但上游枚举变化根因未定位;
3. 三个修复方向(报告级降级/补 seed metadata/扩展 skill selection)均未获批准;
4. 证据不足以选择既修复规划失败又保留 metadata 治理能力的方案;
5. 猜测性修复可能改变原设计行为或引入新回归;
6. OFF + Safe Select ON 是有完整验收证据支持的组合。

**已确认机制**:Coverage ON → check_plan 收到 case_intake + [fetch_case, explain_signal] → STEP_NOT_ALLOWED → planning_failure。Coverage OFF → 同 GU payload 下计划仅 [fetch_case] → 正常完成。

**NOT PROVEN**:Coverage ON/OFF 规划枚举差异的上游根因;resolution 输出完整对比;invalidated/gone 与 explain_signal 入计划的因果追踪;Safe Select 与 metadata 证据层完整传递。

**OFF 功能损失**:reconcile_coverage 目录对账不运行;覆盖报告能力损失。**未完全证明**:Safe Select 是否独立具备全部判别证据;INCOMPLETE/UNVERIFIED 约束完整性;stale metadata 对 Safe Select 的影响。

**解冻条件(仅一即可)**:项目需求要求恢复能力;新证据定位枚举分歧根因;新证据证明 OFF 有实际安全问题;用户明确要求。

**重新启动流程**:先恢复调查和设计审批,不得直接写代码。

### ENABLE_SAFE_SELECT_ADMISSIBILITY

| 项 | 值 |
|---|---|
| 当前状态 | **ON(维持)** |
| 正式决策 | 维持 ON;不因 Candidate Metadata 调查关闭;不得未经验证地声称 Safe Select 完全替代 Candidate Metadata 的全部语义证据能力 |

**后续状态更新条件**:发现明确回归或新证据时重新评估;Candidate Metadata 修复或状态变化必须重新验证与 Safe Select 的组合行为。

---

## Flag 治理状态变更日志

| 日期 | Flag | 原状态 | 新状态 | 依据 | 证据 | 审批 | 验收 |
|---|---|---|---|---|---|---|---|
| 2026-11-02 | GSC / BOUNDED / SAFE_SELECT / CANDIDATE_METADATA | 各异(False/False/False/False) | True/True/True/False | 项目级最终验收校准 | config.py 校准 + 各自验收证据 | 用户(最终验收环境准备轮) | GJ/U00004/U00010 E2E ✓ |
| 2026-11-02 | CANDIDATE_METADATA_COVERAGE | True(校准) | **False(回退)** | U00010-shape intake planning_failure | 隔离实验 + 运行环境复验 | 校准回退(自动) | — |
| 2026-11-02 | ENABLE_TARGET_VALIDITY_GUARD | False | False(维持) | 无 E2E-at-ON 证据;AL-3.5.1 纪律 | al351 41 单测 | 治理轮(记录) | — |
| 2026-11-03 | ENABLE_TARGET_VALIDITY_GUARD | False | **False(维持;ON 验证轮已执行,待用户验收)** | 独立隔离 ON 验证轮按批准矩阵执行完毕:API 安全判据全过(N1×3 零 finding 级工具/零替换回答/诚实非主张/goal_outcome=unanswerable×3),合法旅程无 Guard 误拦(P1/P2/P3/P5/P6 无一 context_unresolved 误判;P3 leg2 evidence_kind=title_match 真实路径),RE_PLAN 保持验证过(P6_composite replans=2 全保持);GJ 浏览器回归 GJ-001(post-fix)/002/003/B1 全 PASS;缺口如实登记:N1 终态形状为 completed/bounded_data(Guard 未介入的既有 case 级路由,非 clarify family)、P7 AMBIGUOUS 真实措辞未触发=API NOT PROVEN、N2 INVALID 维持单测级、P1 业务回答薄弱(既有行为,基线同形)、S06 形状 UI 旅程 planner 天气失败×2 | 见 2026-11-03 验证轮证据组:al351-g1on-preflight(-postrestart)-20261103.json、al351-g1on-matrix-20261103T*.json、al351-g1on-p4-resume-20261103.json、al351-gj-ratio-postfix-*.json、al351-u00010-mlscore-browser-isolated-20261103.json、al351-g1on-browser-n1ui/validui-20261103.json、al351-wrapper-evidence-path-fix-diff-20261103.md | 用户批准执行(2026-11-03;不翻默认值) | API E2E ✓ / Browser 部分 ✓(缺口登记) / 用户验收待做 |
| 2026-11-03 | ENABLE_TARGET_VALIDITY_GUARD | False | **True(正式启用,进入正常运行路径)** | 用户批准的正式启用决策:① G1 针对真实 S11 风险;② 已实现(a52cb89 引入后模块零改动);③ 已覆盖合法路径在 ON 验证轮无回归(见上行);④ decide() 有 41 单测覆盖;⑤ 接受剩余验证限制并正式启用(缺口为已知限制,非未实现,不构成启用阻断)。**剩余验证限制(如实记录,未证明≠失败≠已通过)**:UNGROUNDED 真实 API/Browser 阻断证据不足(ON 轮 N1 未见 Guard 拦截形状);INVALID 真实 API/Browser 运行时证据不足(维持单测级);AMBIGUOUS 真实输入未稳定触发(运行时证据不足) | config.py(settings 默认 True;进程 env/.env 无覆盖);启动后验证:g1-enable-preflight-20261103.json(--expect-flag 经 settings_default 链 matches=true)+ g1-enable-postcheck-20261103.json + 最小真实 API E2E(合法场景,G1 ON) | 用户(2026-11-03 正式启用任务批准) | 启用后最小验证 ✓;剩余限制见依据栏;持续以运行遥测观察 |
| 2026-11-02 | ENABLE_TARGET_GOAL_REFINEMENT | False | False(维持) | spike 合同被拒;safe contract 未实现 | m2-rescope ADR + spike 文档 | 治理轮(记录) | — |
