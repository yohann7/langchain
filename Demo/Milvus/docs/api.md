# HTTP API v1

契约文件为 `contracts/knowledge-api-v1.json`。运行 `python scripts/generate_contract.py` 可从当前代码重建；测试会阻止未同步的契约变更。

## 公共健康检查

- `GET /health/live`
- `GET /health/ready`

## 普通 Token

- `GET /v1/knowledge/status?user_id=...`
- `GET /v1/knowledge-bases?user_id=...`
- `GET /v1/knowledge/documents?user_id=...`
- `POST /v1/knowledge/search`

## 管理员 Token

- `POST /v1/knowledge/ingestions`
- `PATCH /v1/knowledge/documents/{document_id}`
- `DELETE /v1/knowledge/documents/{document_id}`
- `POST /v1/knowledge/exports`
- `POST /v1/knowledge/imports`
- `POST /v1/knowledge/rebuild`

所有用户数据都由请求中的 `user_id` 映射为 SQLite `owner_id`，检索同时过滤 owner、知识库和 SQLite 活动版本。普通 Token 与管理员 Token 应使用互不相同的随机值。

`POST /v1/knowledge/search` 的每个 `hits` 项包含必需的 `doc_id`、
`chunk_id`、`document_name`、`location`、`content`、`score` 和
`knowledge_base`；`sources` 同样使用必需的 `doc_id`、`chunk_id`，不再输出
`source_id`。这是响应契约的破坏性升级：部署时先升级可兼容新旧字段的
XiaoXu，再升级 Knowledge Service。默认返回 10 个命中，候选池为 50；请求
可用 `limit=1～20` 显式覆盖返回量。
