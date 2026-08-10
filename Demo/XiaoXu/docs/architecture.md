# XiaoXu 架构

`app.py` 是组合入口；`agent/` 负责编排，`models/` 负责模型状态，
`tools/` 负责能力实现，`skills/` 负责渐进加载，`interfaces/` 提供 CLI
和 API。`knowledge/` 是唯一的 Knowledge API HTTP 边界。

XiaoXu 不导入 PyMilvus、不解析知识库文档，也不读取 `knowledge.db`。
`xiaoxu.db` 只保存 Agent 自身状态，其中 `agent_memories` 是显式、按用户
隔离的长期记忆；它不包含任何 Knowledge/RAG 表、文档或向量。

所有 LangChain 工具调用与本地命令调用都经过同一个执行网关。网关以
`PermissionPolicy` 为唯一 allow/ask/deny 判定源，并统一写入权限检查、
执行开始和执行完成审计；只有真正开始执行的工具才计入用量。
模型调用与工具调用按用户、UTC 日期写入 `daily_usage`，该数据只用于
统计和审计，不限制模型或工具执行。

API 不直接使用渠道传入的 `thread_id` 作为 checkpoint key。单聊 key
由渠道、会话和 actor 共同 HMAC 派生，群聊 key 由渠道和群会话派生；
原始渠道标识不会写入 checkpoint 主键。
