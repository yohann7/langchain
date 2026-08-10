# Private Agent Versioned Build Plan

## 1. Project Goal

本项目目标是基于 LangChain 1.2 构建一个专业、专用、规范、可长期迭代的私人助理 Agent。它应当先保证安全、可控、可运行，再逐步扩展为具备知识库、长期记忆、常驻运行、主流 Agent 命令、MCP/外部工具集成能力的私人助理。

除本地 CLI 外，`xiaoxu` 还需要提供稳定、受控的内部服务接口，供独立项目 `wxbot` 调用。`wxbot` 使用企业微信官方 `@wecom/aibot-node-sdk` 负责消息收发，`xiaoxu` 只负责对话分析、Agent 编排和回复生成。两个项目独立开发、独立打包，不通过 CLI 子进程耦合。

核心原则：

- 先安全，后能力。所有工具必须经过权限系统注册和调度，不能绕过权限直接执行。
- 先可运行，后复杂化。每个版本都必须能独立运行、测试、验收。
- 先本地 CLI，再建立最小服务边界。V1 保持本地 CLI 可用，V1.5 增加 FastAPI/SSE，并保证 CLI 和 API 复用同一个 Agent 核心。
- 通道与智能体解耦。企业微信协议、白名单、消息去重和回复渲染属于 `wxbot`；模型、工具、权限和会话状态属于 `xiaoxu`。
- 先内存闭环，后持久化。V1.5 不搭建 PostgreSQL，允许重启后丢失会话；接口从一开始支持注入 checkpointer 和存储后端。
- 空闲不调用模型。无明确任务时只做本地事件检查，避免 token 浪费和非预期行为。
- 开发阶段不访问 `learn/`。实现、测试、检索项目文件、生成索引时，开发者和构建流程不得访问、读取、修改或操作 `/Users/yohann/code/python/langchain/learn`。
- 最终 Agent 的目录访问由权限系统控制。最终交付的私人 Agent 不将 `learn/` 写死为永久禁区；它是否能访问任意目录，应由运行时权限配置、目录白名单和用户审批决定。

## 2. Environment And Dependency Rules

### 2.1 Python Environment

Python 解释器根据操作系统选择：

- Windows：`D:\Anaconda3\envs\langchain1.2\python.exe`
- Linux/macOS：`/opt/anaconda3/envs/langchain1.2/bin/python`

Windows 测试命令：

```powershell
& "D:\Anaconda3\envs\langchain1.2\python.exe" -m pytest
```

Linux/macOS 测试命令：

```bash
/opt/anaconda3/envs/langchain1.2/bin/python -m pytest
```

不得使用系统 Python、其他 conda 环境或隐式 `python` 命令。

### 2.2 Existing Core Dependencies

当前项目环境已经具备核心依赖：

- `langchain==1.2.12`
- `langgraph==1.1.2`
- `langchain-openai`
- `langchain-deepseek`
- `langchain-tavily`
- `langchain-mcp-adapters`
- `fastmcp`
- `typer`
- `rich`
- `pydantic-settings`
- `fastapi`
- `uvicorn`
- `pytest`

这些足以完成 V0、V1 和 V1.5 的核心功能。

### 2.3 Optional Dependencies

如下依赖只在对应功能启用时安装：

- PDF 文档解析：`pypdf`
- Word 文档解析：`python-docx`
- Excel/表格增强：`openpyxl`、`pandas`
- Milvus RAG：`langchain-milvus`、`pymilvus`

Windows 安装命令格式：

```powershell
& "D:\Anaconda3\envs\langchain1.2\python.exe" -m pip install <package>
```

Linux/macOS 安装命令格式：

```bash
/opt/anaconda3/envs/langchain1.2/bin/python -m pip install <package>
```

初版不安装重型文档解析栈，例如 `unstructured`、OCR、`torch`、本地深度学习推理依赖。只有明确需要复杂文档解析时再单独规划。

## 3. Overall Architecture

建议目录结构：

```text
private_agent/
  __init__.py
  cli.py
  api.py
  agent_runner.py
  runtime.py
  agent_factory.py
  commands.py
  config.py
  security.py
  audit.py
  memory.py
  rag.py
  scheduler.py
  models.py
  tools/
    __init__.py
    registry.py
    time_tools.py
    math_tools.py
    todo_tools.py
    reminder_tools.py
    file_tools.py
    search_tools.py
    memory_tools.py
    rag_tools.py
    mcp_tools.py
  docs/
    plan.md
tests/
  private_agent/
    test_commands.py
    test_security.py
    test_audit.py
    test_runtime.py
    test_tools.py
    test_memory.py
    test_rag.py
    test_api.py
    test_agent_runner.py
```

核心模块职责：

- `cli.py`：本地 CLI 入口，负责读取用户输入、展示输出、处理审批交互。
- `api.py`：内部 FastAPI/SSE 入口，负责鉴权、请求校验、流式事件和稳定错误响应。
- `agent_runner.py`：CLI 与 API 共用的 Agent 调用层，负责线程配置、流式事件转换、超时和中断处理。
- `runtime.py`：常驻运行状态机，管理 `idle`、`busy`、`awaiting_approval`、`paused`、`stopping` 等状态。
- `agent_factory.py`：用 LangChain 1.2 `create_agent` 创建 Agent，注入模型、工具、中间件、记忆组件。
- `commands.py`：本地 slash 命令解析。命令必须先由确定性代码处理，不能交给模型自由解释。
- `config.py`：配置加载和校验，包含模型、权限、目录、用量统计、外部服务密钥引用。
- `security.py`：权限模型、路径沙箱、工具风险分级、审批决策。
- `audit.py`：审计日志写入和脱敏。
- `memory.py`：短期记忆、长期记忆、用户偏好和项目规则。
- `rag.py`：知识库导入、切分、索引、检索和来源引用。
- `scheduler.py`：提醒、定时汇报、后台任务队列。
- `tools/registry.py`：工具注册中心，每个工具必须声明权限元数据。

### 3.1 Project Boundary

项目边界固定如下：

```text
D:\Code\Python\langchain\Demo\
  localcode\
    XiaoXu\                  # Python Agent、内部 API、SQLite schema
    WxBot\                   # Node.js/TypeScript 企业微信通道
    Milvus\                  # Milvus 配置和数据库运维源码
  docker\
    compose.yaml             # 三个容器的唯一运行编排
    XiaoXu\                  # Xiaoxu 运行配置
    WxBot\                   # wxbot 运行配置
    Milvus\                  # Milvus 持久化数据
```

- `wxbot` 通过 Docker 内网调用 `http://xiaoxu-api:8000/v1/runs`。
- `xiaoxu-api` 不依赖 `aibot-node-sdk`，也不理解企业微信原始 WebSocket 帧。
- `wxbot` 不导入 Python 代码，不解析 xiaoxu CLI 输出，不直接访问 xiaoxu 的本地状态文件。
- 当前 Compose 包含 `wxbot`、`xiaoxu-api` 和独立的 Milvus Standalone。
- SQLite 嵌入在 `xiaoxu-api` 中，通过 `xiaoxu-data` Volume 持久化，不创建独立容器。
- `xiaoxu-api` 仅映射到宿主机回环地址，不暴露到局域网或公网。

## 4. Permission Model

所有工具必须声明如下元数据：

```python
class ToolPermission:
    name: str
    risk: str  # read_safe | network_read | write_local | external_write | mcp | dangerous
    requires_approval: bool
    can_read_files: bool
    can_write_files: bool
    uses_network: bool
    allowed_roots: list[str]
    description: str
```

权限决策：

- `allow`：可直接执行，通常用于纯本地、低风险、只读操作。
- `ask`：执行前必须展示工具名、参数摘要、风险、预期副作用，由用户 approve/edit/reject。
- `deny`：默认拒绝执行。

默认风险规则：

- `read_safe`：默认 `allow`，但仍受目录白名单限制。
- `network_read`：默认 `ask`，用户可配置为 `allow`。
- `write_local`：默认 `ask`。
- `external_write`：默认 `ask`，涉及邮件、日历、消息、API 写操作。
- `mcp`：默认 `ask`。
- `dangerous`：默认 `deny`，包括支付、转账、删除系统文件、读取密钥、执行未授权 shell。

不同调用通道必须使用独立工具 profile：

- `cli` profile：沿用本地 CLI 的完整权限和人工审批流程。
- `wecom_chat` profile：V1.5 只暴露 `get_current_time` 和 `calculate_expression`。
- `wecom_chat` 不暴露文件、待办、提醒、联网搜索、MCP 和外部写工具。
- 微信通道第一版不实现卡片审批；任何需要 `ask` 的工具均视为不可用，不留下等待审批的挂起运行。

## 5. Version Roadmap

## V0: Safety Shell And Runtime Skeleton

### 5.1 Goal

构建一个不依赖大模型也能运行的安全底座。V0 的价值不是智能，而是建立所有后续能力必须经过的运行框架、权限系统、命令系统和审计系统。

### 5.2 User-Facing Functions

V0 完成后，用户可以：

- 启动本地 CLI。
- 查看 Agent 当前状态。
- 查看权限配置摘要。
- 查看 token/调用预算配置。
- 输入基础 slash 命令。
- 安全退出程序。

V0 不提供真正的模型对话，也不执行联网搜索、文件写入、MCP、RAG。

### 5.3 Commands

V0 实现以下命令：

- `/help`：列出可用命令和说明。
- `/status`：显示运行状态、当前线程、是否忙碌、是否等待审批。
- `/permissions`：显示当前权限策略、目录白名单、默认风险等级。
- `/usage`：显示按用户、UTC 日期累计的模型调用、工具调用和 token 用量。
- `/exit`：安全退出。

### 5.4 Implementation Work

需要完成：

- 创建 `private_agent` 包结构。
- 创建 `config.py`：
  - 读取 `.env` 和可选 YAML 配置。
  - 定义默认模型名、默认用户 ID、默认运行目录、默认权限策略。
  - 根据当前操作系统选择并校验 Windows 或 POSIX Python 环境路径。
- 创建 `commands.py`：
  - 解析以 `/` 开头的命令。
  - 未知命令返回明确错误，不交给模型处理。
- 创建 `runtime.py`：
  - 定义状态枚举：`idle`、`busy`、`awaiting_approval`、`paused`、`stopping`。
  - 提供状态查询和状态转换。
- 创建 `security.py`：
  - 定义 `ToolPermission`、`PermissionDecision`。
  - 实现风险等级到默认决策的映射。
  - 实现目录白名单检查。
- 创建 `audit.py`：
  - 写入 JSONL 审计日志。
  - 对密钥、邮箱、手机号、API key 做基础脱敏。
- 创建 `cli.py`：
  - 使用 Typer/Rich 建立 CLI。
  - 循环读取用户输入。
  - slash 命令走 `commands.py`。
  - 普通自然语言在 V0 中返回“Agent core not enabled yet”。

### 5.5 Tests And Acceptance

测试：

- `/help` 返回命令列表。
- `/status` 默认状态为 `idle`。
- `/exit` 将 runtime 状态置为 `stopping`。
- 未知 slash 命令返回错误。
- 权限系统默认拒绝 `dangerous`。
- 路径白名单能拒绝未授权目录。
- 审计日志会脱敏 API key 形态字符串。

验收标准：

- 能用指定 Python 环境启动 CLI。
- 不配置任何模型密钥也能运行 V0。
- 所有 V0 测试通过。
- 开发和测试过程不访问 `learn/`。

## V1: Minimum Usable Private Assistant

### 5.6 Goal

接入 LangChain 1.2 Agent，让系统成为真正可用的私人助理，但能力保持克制。V1 只实现低风险、高频、可测试的功能。

### 5.7 User-Facing Functions

V1 完成后，用户可以：

- 与 Agent 中文对话。
- 让 Agent 解释、总结、改写、生成计划。
- 查询时间、日期、星期。
- 做安全数学计算。
- 创建、查看、完成待办。
- 创建、查看、取消本地提醒。
- 在授权目录中只读列文件、读文本、检索文本。
- 请求联网搜索资料，并在审批后执行。
- 使用 `/compact` 压缩上下文。
- 使用 `/tools` 查看工具和权限等级。

### 5.8 LangChain Components

V1 使用：

- `create_agent`
- `HumanInTheLoopMiddleware`
- `PIIMiddleware`
- `SummarizationMiddleware`
- `ModelCallLimitMiddleware`
- `ToolCallLimitMiddleware`
- `ToolRetryMiddleware`

V1 可先使用内存 checkpointer，后续版本再切换持久化。

### 5.9 Tools

基础工具：

- `get_current_time()`：返回当前时间。
- `calculate_expression(expression: str)`：安全计算，只允许 AST 白名单数学表达式，不使用 `eval`。
- `create_todo(title: str, due_at: str | None, priority: str)`：创建待办。
- `list_todos(status: str | None)`：查看待办。
- `complete_todo(todo_id: str)`：完成待办。
- `create_reminder(title: str, remind_at: str)`：创建提醒。
- `list_reminders()`：查看提醒。
- `cancel_reminder(reminder_id: str)`：取消提醒。
- `list_files(root_id: str, path: str)`：列出授权目录下文件。
- `read_text_file(root_id: str, path: str)`：读取授权目录下文本文件。
- `search_text_files(root_id: str, query: str)`：在授权目录中检索文本。
- `web_search(query: str)`：联网搜索，默认审批。

### 5.10 Commands

V1 新增：

- `/clear`：清空当前会话短期上下文。
- `/compact`：摘要压缩上下文。
- `/tools`：列出工具、风险等级、是否需要审批。

### 5.11 Security Requirements

V1 必须满足：

- 所有工具只能通过工具注册中心暴露给 Agent。
- 工具执行前统一经过权限网关。
- `web_search` 默认 `ask`。
- 文件工具默认只读。
- 文件工具只访问用户配置的目录白名单。
- 开发阶段仍不得访问 `learn/`。
- 最终 Agent 不把 `learn/` 写死为禁区；访问能力由权限配置决定。
- 任何写操作必须进入审批。

### 5.12 Persistence

V1 本地持久化：

- 待办：JSONL 或 SQLite。
- 提醒：JSONL 或 SQLite。
- 审计：JSONL。
- 配置：YAML + `.env`。

V1 不要求 Postgres。

### 5.13 Tests And Acceptance

测试：

- `create_agent` 能加载基础工具。
- 普通中文问题能得到模型回复。
- 计算器拒绝函数调用、属性访问、导入、文件访问等表达式。
- 创建待办后能列出。
- 完成待办后状态变化。
- 创建提醒后能列出。
- 文件工具拒绝越界路径。
- 联网搜索触发审批。
- approve 后工具执行，reject 后不执行，edit 后使用编辑后的参数执行。
- `/compact` 后仍保留最近必要上下文。

验收标准：

- V1 能作为日常轻量私人助理使用。
- 空闲时不调用模型。
- 高风险工具不能绕过审批。
- 所有 V1 测试通过。

## V1.5: Service API And WeCom Bridge

### 5.14 Goal

把 `xiaoxu` 从只能由本地 CLI 使用的 Agent，扩展为可以被独立 `wxbot` 项目安全调用的内部服务。该版本只建立文本和语音转写文本的对话闭环，不引入 PostgreSQL、图片分析、文件处理或微信内工具审批。

### 5.15 Service Interface

新增内部接口：

```http
POST /v1/runs
Authorization: Bearer <WXBOT_XIAOXU_TOKEN>
Content-Type: application/json
Accept: text/event-stream
```

请求体：

```json
{
  "request_id": "wecom-msgid",
  "thread_id": "wecom:dm:hmac-route-id",
  "actor_id": "hmac-user-id",
  "channel": "wecom",
  "conversation_type": "single",
  "message": {
    "type": "text",
    "text": "用户输入"
  }
}
```

SSE 事件：

- `run.started`：返回本次运行 ID。
- `response.delta`：返回新增回答文本。
- `tool.status`：只返回允许公开的工具名和状态，不返回敏感参数。
- `run.completed`：返回最终完整文本。
- `run.failed`：返回稳定错误码、可展示信息和是否可重试。
- `approval.required`：为后续版本保留；V1.5 不产生该事件。

健康检查：

- `GET /health/live`：进程存活。
- `GET /health/ready`：模型配置完成且 Agent 可创建。

### 5.16 Agent Runner Refactor

实现要求：

- 提取 `AgentRunner`，让 CLI 和 FastAPI 复用 Agent 创建、线程配置和流式解析逻辑。
- `create_private_agent()` 接收可选 checkpointer；未传入时使用 `InMemorySaver`。
- API 通过请求中的 `thread_id` 隔离会话，不使用全局固定 `settings.thread_id`。
- 运行状态按请求创建，禁止多个用户共享一个可变 `RuntimeState`。
- API 使用异步流式调用；客户端断开或超过 120 秒时取消 Agent 运行。
- 不向 API 输出模型思维链、系统提示、异常栈、密钥或完整工具参数。
- 输入文本最大 8 KiB，输出最大 20,000 字节。
- `wecom_chat` profile 每次运行最多 4 次模型调用和 4 次工具调用。

### 5.17 wxbot Calling Contract

`wxbot` 的固定调用流程：

1. 使用 `aibot-node-sdk` 接收企业微信文本或语音消息。
2. 完成白名单、消息格式、长度和重复消息检查。
3. 为单聊或群聊生成 HMAC 化的 `thread_id`。
4. 通过 Docker 内网向 `/v1/runs` 发起 HTTP POST。
5. 解析 SSE，将 `response.delta` 累积为完整回答。
6. 使用 `replyStreamNonBlocking()` 发送中间内容。
7. 使用 `replyStream(..., finish=true)` 保证最终帧发送。

单聊按用户隔离线程，群聊按群 ID 共享线程；单聊与群聊上下文不得互通。`wxbot` 只处理白名单用户和群聊，群聊仅在企业微信明确 `@机器人` 或引用机器人时触发。

### 5.18 Authentication And Network

- `wxbot` 和 `xiaoxu-api` 使用随机内部 Bearer Token 鉴权。
- Token 只能通过环境变量或 Docker secret 文件注入，不得提交到 Git。
- `xiaoxu-api` 在 Docker 内使用 `expose: 8000`，并只映射到宿主机
  `127.0.0.1:8000` 供本地客户端使用，不绑定局域网或公网地址。
- 日志使用 `request_id`、`run_id` 和 HMAC 后的会话标识关联请求。
- 日志不得记录消息正文、完整回复、企业微信 secret、模型 API key 或完整请求头。
- 未鉴权请求返回 `401`；无权限工具不注册到微信 profile，不能依赖提示词约束。

### 5.19 Temporary In-Memory Limitations

V1.5 明确接受以下限制：

- xiaoxu 使用 `InMemorySaver`，容器重启后对话上下文丢失。
- wxbot 首版在内存中保存消息去重、限流和会话代数，重启后这些状态丢失。
- 只允许单个 wxbot 实例连接同一个企业微信 Bot。
- 进程崩溃时，正在生成的回复不保证恢复。
- `/new` 通过更换当前进程中的线程代数开启新会话。

存储访问必须通过抽象接口完成，后续可将 checkpointer、消息去重和会话映射替换为 PostgreSQL，而不改变 `/v1/runs` 契约。

### 5.20 Tests And Acceptance

xiaoxu 测试：

- 未鉴权请求返回 `401`。
- 非法请求、超长输入和不支持的消息类型被拒绝。
- SSE 事件顺序稳定且只产生一个结束事件。
- 不同 `thread_id` 的上下文不互相污染。
- `wecom_chat` 只能调用时间和计算器工具。
- 客户端断开和 120 秒超时能够取消运行。
- CLI 原有调用、审批和测试保持可用。

wxbot 集成测试：

- 文本和语音转写文本可以完成端到端回复。
- 同一企业微信 `msgid` 在进程生命周期内只处理一次。
- 同一会话串行执行，不同会话允许受限并发。
- SSE 增量能够转换为企业微信累计流式回复。
- 中间帧可以跳过，但最终 `finish=true` 帧不能丢失。
- 未授权用户、非白名单群和非触发群消息不会调用 xiaoxu。
- xiaoxu 超时或不可用时返回脱敏的统一错误提示。

验收标准：

- Docker 中只启动 `wxbot` 和 `xiaoxu-api` 即可完成企业微信对话闭环。
- 文本和语音消息可进入 xiaoxu，并通过企业微信流式返回。
- 重启导致的内存状态丢失有明确文档说明。
- 企业微信通道不能扩大 xiaoxu 的原有工具权限。
- 所有 V1.5 自动化测试通过。

## V2: Knowledge Base And Long-Term Memory

### 5.21 Goal

让 Agent 拥有可控的长期记忆和知识库能力。V2 的重点是“知道用户和资料”，但仍然要保证隐私、来源和注入防护。

V2 的数据库和检索能力全部属于 `xiaoxu`：

- `wxbot` 仍然只是 `/v1/runs` 的 API 客户端，不直接连接 SQLite 或 Milvus。
- `xiaoxu` 升级记忆、RAG、模型或工具时，只要 API 契约保持兼容，`wxbot` 无需修改或重新配置。
- SQLite 保存长期记忆、文档登记、导入任务和审计状态。
- Milvus 保存文档分块和向量，负责相似度检索。
- 短期对话状态继续使用当前 checkpointer；V2 不把会话状态、长期记忆和 RAG 文档混为一种数据。
- V2 不引入 PostgreSQL。单实例运行时使用 SQLite；只有未来出现多实例并发写入需求时才评估迁移。

### 5.22 User-Facing Functions

V2 完成后，用户可以：

- 保存个人偏好和项目规则。
- 查看、修改、删除长期记忆。
- 导入本地资料到知识库。
- 对知识库提问。
- 获得带来源片段的回答。
- 使用 `/memory` 管理记忆。
- 使用 `/rag` 管理知识库。

### 5.23 Long-Term Memory

记忆分类：

- `profile`：用户偏好，例如语言、回答风格、常用工具。
- `project_rules`：项目规则，例如 Python 环境、开发限制、测试命令。
- `facts`：用户明确要求记住的事实。
- `blocked`：用户明确要求不要保存或不要使用的信息。

记忆写入规则：

- 低敏偏好可自动建议保存，但应告知用户。
- 敏感信息默认不保存。
- API key、密码、token、银行卡、身份证等不得保存。
- 用户可以用 `/memory delete` 删除记忆。
- V2 第一阶段只接受用户明确发起的记忆写入，不静默从普通聊天中自动提取并保存。
- SQLite 数据库默认位于 `run_dir/xiaoxu.db`；Docker 部署时使用持久化路径 `/data/xiaoxu.db`。
- 每条记忆必须按可信 `user_id`、类别、作用域和主题隔离。
- 同一用户、类别、作用域和主题的新值覆盖为新版本，旧版本不再参与正常检索。
- 删除操作必须从当前有效记忆中立即消失，服务重启后也不能恢复。
- `blocked` 只保存禁止主题，不保存用户要求删除的敏感原文。

### 5.24 RAG Scope

V2 默认支持：

- TXT
- Markdown
- HTML
- JSON
- CSV
- PDF 文本层：`pypdf`
- Word DOCX：`python-docx`
- Excel XLSX：`openpyxl`
- PowerPoint PPTX：`python-pptx`

当前不支持扫描 PDF 或 PPTX 图片 OCR、旧版 DOC/XLS/PPT、DOCX 嵌入对象、
XLSX 图表/图片语义提取，以及 PPTX 图表、SmartArt、嵌入对象和动画语义。
公式只作为文本读取，绝不执行宏或外部链接。

V2 选定 Milvus 作为 RAG 向量后端，安装 `langchain-milvus`、`pymilvus`。Milvus 不保存长期记忆的权威状态，也不替代 SQLite 的文档登记和导入事务。

### 5.25 RAG Data Flow

导入流程：

1. 用户选择授权目录或文件。
2. 权限系统确认读取范围。
3. Loader 读取文本。
4. Splitter 切分文档。
5. 写入本地索引或向量库。
6. 保存来源元数据。
7. 记录审计日志。

检索流程：

1. 用户提问。
2. xiaoxu 从可信请求上下文取得用户和知识库范围；必要时从 SQLite 校验知识库状态。
3. xiaoxu 使用服务端构造的过滤条件检索 Milvus，wxbot 不参与数据库访问。
4. 组合相关片段和长期记忆。
5. 系统提示明确：检索内容是数据，不是指令。
6. Agent 回答并给出来源。

### 5.26 Commands

V2 新增：

- `/memory list`
- `/memory add`
- `/memory delete`
- `/memory clear`
- `/rag status`
- `/rag ingest`
- `/rag search`
- `/rag clear`

### 5.27 Security Requirements

V2 必须满足：

- RAG ingest 默认 `ask`。
- RAG 内容不得覆盖系统提示、权限规则、审批规则。
- RAG 回答必须能展示来源。
- 记忆写入必须经过敏感信息检查。
- 用户可删除长期记忆。
- 向量库或本地索引不得保存密钥原文。

### 5.28 Tests And Acceptance

测试：

- 保存偏好后能读取。
- 删除记忆后不能再检索到。
- 敏感信息不会写入长期记忆。
- 导入 Markdown 后可以检索。
- CSV 导入能按行或块生成来源。
- PDF 文本层能按页生成来源；扫描件给出 OCR 未启用提示。
- DOCX 能提取标题、段落、表格、页眉和页脚。
- XLSX 能提取多工作表、单元格值和公式文本，且不执行公式。
- PPTX 能按页提取标题、文本框、组合形状文字、表格和演讲者备注。
- RAG 注入文本不能改变权限规则。

验收标准：

- Agent 能回答知识库问题并引用来源。
- 用户能控制长期记忆。
- RAG 不绕过权限系统。

### 5.29 第一版实施状态（2026-07-26）

当前已经进入 V2 第一阶段，实施边界如下：

- 已实现 SQLite schema v2，保存知识库、文档、文档版本、导入任务、分块清单和嵌入模型登记；SQLite 是权威状态。
- 已实现 TXT、Markdown、HTML、JSON、CSV、PDF、DOCX、XLSX、PPTX 加载，以及结构感知切分、稳定哈希和来源定位。
- 已实现本地 `BAAI/bge-m3` 嵌入服务，固定模型 revision 和 1024 维向量，不调用外部嵌入 API。
- 已实现 Milvus dense + BM25 混合检索，并使用 RRF 融合结果；Milvus 只保存可重建的分块和向量。
- 已实现 staging、强一致校验、版本激活和失败隔离。只有 SQLite 中当前激活的文档版本能参与检索。
- 已实现持久化前敏感值打码：托管副本、嵌入文本和 Milvus 分块只接收打码结果；剩余非敏感有效字符不足阈值的文件整份拒绝。
- 已实现 `/rag status`、`/rag list`、`/rag search`；文档导入通过 `ingest_document` 工具并要求人工审批。
- 企业微信通道只开放 RAG 读取能力，不开放导入和删除能力。
- 检索片段按不可信数据处理，返回文件、行号和分块引用。
- 第一版暂不暴露永久删除/清空接口；需要先定义备份、软删除和恢复策略，再单独授权实施。
- PDF、DOCX、XLSX、PPTX 文本内容已进入本阶段；OCR、旧版 DOC/XLS/PPT、多模态检索和自动记忆提取仍不在本阶段范围内。

聊天记忆边界：

- 普通聊天首先只进入当前会话 checkpointer，不自动成为长期记忆或知识库文档。
- 第一阶段只有用户明确表达“记住、以后遵循、保存为偏好”等持久化意图时，才生成长期记忆候选并要求确认。
- 经确认的简短偏好、事实和项目规则写入 SQLite `memories` 表；普通聊天不写入 Milvus。
- 用户明确要求把长文本、文件或资料作为可检索知识时，才走 RAG 导入流程，同时写入 SQLite 元数据和 Milvus。
- 密钥、令牌、密码、支付信息等禁止进入长期记忆；一般敏感长期记忆仍需单独明确确认。

本阶段完成条件：

- 自动化测试全部通过。
- Docker 中 `xiaoxu-api`、`xiaoxu-embedding`、Milvus 和 wxbot 健康运行。
- 使用真实本地 BGE-M3 完成一次无残留的导入、混合检索和引用端到端验收。

## V3: Always-On Runtime, Rewind, MCP, And Integrations

### 5.30 Goal

让 Agent 具备主流 Agent 的高级操作能力：常驻、定时汇报、任务队列、回退、MCP 集成、模型 fallback 和工具重试。

### 5.31 User-Facing Functions

V3 完成后，用户可以：

- 让 Agent 常驻运行。
- 创建周期性汇报。
- 管理后台任务队列。
- 回退会话到历史 checkpoint。
- 分叉历史会话继续执行。
- 接入 MCP 工具。
- 查看模型调用和 token 用量。
- 使用备用模型自动恢复。

### 5.32 Always-On Runtime

状态规则：

- `idle`：无任务，不调用模型，只检查本地事件。
- `busy`：有明确用户任务，正常调用模型和工具。
- `awaiting_approval`：等待用户审批，不能继续执行相关工具。
- `paused`：暂停后台任务，不触发模型调用。
- `stopping`：安全退出。

空闲规则：

- 空闲时不得自发调用模型。
- 定时任务到期时，先本地判断是否有可汇报变化。
- 没有变化时只输出本地状态，不生成模型总结。
- 有变化且配置允许时才调用模型生成汇报。

### 5.33 Rewind

`/rewind` 支持：

- 列出 checkpoint。
- 选择 checkpoint 回退。
- 从 checkpoint 分叉新会话。
- 查看回退影响范围。

限制：

- 对话状态可回退。
- Agent 自己写入且有快照的本地文件可尝试回滚。
- 外部副作用，例如已发送邮件、已调用外部 API，不自动回滚，只记录和提示。

### 5.34 MCP

MCP 能力：

- 从配置读取 MCP server。
- 发现 MCP tools。
- 转换为 LangChain tools。
- 统一接入权限网关。

默认策略：

- 所有 MCP 工具默认 `ask`。
- 未知 MCP 工具不得自动执行。
- MCP 工具说明和参数必须展示给用户审批。

### 5.35 Commands

V3 新增：

- `/schedule list`
- `/schedule add`
- `/schedule delete`
- `/pause`
- `/resume`
- `/rewind list`
- `/rewind to`
- `/rewind fork`
- `/mcp list`
- `/mcp enable`
- `/mcp disable`
- `/model`

### 5.36 Reliability

V3 实现：

- `ModelRetryMiddleware`
- `ToolRetryMiddleware`
- `ModelFallbackMiddleware`
- 模型调用上限
- 工具调用上限
- 每用户、每日 token 统计
- 错误分类和恢复建议

### 5.37 Tests And Acceptance

测试：

- 空闲循环不调用模型。
- 到期提醒能触发本地通知或 CLI 输出。
- 定时汇报无变化时不调用模型。
- `/rewind list` 能列出 checkpoint。
- `/rewind to` 能恢复会话状态。
- MCP 工具默认触发审批。
- 模型失败时 fallback 生效。
- token 用量只记录和审计，不拒绝新的模型或工具任务。

验收标准：

- Agent 能长期运行而不无故消耗 token。
- 回退功能不会假装撤销外部副作用。
- MCP 不能绕过权限系统。

## V4: Production Hardening And Ecosystem Expansion

### 5.38 Goal

把 Agent 从个人项目提升为长期可靠的私人助理系统，强化可观测性、多 profile、外部服务集成和运维能力。

### 5.39 User-Facing Functions

V4 完成后，用户可以：

- 使用多个 profile。
- 为不同场景设置不同权限策略。
- 接入邮件、日历、Notion、GitHub、浏览器等外部服务。
- 查看任务历史、错误历史、成本统计。
- 导出对话、任务、记忆、审计摘要。
- 在 V1.5 FastAPI/SSE 基础上使用持久化、多实例和更完整的服务治理能力。

### 5.40 External Integrations

候选集成：

- Email：读邮件、草拟邮件、审批后发送。
- Calendar：查看日程、草拟日程、审批后创建或修改。
- Notion：读取页面、写入笔记，默认审批。
- GitHub：读取 issue/PR，评论和修改默认审批。
- Browser：网页读取、摘要、信息抽取；表单提交默认审批。

所有外部写操作默认 `ask`。

### 5.41 Observability

实现：

- 模型调用统计。
- token 成本统计。
- 工具调用统计。
- 审批统计。
- 错误分类统计。
- RAG 命中率和来源统计。
- 每日/每周运行报告。

### 5.42 API Mode

V4 对 V1.5 的内部服务进行生产强化：

- 保持 `/v1/runs` 请求和 SSE 事件向后兼容。
- 增加 PostgreSQL checkpointer、消息幂等和会话生命周期管理。
- 支持服务实例横向扩展、限流、指标、追踪和优雅停机。
- 支持密钥轮换和更细粒度的调用方身份。
- 高风险 API 调用仍需审批，不因服务化绕过权限网关。

### 5.43 Tests And Acceptance

测试：

- 多 profile 互不污染记忆。
- 外部写操作必须审批。
- API 未鉴权请求被拒绝。
- API 模式不能绕过权限网关。
- 成本统计与实际调用一致。
- 导出功能不泄露密钥。

验收标准：

- Agent 能作为长期私人助理使用。
- 用户能审计它做过什么。
- 外部集成可控、可撤销授权、可关闭。

## 6. Command Roadmap

| Version | Commands |
| --- | --- |
| V0 | `/help`, `/status`, `/permissions`, `/usage`, `/exit` |
| V1 | `/clear`, `/compact`, `/tools` |
| V1.5 | xiaoxu API 无新增 slash 命令；wxbot 提供 `/help`, `/new`, `/status` |
| V2 | `/memory list`, `/memory add`, `/memory delete`, `/memory clear`, `/rag status`, `/rag ingest`, `/rag search`, `/rag clear` |
| V3 | `/schedule list`, `/schedule add`, `/schedule delete`, `/pause`, `/resume`, `/rewind list`, `/rewind to`, `/rewind fork`, `/mcp list`, `/mcp enable`, `/mcp disable`, `/model` |
| V4 | `/profile`, `/export`, `/integrations`, `/report` |

## 7. Feature Summary By Version

### V0 Features

- CLI shell
- Runtime state machine
- Permission model
- Tool metadata model
- Audit log
- Local command parser
- Config loading
- Development-stage `learn/` isolation

### V1 Features

- LangChain 1.2 Agent
- Conversation
- Summarization
- Planning
- Time tool
- Safe calculator
- Todo tool
- Reminder tool
- Read-only file tools
- Search tool with approval
- HITL approval
- PII redaction
- Context compaction
- Idle zero model calls

### V1.5 Features

- Shared `AgentRunner` for CLI and API
- Internal FastAPI endpoint
- SSE streaming response
- Bearer token authentication
- Per-thread conversation isolation
- `wecom_chat` restricted tool profile
- Text and transcribed-voice input contract
- Docker-internal wxbot integration
- In-memory checkpointer and documented restart limitations

### V2 Features

- Long-term memory
- Memory commands
- Local RAG
- Source-cited answers
- RAG injection resistance
- Optional PDF/Word/Excel parsing
- Milvus Standalone backend

### V3 Features

- Always-on scheduler
- Periodic reports
- Background task queue
- Rewind and fork
- MCP integration
- Model fallback
- Tool/model retry
- Token budget
- Call limits

### V4 Features

- Multiple profiles
- External integrations
- FastAPI/SSE mode
- Cost dashboard
- Error history
- Task history
- Export
- Production hardening

## 8. Development Order

Recommended order:

1. Implement V0 fully.
2. Write tests for V0 and verify the security skeleton.
3. Implement V1 without RAG, MCP, or external writes.
4. Use V1 daily enough to expose workflow issues.
5. Refactor the shared `AgentRunner` without changing CLI behavior.
6. Implement and test the V1.5 FastAPI/SSE service locally.
7. Implement `wxbot` under `Demo\localcode\WxBot`, then verify the API loop.
8. Connect a white-listed enterprise WeChat user; add group testing only after direct messages are stable.
9. Implement V2 memory first, then RAG.
10. Add optional document dependencies only when the base RAG path works.
11. Implement V3 scheduler and rewind before MCP.
12. Add MCP only after permissions and approval flows are stable.
13. Implement V4 persistence and production hardening after the in-memory service path is stable.

## 9. Non-Goals For Early Versions

V0 and V1 should not include:

- Payment, transfer, order placement.
- Automatic shell execution.
- Automatic external writes.
- Browser automation.
- FastAPI service mode.
- OCR or heavy document parsing.
- Multi-agent orchestration.
- Complex plugin marketplace.

These are intentionally deferred to avoid increasing the attack surface before the permission system is proven.

V1.5 should not include:

- PostgreSQL, Redis or a message queue.
- Image, file or video analysis.
- Enterprise WeChat template-card approvals.
- `wecom-cli` tools or enterprise WeChat document/todo/calendar operations.
- Multiple wxbot replicas or multiple Bot accounts.
- Publicly exposed xiaoxu API ports.

## 10. Definition Of Done

Each version is done only when:

- The version can be started with the fixed Python environment.
- Version-specific commands work.
- Version-specific tests pass.
- Security tests pass.
- High-risk tools cannot bypass approval.
- Audit records are written for tool calls.
- No development or test process accesses `learn/`.
- Documentation is updated to describe user-facing behavior and limitations.

V1.5 additionally requires:

- CLI and API use the same Agent core without parsing subprocess output.
- `/v1/runs` authentication, schema and SSE contract tests pass.
- `wecom_chat` cannot access tools outside its explicit allowlist.
- wxbot can complete text and voice conversations through Docker internal networking.
- Restart-related loss of context and deduplication state is clearly documented until persistent storage is introduced.
