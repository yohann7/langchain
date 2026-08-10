# Tools

`search_knowledge` 是唯一知识检索工具。它检查 XiaoXu 权限后调用
Knowledge API，将命中内容标记为不可信材料并保留文档、位置和来源编号。
导入、修改、删除和导出由 Knowledge Service 的管理接口完成。

`get_knowledge_status` 通过 Knowledge HTTP API 查询当前用户的启用状态、
Embedding、SQLite 统计和 Milvus 状态。它没有用户参数，不读取文档正文，不消耗
`search_knowledge` 查询次数，也不产生知识检索来源标记。WxBot 即使未来接入，
也只能通过 XiaoXu 使用该工具，不能直接访问 Knowledge 数据库或 Milvus。

当前 Agent 工具包括 Skill、Knowledge、Web Search，以及显式长期记忆工具
`remember_memory`、`search_memories`、`list_memories`、
`update_memory` 和 `forget_memory`。它们都由统一执行网关强制应用权限、
审计和调用计数；token 用量只统计、不限制执行。deny 会直接阻断且不计为已执行；ask
交给 HITL 审批；持久化 allow 不再重复弹出审批。

记忆工具的参数在网关审计中被隐藏，避免正文或查询词复制到
`audit_events`。写操作使用 `user_memory_write` 风险类别：它只允许写入
当前用户自己的 Agent 状态，并可通过 permission override 显式 deny。

## 检索策略

- `search_knowledge` 先执行一个聚焦查询；只有 Knowledge API 成功返回空结果且
  `remaining_queries` 大于零，才允许针对缺失事实使用不同查询继续检索。
  任何新切片都会立即关闭本轮知识库检索。
- `web_search` 先执行一个聚焦查询；主模型仅可在证据不足且
  `remaining_queries` 大于零时针对缺失事实继续检索。一次工具调用内部配置的
  SearXNG 后端尝试和 Tavily 降级只算一个逻辑查询。
- 执行查询使用 NFKC 和空白规范化；判重再忽略大小写及无意义句末标点。
  Knowledge 指纹包含排序后的知识库范围但忽略 `limit`，Web 指纹只含查询。
- Knowledge 按 `(doc_id, chunk_id)` 去重；Web 会规范化 HTTP/HTTPS URL、移除
  fragment 和跟踪参数，并在同一轮保持连续来源编号。
- 策略中间件位于 HITL 审批之后、执行网关之前。重复、超限、已有知识证据或
  后端已不可用时返回稳定错误码并写阻止审计，但不进入后端、不增加执行计数。
- 每次工具调用前读取对应搜索 YAML；配置非法时返回
  `SEARCH_CONFIG_INVALID`，不进入后端、不消耗查询次数，并只关闭当前轮次的
  对应搜索类型。

搜索状态只存在当前用户轮次的 `RuntimeState` 中，不写入 checkpoint、SQLite
或长期记忆；审批恢复会保留它，轮次结束即丢弃。
