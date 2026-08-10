# XiaoXu 用户知识库状态工具设计

## 目标

为 XiaoXu 增加只读工具 `get_knowledge_status`，通过 Knowledge Service 已有的
`GET /v1/knowledge/status?user_id=...` 查询当前用户的知识库详细状态、文档数量
和分块数量。工具不直接访问 Knowledge SQLite 或 Milvus，也不修改 Knowledge
Service、HTTP/SSE 接口或数据库结构。

## 边界与权限

- 工具属于 XiaoXu，调用现有 `KnowledgeClient` HTTP 边界。
- 工具无模型可见参数；`user_id` 必须由 XiaoXu 的当前身份上下文注入，模型不能
  指定、猜测或覆盖其他用户身份。
- 调用使用现有 `KNOWLEDGE_API_TOKEN`，不使用管理 Token。
- 权限为 `READ_SAFE`、无需审批、`uses_network=True`，并复用现有
  `CapabilityPolicy.can_search_knowledge` 用户能力检查。
- 工具不属于搜索查询，不经过 `SearchPolicyMiddleware`，不消耗
  `search_knowledge` 的查询次数，也不产生 `tool_usage:knowledge_search` 标记。
- 工具注册到 XiaoXu 的完整工具列表和低风险渠道 allowlist。未来 WxBot 若接入，
  仍只调用 XiaoXu；WxBot 不直接访问 Knowledge、SQLite 或 Milvus。

## 架构与组件

### 状态 DTO

在 `private_agent.knowledge.schemas` 增加不可变响应模型：

- `KnowledgeEmbeddingStatus`
- `KnowledgeSqliteStatus`
- `KnowledgeStatusResponse`

Embedding 和 SQLite 字段按 Knowledge API 契约严格解析。Milvus 状态允许服务端
返回数据库名、Collection、向量维度等扩展字段，因此在客户端保留为字典，但
必须包含布尔型 `ready`；非法或缺失的核心字段统一视为协议错误。

### Knowledge 客户端

在现有 `KnowledgeClient` 增加：

```python
status(*, user_id: str) -> KnowledgeStatusResponse
```

它向 `/v1/knowledge/status` 发起 GET 请求，并以查询参数传递当前用户标识。认证、
401/403、5xx、网络异常和响应解析沿用搜索接口的异常分类：

- `KnowledgeAuthenticationError`
- `KnowledgeTimeoutError`
- `KnowledgeUnavailableError`
- `KnowledgeProtocolError`

状态请求使用 HTTPX 客户端的默认请求超时，不复用
`knowledge-search.yaml`，避免把状态检查误计为搜索行为。

### 工具服务与注册

新增独立的 Knowledge 状态工具服务函数，职责是：

1. 检查当前用户是否允许使用 Knowledge 能力；
2. 调用 `KnowledgeClient.status`；
3. 将响应转换为稳定的普通字典；
4. 对认证、超时、不可用和协议异常返回脱敏错误。

`build_tools` 注册无参数 LangChain 工具 `get_knowledge_status`。工具调用时从
`current_user_id` 获取当前身份，输出排序后的 JSON，供主模型整理为中文回答。

## 输出契约

成功时保留以下结构：

```json
{
  "enabled": true,
  "embedding": {
    "model": "BAAI/bge-m3",
    "revision": "...",
    "dimension": 1024,
    "ready": true
  },
  "sqlite": {
    "ready": true,
    "knowledge_bases": 2,
    "total_documents": 10,
    "active_chunks": 120
  },
  "milvus": {
    "ready": true,
    "database": "...",
    "collection": "...",
    "dimension": 1024
  }
}
```

错误时返回：

```json
{
  "error": {
    "code": "KNOWLEDGE_UNAVAILABLE",
    "message": "知识库状态暂时不可用。"
  }
}
```

错误正文不得包含 Token、原始 HTTP 响应、底层异常详情或其他用户标识。若
Milvus 状态中的 `error` 非空，工具输出只保留“Milvus 状态异常”的脱敏描述，
不得向模型转发服务端原始异常；状态结果也不得写入长期记忆、checkpoint 或
XiaoXu 数据库。

## 提示与文档

- 系统提示增加：当用户询问知识库是否启用、是否就绪、知识库/文档/分块数量、
  Embedding 或 Milvus 状态时，调用 `get_knowledge_status`。
- 工具描述明确它只查询当前用户状态，不检索文档正文。
- `docs/tools.md` 记录工具边界、字段和不消耗搜索预算的语义。

## 测试与验收

按 TDD 顺序覆盖：

1. 状态 DTO 正常解析、Milvus 扩展字段保留、核心字段非法时拒绝；
2. `KnowledgeClient.status` 使用 GET、正确路径和当前 `user_id`；
3. 401/403、超时、5xx、非 JSON 和非法结构映射为稳定异常；
4. 权限拒绝时不调用 Knowledge API；
5. 工具无模型参数、自动注入当前用户、输出完整状态；
6. 工具错误脱敏，且不产生 Knowledge 搜索标记或搜索查询计数；
7. CLI 和 XiaoXu 低风险渠道工具集合包含 `get_knowledge_status`；
8. XiaoXu 完整测试套件通过；
9. 在现有 Knowledge 容器可用时，调用真实状态 API，核对文档/分块数量及
   Embedding、SQLite、Milvus 字段，不修改服务数据。

## 非目标

- 不新增 Knowledge Service API；
- 不调用管理 CLI；
- 不列出具体文档，不读取知识片段；
- 不修改、导入、删除或重建知识库；
- 不在 WxBot 中实现 Knowledge 访问；
- 不将状态结果写入长期记忆、checkpoint 或 XiaoXu 数据库。
