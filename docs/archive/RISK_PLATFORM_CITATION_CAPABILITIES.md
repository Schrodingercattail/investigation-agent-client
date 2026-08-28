# Risk Platform Citation Retrieval & Validation Capabilities

## 1. Available Citation Services

Risk Platform 提供以下 citation 相关的服务：

### Core Services
1. **CitationRetrievalService** (`citation_retrieval_service.py`)
   - 实现严格域强制 citation retrieval
   - 架构：Finding → FindingType分类 → CitationPolicyRouter → 允许范围 → RAG检索 → 验证 → 输出

2. **SimpleCitationService** (`citation_service.py`)
   - 简化的 citation 生成，专注于调查员 UX
   - 每个 finding 一个主要 citation
   - 仅从文本中使用的标记构建 citations

3. **CitationCoverageService** (`citation_coverage_service.py`)
   - 实现最小充分 citation 集
   - 目标：每个响应 3-5 个唯一 citations
   - 硬限制：最多 5 个 citations

4. **CitationValidator** (`citation_validator.py`)
   - 验证 citation 质量
   - 生成警告而不阻止响应（优雅降级）
   - 检查域不匹配、缺失 citations 等

5. **PolicyRAGService** (`policy_rag_service.py`)
   - 轻量级本地文件 RAG（无向量数据库）
   - 从 `repo_root/policies/*.md` 加载 markdown 文件
   - 按标题分割，关键词评分 chunks
   - 过滤掉元数据 chunks

### Supporting Services
- **CitationPolicyRouter** (`citation_policy_router.py`) - 域路由和允许范围
- **CitationMapper** (`citation_mapper.py`) - 域感知 citation 映射
- **CitationRegistry** (`citation_registry.py`) - Citation 注册表

---

## 2. Available API Endpoints

### Evidence API (已由 Agent Client 使用)
```
GET /api/risk/cases/{user_id}/evidence
```
**当前使用**: Agent Client 通过 `risk_platform_client.get_case_evidence()` 调用此端点
**返回**: RiskEvidenceResponse 包含 unified_findings（但 **不包含** citations）

### Explanation API (包含 Citations)
```
POST /api/risk/explain
POST /api/risk/explain/regenerate
```
**返回**: ExplanationResponse 包含：
- `key_findings` - findings 文本，带有 citation 标记如 `[1]`, `[2]`
- `citations` - citation 对象列表，每个包含：
  - `id` - citation ID
  - `doc` - 策略文档名（如 "AML_Suspicious_Indicators.md"）
  - `section` - 筠节路径
  - `quote` - 实际策略文本引用
  - `chunk_id` - chunk 唯一 ID

**Agent Client 当前未使用此端点** - 但这是获取真实 citations 的方式！

---

## 3. Policy Corpus

Risk Platform 使用本地 markdown 策略文件：

**Location**: `/Users/vv/risk-platform-demo/policies/`

**Available Policies**:
1. `AML_Suspicious_Indicators.md` - AML 可疑活动指标
   - 2.1 High-Velocity Transfers（高速度转账）
   - 2.2 Structured or Repetitive Transfers（结构化/重复转账）
   - 2.3 Rapid Fund Movement（快速资金流动）
   - 3.1 Amount Anomaly vs Historical Pattern（金额异常）
   - 5.1 Links to Known Risky Clusters（链接到已知风险集群）

2. `KYC_CDD_Requirements.md` - KYC/CDD 要求
   - 1. Principle（原则）
   - 2.1 Standard Due Diligence (SDD)
   - 2.2 Enhanced Due Diligence (EDD)
   - 3. Common Information Requests

3. `Investigation_and_Action_SOP.md` - 调查和行动 SOP
   - 2. Standard Investigation Flow（标准调查流程）
   - 3. Recommended Actions（推荐行动）

4. `Risk_Scoring_Explainability_Guide.md` - 风险评分可解释性指南
   - 2. Evidence Types (Conceptual)
   - 4. Citation Requirement

---

## 4. Citation Retrieval Process

### Risk Platform 的 Citation 生成流程：

```
1. Finding Classification
   ├─ FindingDomain: ACCOUNT_PROFILE, NETWORK, TRANSACTION, ML_ANOMALY, RULE_SIGNAL
   └─ FindingType: ML_SIGNAL, RULE_SIGNAL, GRAPH_SIGNAL, ACCOUNT_PROFILE, etc.

2. Domain-Specific RAG Query
   ├─ 每个域有特定的搜索术语
   ├─ 允许的文档范围限制
   └─ PolicyRAGService.search(query, top_k, allowed_docs)

3. Citation Selection
   ├─ 每个 finding 最多一个主要 citation
   ├─ 总共最多 5 个 citations
   └─ 确保每个 finding 至少有一个 citation

4. Validation
   ├─ CitationValidator 检查域不匹配
   ├─ 生成警告（不阻止）
   └─ 过滤元数据 chunks

5. Output
   ├─ 在 finding 文本中附加 [n] 标记
   └─ 构建 citations[] 列表
```

### 关键特性：
- **Metadata Filtering**: 自动过滤掉元数据 chunks（Status, Purpose, Demo Template）
- **Domain Enforcement**: 在 RAG 检索之前强制域约束
- **Semantic Validation**: 检查 citation 是否真正支持 finding 的 claim

---

## 5. Citation Validation Semantics

### Risk Platform 的 "Citation Supported" 定义：

**不仅仅检查**：`policy_ids ⊆ retrieved_policy_ids`

**而是检查**：
1. **Finding Type → Policy Domain 匹配**
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
       },
       # ...
   }
   ```

2. **Semantic Support**
   - Citation 的 quote 必须实际支持 finding 的 claim
   - 不仅仅是因为关键词匹配

3. **Metadata Filtering**
   - Status, Purpose, Demo Template 被过滤
   - 短 chunks（<30 字符）被过滤

4. **Warning System**
   - 生成警告但不阻止响应
   - 优雅降级

---

## 6. How Agent Client Can Integrate

### Option A: Use /api/risk/explain Endpoint

**优点**：
- 获取完整的 citations 数据（包括 quote, section, chunk_id）
- 使用 Risk Platform 的语义验证
- 自动 metadata filtering

**缺点**：
- 需要额外的 API 调用
- 返回 Explanation 格式，不是直接的 citation 验证

**实现**：
```python
async def get_explanation_with_citations(user_id: str):
    """从 Risk Platform 获取 explanation 和 citations"""
    url = f"{base_url}/api/risk/explain"
    payload = {"user_id": user_id}
    response = await httpx.AsyncClient().post(url, json=payload)
    data = response.json()
    
    return {
        "key_findings": data.get("key_findings"),  # 带有 [n] 标记
        "citations": data.get("citations"),        # 完整 citation 对象
        "finding_citations": data.get("finding_citations")  # mapping
    }
```

### Option B: Reuse PolicyRAGService Locally

**优点**：
- 无需额外 API 调用
- 完全控制 citation 检索逻辑
- 可以与 Risk Platform 使用相同的策略文件

**缺点**：
- 需要复制策略文件或共享文件系统
- 需要实现域路由和验证逻辑

**实现**：
```python
from risk_platform_demo.backend.app.services.policy_rag_service import PolicyRAGService

# 创建 RAG 服务实例
rag = PolicyRAGService()
rag.load()

# 检索 citations
chunks = rag.search(
    query="high velocity transfers withdrawal frequency",
    top_k=3,
    allowed_docs=["AML_Suspicious_Indicators.md"]
)
```

### Option C: Create New Citation API

在 Risk Platform 中添加新端点：
```
POST /api/risk/citations/validate
Body: {
    "findings": [{"finding_id": "F-1", "claim": "...", "type": "rule_signal"}]
}
Response: {
    "citations": [...],
    "finding_citations": {"F-1": [1, 2]},
    "validation": {...}
}
```

---

## 7. Recommendation

**建议采用 Option A - 使用 /api/risk/explain 端点**：

1. **立即可用** - 端点已存在并返回完整的 citation 数据
2. **语义一致性** - 使用与 Risk Platform UI 相同的 citation 逻辑
3. **最小改动** - 只需在 Agent Client 中添加一个 API 调用
4. **可扩展性** - 未来可以添加 `/api/risk/citations/validate` 端点进行更细粒度的控制

**实现步骤**：
1. 在 `risk_platform_client.py` 中添加 `get_explanation()` 方法
2. 在 Agent Client 的 tools 中添加 `citation_fetch()` 工具
3. 更新 `citation_validate()` 使用真实的 citation 数据而不是 policy ID 检查
4. 更新前端显示真实的 citation（包括 quote 和 section）

---

## 8. Summary

| Aspect | Risk Platform | Agent Client (Current) |
|---|---|---|
| Policy Source | Local markdown files | Mock data (3 policies) |
| Citation Method | Semantic validation via RAG | Policy ID existence check |
| Citation Count | 3-5 max, per finding domain mapping | Based on policy_ids set inclusion |
| Metadata Filtering | Yes (Status, Purpose, etc.) | No |
| Domain Enforcement | Yes (FindingType → PolicyDomain) | No |
| Quote Retrieval | Yes (actual policy text) | No |
| Validation | Warnings for mismatches | None |

**关键差异**: Agent Client 有 **policy mapping**，Risk Platform 有 **citation-backed grounding**。
