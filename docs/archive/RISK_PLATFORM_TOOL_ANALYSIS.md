# Risk Platform API/服务能力盘点报告

**Date**: 2026-08-26
**Scope**: Risk Platform 已有 API/服务能力分析
**Purpose**: 识别适合作为独立 Agent tool 的能力及运行时决策逻辑

---

## 执行摘要

| API 端点 | 当前状态 | 适合作为 Tool? | 输入 | 返回 |
|---|---|---|---|---|---|
| `/api/risk/cases/{user_id}/evidence` | ✅ 已集成 | ⚠️ 部分 (被 risk_case_fetch 取代) | user_id | 统一发现 + 风险摘要 |
| `/api/risk/explain` | ✅ 已集成 | ✅ **核心推荐** | user_id | 权威发现 + 已验证引文 |
| `/api/risk/explain/regenerate` | ❌ 未集成 | ✅ **推荐添加** | user_id + config | 重新生成的解释 |
| CitationRetrievalService | ❌ 未集成 | ⚠️ 复杂 (需完整 Risk Platform) | finding + domain | 语义引文检索 |
| SimpleCitationService | ❌ 未集成 | ⚠️ 复杂 (需完整 Risk Platform) | text + findings | 简化引文生成 |
| CitationCoverageService | ❌ 未集成 | ⚠️ 复杂 (需完整 Risk Platform) | findings | 最小充分引文集 |
| CitationValidator | ❌ 未集成 | ✅ **推荐独立 Tool** | claims + citations | 引文质量验证 |
| PolicyRAGService | ❌ 未集成 | ✅ **推荐独立 Tool** | query + domain | 策略片段检索 |
| CitationPolicyRouter | ❌ 未集成 | ❌ 不适合 (内部服务) | finding + type | 允许的文档范围 |
| CitationMapper | ❌ 未集成 | ❌ 不适合 (内部服务) | finding + citation | 域感知映射 |
| CitationRegistry | ❌ 未集成 | ❌ 不适合 (内部服务) | N/A | 引文注册表 |

---

## 一、核心 API 端点分析

### 1.1 `/api/risk/cases/{user_id}/evidence` (已集成)

**当前状态**: ✅ 已通过 `evidence_fetch` tool 集成
**推荐状态**: ⚠️ 被 `risk_case_fetch` 功能覆盖，可标记为遗留

**输入**:
```python
{
    "user_id": "U00299"  # 案件/用户 ID
}
```

**返回**:
```python
{
    "user_id": "U00299",
    "case_id": "U00299",
    "evidence": [
        {
            "evidence_id": "EV-TX-12345",
            "type": "transaction",
            "description": "Transaction: BTC sell 1.5 @ 45230.50",
            "value": 67845.75,
            "timestamp": "2024-01-15T10:30:00Z",
            "risk_reason": "Large sudden outflow",
            "source": {...}
        },
        # ... withdrawal_evidence, risk_factor_evidence
    ],
    "unified_findings": [
        {
            "finding_id": "F-1",
            "type": "ml_signal",
            "description": "ML pattern detection score 96.24/100",
            "severity": "high",
            "canonical_name": "ML Pattern Detection Signal"
        },
        # ... rule, risk_factor findings
    ],
    "risk_summary": {
        "risk_level": "CRITICAL",
        "risk_score": 85.5,
        "primary_reason": "ML Pattern Detection",
        "ml_score": 96.24,
        "rule_score": 45.0,
        "graph_score": 18.0
    }
}
```

**运行时决策**: Agent 需要判断是否获取原始证据数据
- **何时调用**: 需要详细交易/提现记录进行分析时
- **何时不调用**: 只需要高层级发现和解释时（使用 risk_case_fetch）

**下一步可能触发**:
1. 基于 evidence 生成详细分析报告
2. 对特定交易进行深入调查
3. 证据聚合计算风险趋势

---

### 1.2 `/api/risk/explain` (已集成 - 核心)

**当前状态**: ✅ 已通过 `risk_case_fetch` tool 集成
**推荐状态**: ✅ **推荐作为主要权威数据源**

**输入**:
```python
{
    "user_id": "U00299"
}
```

**返回**:
```python
{
    "summary": "High risk coordinated trading pattern detected",
    "key_findings": [
        "1. ML Pattern Detection — 96.24/100; a system signal",
        "2. Coordinated Trading Pattern [1]",
        "3. High Withdrawal Frequency [1]",
        "4. First Withdrawal to a New Address [2]",
        "5. Abnormal Withdrawal Behavior"
    ],
    "recommended_action": "Manual Review",
    "citations": [
        {
            "id": 1,
            "doc": "AML_Suspicious_Indicators.md",
            "section": "2. Transaction Velocity & Burst Patterns / 2.1 High-Velocity Transfers",
            "quote": "A sudden spike in the number of outgoing or incoming transfers...",
            "chunk_id": "AML_Suspicious_Indicators#2.1#001"
        },
        {
            "id": 2,
            "doc": "KYC_CDD_Requirements.md",
            "section": "2.2 Enhanced Due Diligence",
            "quote": "Enhanced due diligence applies for high-risk customers...",
            "chunk_id": "KYC_CDD_Requirements#2.2#003"
        }
    ],
    "explanation_source": "LLM",  # 或 "MODEL_FALLBACK"
    "llm_error": None,
    "missing_info": ["account_age", "trade_volume"]
}
```

**运行时决策**: Agent 需要判断是否需要权威发现和引文
- **何时调用**:
  - 用户请求案件解释
  - 需要策略引用支持
  - 需要权威的风险评估
- **何时不调用**:
  - 只需要原始数据（使用 evidence_fetch）
  - 进行批量分析（缓存考虑）

**下一步可能触发**:
1. 引文验证 (citation_validate)
2. 策略深度搜索 (policy_search)
3. 生成最终报告
4. 行动建议验证

---

### 1.3 `/api/risk/explain/regenerate` (未集成 - 推荐添加)

**当前状态**: ❌ 未集成
**推荐状态**: ✅ **推荐作为独立 Tool**

**输入**:
```python
{
    "user_id": "U00299",
    "regenerate_config": {
        "focus_area": "withdrawal_behavior",  # 可选
        "citations_required": True,
        "max_findings": 10,
        "detail_level": "comprehensive"  # 或 "summary"
    }
}
```

**返回**: 与 `/api/risk/explain` 相同结构，但根据配置重新生成

**运行时决策**:
- **何时调用**:
  - 用户对默认解释不满意
  - 需要特定重点领域的深度分析
  - 需要更多或更少的发现
  - LLM 生成失败时重试
- **何时不调用**:
  - 首次请求（使用 explain）
  - 资源限制（LLM 调用成本）

**下一步可能触发**:
1. 与原始解释对比
2. 用户满意度评估
3. 解释优化反馈

---

## 二、Citation 服务能力分析

### 2.1 CitationRetrievalService (未集成 - 复杂)

**当前状态**: ❌ 未集成 (存在于 Risk Platform，未暴露为 API)
**推荐状态**: ⚠️ 复杂 - 需要完整 Risk Platform 访问

**输入**:
```python
{
    "finding": {
        "finding_id": "F-1",
        "claim": "Coordinated Trading Pattern detected",
        "type": "rule_signal",
        "domain": "TRANSACTION_BEHAVIOR"
    },
    "retrieval_config": {
        "top_k": 3,
        "allowed_docs": ["AML_Suspicious_Indicators.md"],
        "min_relevance_score": 0.7
    }
}
```

**返回**:
```python
{
    "citations": [
        {
            "id": 1,
            "chunk_id": "AML#2.1#001",
            "doc": "AML_Suspicious_Indicators.md",
            "section": "2.1 High-Velocity Transfers",
            "quote": "A sudden spike in transfers may indicate...",
            "relevance_score": 0.94
        }
    ],
    "finding_to_citations": {
        "F-1": [1]
    },
    "validation_warnings": []
}
```

**运行时决策**:
- **何时调用**:
  - 需要特定发现的引文
  - 需要域感知的引文检索
  - 需要语义验证的引文
- **何时不调用**:
  - 已有完整解释（使用 explain）
  - 只需要策略文本（使用 PolicyRAGService）

**下一步可能触发**:
1. CitationValidator 验证
2. 引文覆盖率计算
3. 最终报告生成

---

### 2.2 PolicyRAGService (未集成 - 推荐独立 Tool)

**当前状态**: ❌ 未集成 (存在于 Risk Platform，轻量本地 RAG)
**推荐状态**: ✅ **推荐作为独立 Tool**

**输入**:
```python
{
    "query": "high velocity transfers withdrawal frequency",
    "top_k": 3,
    "allowed_docs": ["AML_Suspicious_Indicators.md"],  # 可选
    "filter_metadata": True  # 过滤 Status, Purpose 等
}
```

**返回**:
```python
{
    "query": "high velocity transfers withdrawal frequency",
    "chunks": [
        {
            "chunk_id": "AML#2.1#001",
            "doc": "AML_Suspicious_Indicators.md",
            "section": "2.1 High-Velocity Transfers",
            "text": "A sudden spike in the number of outgoing transfers...",
            "score": 0.94
        },
        {
            "chunk_id": "KYC#3.1#002",
            "doc": "KYC_CDD_Requirements.md",
            "section": "3. Common Information Requests",
            "text": "For high-risk customers, request transaction history...",
            "score": 0.82
        }
    ],
    "total_retrieved": 2,
    "metadata_filtered": 1
}
```

**运行时决策**:
- **何时调用**:
  - 需要策略文本参考
  - 需要支持特定发现
  - 需要策略上下文信息
- **何时不调用**:
  - 已有完整引文（使用 explain）
  - 非策略相关问题

**下一步可能触发**:
1. 引文生成
2. 策略对比分析
3. 合规性检查

---

### 2.3 CitationValidator (未集成 - 推荐独立 Tool)

**当前状态**: ⚠️ 部分集成 (Agent Client 有简化版本 citation_validate)
**推荐状态**: ✅ **推荐使用完整版本**

**输入**:
```python
{
    "claims": [
        {
            "finding_id": "F-1",
            "claim": "Coordinated Trading Pattern - opposite trade ratio 45.24% exceeded threshold",
            "type": "rule_signal",
            "domain": "TRANSACTION_BEHAVIOR"
        }
    ],
    "citations": [
        {
            "id": 1,
            "doc": "AML_Suspicious_Indicators.md",
            "section": "2.1",
            "quote": "Sudden spike in transfers...",
            "chunk_id": "AML#2.1#001"
        }
    ]
}
```

**返回**:
```python
{
    "total_claims": 5,
    "fully_supported": 3,
    "partially_supported": 1,
    "unsupported": 1,
    "domain_mismatches": [
        {
            "claim_id": "F-1",
            "claim_domain": "ML_ANOMALY",
            "citation_domain": "TRANSACTION_BEHAVIOR",
            "severity": "warning"
        }
    ],
    "metadata_warnings": [
        {
            "citation_id": 2,
            "issue": "Citation from metadata section (Status)",
            "action": "filtered"
        }
    ],
    "validation_summary": {
        "citation_accuracy": 0.6,
        "domain_compliance": 0.8,
        "metadata_compliance": 0.95
    }
}
```

**运行时决策**:
- **何时调用**:
  - 需要验证引文质量
  - 需要检查域匹配
  - 需要确保语义支持
- **何时不调用**:
  - 使用 Risk Platform 权威解释（已验证）
  - 只有策略 ID 映射（无真实引文）

**下一步可能触发**:
1. 引文修复
2. 警告展示
3. 报告质量评估

---

### 2.4 SimpleCitationService / CitationCoverageService (未集成 - 复杂)

**当前状态**: ❌ 未集成 (存在于 Risk Platform)
**推荐状态**: ⚠️ 复杂 - 需要完整 Risk Platform 访问

**输入**:
```python
# SimpleCitationService
{
    "text": "Based on findings: ML Pattern Detection, Coordinated Trading...",
    "findings": ["F-1", "F-2"],
    "max_citations": 5
}

# CitationCoverageService
{
    "findings": [
        {"finding_id": "F-1", "claim": "...", "type": "rule_signal"},
        {"finding_id": "F-2", "claim": "...", "type": "ml_signal"}
    ],
    "min_coverage": 3,
    "max_citations": 5
}
```

**返回**:
```python
# SimpleCitationService
{
    "text_with_markers": "Based on findings [1], [2]...",
    "citations": [
        {"id": 1, "doc": "...", "section": "...", "quote": "..."}
    ]
}

# CitationCoverageService
{
    "coverage_result": {
        "total_findings": 5,
        "cited_findings": 4,
        "citation_coverage": 0.8,
        "selected_citations": [...],
        "uncovered_findings": ["F-5"]
    }
}
```

**运行时决策**:
- **何时调用**:
  - 需要为 Agent 生成文本添加引文
  - 需要优化引文覆盖率
  - 需要最小充分引文集
- **何时不调用**:
  - 已有 Risk Platform 解释（包含引文）
  - 不需要生成新文本

---

## 三、推荐的独立 Agent Tool 设计

### Tool 1: `risk_case_explain` (核心 - 已实现为 risk_case_fetch)

**目的**: 获取权威的案件解释，包含发现和已验证引文

```python
def risk_case_explain(
    case_id: str,
    detail_level: str = "standard"  # "summary", "standard", "comprehensive"
) -> dict:
    """
    获取 Risk Platform 权威解释。

    Args:
        case_id: 案件 ID (e.g., "U00299")
        detail_level: 详细程度 (可选)

    Returns:
        {
            "summary": "...",
            "key_findings": [...],  # 带 [n] 引文标记
            "recommended_action": "...",
            "citations": [...],      # 完整引文对象
            "explanation_source": "LLM" | "MODEL_FALLBACK",
            "llm_error": None | "...",
            "missing_info": [...]
        }
    """
```

**运行时决策点**:
1. **是否需要权威发现?** → 调用此 tool
2. **是否需要策略引文?** → 调用此 tool
3. **是否需要风险评估?** → 调用此 tool

**下一步触发**:
- 引文验证 (如需要)
- 策略深度搜索 (如需要)
- 报告生成

---

### Tool 2: `policy_rag_search` (推荐添加)

**目的**: 搜索策略文档，获取相关策略文本

```python
def policy_rag_search(
    query: str,
    domain: str = "all",      # "AML", "KYC", "all"
    top_k: int = 3,
    filter_metadata: bool = True
) -> dict:
    """
    搜索策略文档。

    Args:
        query: 搜索查询
        domain: 策略域限制
        top_k: 返回数量
        filter_metadata: 是否过滤元数据

    Returns:
        {
            "query": "...",
            "chunks": [
                {
                    "chunk_id": "...",
                    "doc": "...",
                    "section": "...",
                    "text": "...",
                    "score": 0.94
                }
            ],
            "total_retrieved": 3,
            "metadata_filtered": 1
        }
    """
```

**运行时决策点**:
1. **用户询问策略细节?** → 调用此 tool
2. **需要支持特定发现?** → 调用此 tool
3. **需要合规性参考?** → 调用此 tool

**下一步触发**:
- 引文生成
- 策略对比
- 合规分析

---

### Tool 3: `citation_validate_full` (推荐增强)

**目的**: 完整的引文质量验证（比当前 citation_validate 更强）

```python
def citation_validate_full(
    claims: list[dict],
    citations: list[dict],
    validation_level: str = "standard"  # "basic", "standard", "strict"
) -> dict:
    """
    验证引文质量。

    Args:
        claims: 发现列表
        citations: 引文列表
        validation_level: 验证严格程度

    Returns:
        {
            "total_claims": 5,
            "fully_supported": 3,
            "partially_supported": 1,
            "unsupported": 1,
            "domain_mismatches": [...],
            "metadata_warnings": [...],
            "validation_summary": {
                "citation_accuracy": 0.6,
                "domain_compliance": 0.8,
                "metadata_compliance": 0.95
            }
        }
    """
```

**运行时决策点**:
1. **需要验证引文质量?** → 调用此 tool
2. **用户质疑引文有效性?** → 调用此 tool
3. **需要域合规检查?** → 调用此 tool

**下一步触发**:
- 引文修复
- 警告展示
- 质量报告

---

### Tool 4: `explanation_regenerate` (推荐添加)

**目的**: 重新生成解释，支持自定义配置

```python
def explanation_regenerate(
    case_id: str,
    focus_area: str = "all",       # "all", "withdrawal", "trading", "network"
    detail_level: str = "standard",  # "summary", "standard", "comprehensive"
    max_findings: int = 10,
    require_citations: bool = True
) -> dict:
    """
    重新生成案件解释。

    Args:
        case_id: 案件 ID
        focus_area: 重点关注领域
        detail_level: 详细程度
        max_findings: 最大发现数量
        require_citations: 是否需要引文

    Returns: 同 risk_case_explain
    """
```

**运行时决策点**:
1. **用户对默认解释不满意?** → 调用此 tool
2. **需要特定重点领域?** → 调用此 tool
3. **需要调整详细程度?** → 调用此 tool

**下一步触发**:
- 与原解释对比
- 用户反馈
- 迭代优化

---

### Tool 5: `evidence_fetch_detailed` (可选)

**目的**: 获取原始证据数据（已实现为 evidence_fetch）

```python
def evidence_fetch_detailed(
    case_id: str,
    evidence_types: list[str] = None  # ["transaction", "withdrawal", "risk_factor"]
) -> dict:
    """
    获取详细证据数据。

    Args:
        case_id: 案件 ID
        evidence_types: 证据类型过滤

    Returns:
        {
            "evidence": [...],
            "unified_findings": [...],
            "risk_summary": {...}
        }
    """
```

**运行时决策点**:
1. **需要详细交易数据?** → 调用此 tool
2. **需要原始证据分析?** → 调用此 tool
3. **需要数据导出?** → 调用此 tool

**下一步触发**:
- 数据分析
- 报告生成
- 趋势计算

---

## 四、Agent 运行时决策流程

### 4.1 主决策树

```
用户请求
    |
    ├─ 询问案件/用户情况?
    |   └─> risk_case_explain (获取权威解释)
    |       ├─ 有引文? → 完成
    |       └─ 无引文/不足? → policy_rag_search (补充策略)
    |
    ├─ 询问策略细节?
    |   └─> policy_rag_search (直接搜索)
    |
    ├─ 请求详细分析?
    |   ├─> risk_case_explain (获取发现)
    |   └─> evidence_fetch_detailed (获取原始数据)
    |
    ├─ 质疑引文质量?
    |   └─> citation_validate_full (验证)
    |       ├─ 合格? → 完成
    |       └─ 不合格? → explanation_regenerate (重新生成)
    |
    └─ 请求重新生成/定制?
        └─> explanation_regenerate (自定义配置)
```

### 4.2 运行时决策因素

| 决策点 | 考虑因素 | Tool 选择 |
|---|---|---|
| 是否需要权威发现? | 用户意图、请求类型 | risk_case_explain |
| 是否需要策略文本? | 问题类型、上下文 | policy_rag_search |
| 是否需要引文验证? | 用户质疑、质量要求 | citation_validate_full |
| 是否需要原始数据? | 分析深度、数据需求 | evidence_fetch_detailed |
| 是否需要重新生成? | 用户满意度、定制需求 | explanation_regenerate |

---

## 五、工具调用链路示例

### 场景 1: 标准案件查询

```
用户: "Tell me about case U00299"

Agent:
1. risk_case_explain("U00299")
   → 返回: 5 个发现, 3 个引文

2. 检查: 是否有缺失引文?
   → F-5 (Abnormal Withdrawal Behavior) 无引文

3. policy_rag_search("abnormal withdrawal behavior", domain="AML")
   → 返回: 2 个相关策略片段

4. 生成完整响应
```

### 场景 2: 深度调查

```
用户: "Deep dive into U00299 withdrawal patterns"

Agent:
1. risk_case_explain("U00299", detail_level="comprehensive")
   → 返回: 详细发现

2. evidence_fetch_detailed("U00299", evidence_types=["withdrawal"])
   → 返回: 所有提现证据

3. policy_rag_search("withdrawal frequency suspicious indicators")
   → 返回: AML 策略相关片段

4. 生成深度分析报告
```

### 场景 3: 引文质量检查

```
用户: "Are these citations valid for case U00299?"

Agent:
1. risk_case_explain("U00299")
   → 返回: 发现和引文

2. citation_validate_full(claims, citations, validation_level="strict")
   → 返回: 验证结果

3. 如有问题:
   explanation_regenerate("U00299", require_citations=True)
   → 返回: 重新生成的引文

4. 展示验证报告
```

---

## 六、实现优先级建议

### P0 (立即实现)
1. ✅ `risk_case_explain` (已实现为 risk_case_fetch)
   - 核心权威数据源
   - 所有请求的基础

### P1 (高优先级)
2. ⭐ `policy_rag_search`
   - 支持策略查询
   - 补充引文缺失
   - 高用户价值

3. ⭐ `explanation_regenerate`
   - 支持定制需求
   - 处理不满意情况
   - 提升用户体验

### P2 (中优先级)
4. ⚡ `citation_validate_full`
   - 增强验证能力
   - 质量保证
   - 当前有简化版本

5. ⚡ `evidence_fetch_detailed` (已实现)
   - 深度分析支持
   - 数据导出
   - 已有基础版本

### P3 (低优先级 - 需完整 Risk Platform)
6. 🔧 `CitationRetrievalService` 集成
7. 🔧 `CitationCoverageService` 集成
8. 🔧 `SimpleCitationService` 集成

---

## 七、技术实现要点

### 7.1 异步处理
所有 API 调用都是异步的，Agent 需要正确处理：

```python
# 当前实现方式
try:
    loop = asyncio.get_running_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        result = pool.submit(asyncio.run, api_call()).result()
except RuntimeError:
    result = asyncio.run(api_call())
```

### 7.2 错误处理
每个 tool 需要处理特定的错误类型：

```python
- ToolArgumentError: 参数错误
- RiskPlatformAuthenticationError: 401/403
- RiskPlatformUnavailableError: 503/超时
- RiskPlatformError: 其他 HTTP 错误
```

### 7.3 缓存策略
- `risk_case_explain`: 可缓存（同一 case_id）
- `policy_rag_search`: 可缓存（同一 query）
- `explanation_regenerate`: 不缓存（每次不同配置）

### 7.4 域感知
不同 finding 类型有不同允许的策略域：

```python
ALLOWED_DOMAINS = {
    FindingType.ML_SIGNAL: {
        PolicyDomain.ML_ANOMALY,
        PolicyDomain.TRANSACTION_BEHAVIOR,
        PolicyDomain.INVESTIGATION_SOP
    },
    FindingType.RULE_SIGNAL: {
        PolicyDomain.TRANSACTION_BEHAVIOR,
        PolicyDomain.INVESTIGATION_SOP
    }
}
```

---

## 八、关键发现与建议

### 关键发现

1. **Risk Platform 已有完整的 citation 系统**
   - CitationRetrievalService: 严格域强制检索
   - CitationValidator: 语义验证
   - PolicyRAGService: 轻量本地 RAG

2. **当前 Agent Client 集成有限**
   - 主要使用 `/api/risk/explain` 端点
   - citation_validate 只是 policy ID 检查，非语义验证
   - 缺少策略搜索工具

3. **策略源是本地 markdown 文件**
   - 位置: `/Users/vv/risk-platform-demo/policies/*.md`
   - 可直接访问用于 RAG

### 实现建议

1. **优先实现 PolicyRAGService 作为独立 tool**
   - 轻量级，本地可用
   - 高用户价值
   - 支持多种查询场景

2. **增强 citation_validate 为完整验证**
   - 添加域匹配检查
   - 添加元数据过滤
   - 语义支持验证

3. **添加 explanation_regenerate 支持**
   - 支持用户定制
   - 处理不满意情况
   - 提升灵活性

4. **保留 evidence_fetch 作为详细数据源**
   - 深度分析场景
   - 数据导出场景
   - 原始证据访问

---

## 九、总结

| Tool | 输入 | 返回 | 运行时决策 | 下一步 |
|---|---|---|---|---|
| risk_case_explain | case_id | 权威发现+引文 | 需要权威解释? | 验证/搜索/报告 |
| policy_rag_search | query+domain | 策略片段 | 需要策略参考? | 引文生成/对比 |
| citation_validate_full | claims+citations | 验证结果 | 需要质量检查? | 修复/报告 |
| explanation_regenerate | case_id+config | 重生成解释 | 不满意/定制? | 对比/优化 |
| evidence_fetch_detailed | case_id+types | 原始证据 | 需要详细数据? | 分析/导出 |

**核心洞察**: Risk Platform 已有完整的 citation 和 RAG 能力，Agent Client 应优先利用这些现有服务而非重建类似功能。

**下一步行动**:
1. 实现 PolicyRAGService 作为 policy_rag_search tool
2. 增强 citation_validate 为完整版本
3. 添加 explanation_regenerate tool
4. 评估是否需要完整 Risk Platform 服务集成

---

**Sources:**
- [API Forensics: Evidence and Investigation Guide - Ammune.AI](https://ammune.ai/blog/api-forensics)
- [API Vulnerability Assessment And Penetration Testing](https://selkeycybersecurity.com/service/api/vulnerability-assessment-and-penetration-testing/)
