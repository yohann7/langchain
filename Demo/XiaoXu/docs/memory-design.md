# 显式长期记忆设计

XiaoXu 实现的是 Agent 自有、按用户隔离的显式长期记忆。

## 行为边界

- 不使用自动记忆 middleware，不从普通对话或工具结果自动提取。
- 只有用户明确要求记住、查询、更新或删除时才调用记忆工具。
- 记忆不会自动注入每一轮上下文；需要时通过工具按需读取。
- `checkpoint` 是 thread 级短期对话状态；`agent_memories` 是 user 级长期状态。
- `/clear` 只删除当前 checkpoint，不删除长期记忆。
- 记忆不是 Knowledge/RAG，不保存知识库文档、切片、embedding 或向量。

## 身份与隔离

`MemoryService` 不接受调用者传入任意 `user_id`。它只从当前运行上下文获取
内部用户身份：

- CLI 使用配置的 `settings.user_id`。
- API 使用 `actor_id -> actor_to_user_id() -> user_context()`。

存储层的新增、查询、更新、删除都必须带 `user_id`；主键为
`(user_id, memory_id)`。即使猜到其他用户的 `memory_id`，操作结果也与
不存在完全相同。

群聊 checkpoint 会被群成员共享，因此服务层禁止在
`conversation_type=group` 时读取或写入私人长期记忆，防止工具结果经共享
checkpoint 间接暴露。企业微信单聊和 CLI 可以正常使用。

## 数据模型与生命周期

数据存于 Agent 自己的 `xiaoxu.db` 表 `agent_memories`：

- `memory_id`：不可预测的随机 ID。
- `user_id`：内部用户域。
- `content`：用户明确要求保存的正文。
- `source`：固定为 `explicit_user_request`。
- `source_thread_id`：创建时的内部 checkpoint key，用于来源审计。
- `created_at` / `updated_at`：UTC 时间。

删除为硬删除。审计事件只保留 memory ID、字节数、数量和结果，不复制记忆
正文或搜索词。SQLite 当前为本机明文存储，磁盘加密和备份策略由部署环境负责。

## 显式接口

Agent 工具：

- `remember_memory(content)`
- `search_memories(query, limit)`
- `list_memories(limit)`
- `update_memory(memory_id, content)`
- `forget_memory(memory_id)`

CLI 还提供 `/memory add|list|search|update|delete`，用于不依赖模型判断的
确定性管理。所有接口仍经过统一工具执行网关。

## 限制

通过配置控制单条正文、查询、单用户记录数量和单次返回数量：

- `memory_max_content_bytes`
- `memory_max_query_bytes`
- `memory_max_items_per_user`
- `memory_max_results`

记忆内容属于用户数据，不是系统指令，不能覆盖权限、审批或工具边界。
