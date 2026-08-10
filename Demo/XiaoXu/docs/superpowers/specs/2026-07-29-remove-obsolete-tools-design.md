# 删除 XiaoXu 旧工具设计规格

日期：2026-07-29

## 目标

从 XiaoXu 中彻底删除以下 11 个 Agent 工具及其专属底层实现：

- `get_current_time`
- `calculate_expression`
- `create_todo`
- `list_todos`
- `complete_todo`
- `create_reminder`
- `list_reminders`
- `cancel_reminder`
- `list_files`
- `read_text_file`
- `search_text_files`

删除完成后，CLI Agent 只保留 `activate_skill`、`web_search` 和
`search_knowledge`；企业微信 Agent 只保留 `activate_skill` 和
`search_knowledge`。

## 删除范围

### Agent 组装

- 从 `agent/factory.py` 删除旧工具导入、LangChain 工具包装、权限注册和
  `all_tools` 项。
- 从 `AgentResources` 和 `create_resources()` 删除 `TodoStore`、
  `ReminderStore` 和文件授权根目录资源。
- 从 `agent/profiles.py` 删除企业微信中的时间和计算工具。
- 从 API 的公开工具状态白名单删除时间和计算工具。

### 底层模块

删除只服务于这些工具的模块：

- 时间和计算工具的兼容模块与 `tools/utility` 实现。
- todo/reminder 的兼容模块、`tools/personal` 实现、`storage.py` 和
  `persistence/todos.py`、`persistence/reminders.py`。
- 文件工具的兼容模块与整个 `tools/files` 实现。

保留通用工具注册、权限分级、审批、Knowledge、Web Search 和 Skill
能力。`WRITE_LOCAL` 等通用风险类型保留，供未来工具使用。

### 配置与安全残留

- 从 `AppSettings` 删除 `todo_store_path`、`reminder_store_path`、
  `allowed_roots` 和 `normalized_allowed_roots()`。
- CLI 不再从配置创建文件访问白名单。
- 从 `ToolPermission` 删除文件工具专属的 `can_read_files`、
  `can_write_files` 和 `allowed_roots` 字段。
- 从 `PermissionPolicy` 删除 `allowed_roots` 状态和
  `is_path_allowed()`。
- `/tools` 输出不再展示文件读写字段。

通用的工具风险决策、覆盖策略和人工审批逻辑不变。

## 数据库迁移

`xiaoxu.db` schema 版本升级。数据库初始化在同一 SQLite 事务中执行：

```sql
DROP TABLE IF EXISTS todos;
DROP TABLE IF EXISTS reminders;
```

该迁移不可逆，会删除两个表中的全部现有数据。迁移不得删除或重建以下
数据：

- LangGraph checkpoint 表
- `model_state`
- `tool_grants`
- `audit_events`
- `schema_metadata`

新的数据库不再创建 `todos` 或 `reminders` 表。项目目录当前未发现
`todos.json`、`reminders.json` 或运行中的 `xiaoxu.db` 文件，因此本次
代码变更没有额外的项目内数据文件需要删除；部署中的数据库会在升级后
首次初始化时执行迁移。

## 测试策略

### 先失败

先更新或增加以下行为测试并确认其在旧实现上失败：

- CLI 工具集合严格等于三个保留工具。
- 企业微信工具集合严格等于两个保留工具。
- 新建数据库不含 `todos`、`reminders` 表。
- 含有旧表和示例数据的数据库初始化后，两个旧表被删除，其他 Agent
  状态表仍然存在。
- 配置和权限对象不再暴露文件工具专属字段。

### 再删除实现

删除生产模块和专属测试：

- `test_math_tools.py`
- `test_file_tools.py`
- `test_storage_tools.py`
- todo 持久化专属测试

更新仍用于测试通用流式输出、审批或权限覆盖的用例，将旧工具名替换为
仍存在的工具或明确的虚拟工具名，避免测试文本留下误导。

### 完整验证

- 运行 XiaoXu 全部 pytest。
- 运行 Python `compileall`。
- 全仓搜索 11 个旧工具名；除历史设计文档
  `docs/legacy/v1-plan.md` 外不得存在运行时代码、当前文档或测试引用。
- 检查删除模块不能再被导入。

## 非目标

- 不修改 checkpoint、对话摘要或未来 memory 设计。
- 不修改 Knowledge Service、Milvus 或 WxBot 项目。
- 不删除 `docs/legacy/v1-plan.md` 中的历史记录。
- 不增加替代工具。

## 风险与回滚

代码可以通过恢复旧版本回滚，但已经执行数据库迁移的 todo/reminder 数据
无法从应用数据库恢复。需要保留这些数据的部署必须在升级前自行备份
`xiaoxu.db`；本规格按用户选择执行不可逆删除，不在迁移中自动保留副本。
