# 配置

静态样例位于 `config/`，密钥只通过环境变量注入。核心变量包括
`PRIVATE_AGENT_API_TOKEN`、`PRIVATE_AGENT_IDENTITY_SECRET`、
`PRIVATE_AGENT_KNOWLEDGE_API_URL` 和
`PRIVATE_AGENT_KNOWLEDGE_API_TOKEN`。

模型目录由 `PRIVATE_AGENT_MODEL_CATALOG_PATH` 指定，默认读取
`config/model-catalog.yaml`。该文件只保存非敏感模型元数据；API key 和
base URL 仍从目录中声明的环境变量名读取。

摘要默认复用当前实际选中的聊天模型，也可用
`summarization_model_name` 单独指定。触发与保留窗口使用
`summarization_trigger_tokens` 和 `summarization_keep_tokens`，不再按固定
消息数触发；保留窗口必须小于触发窗口。

Skill 的三项大小上限为 `skill_max_frontmatter_bytes`、
`skill_max_instructions_bytes` 和 `skill_max_resource_bytes`。

显式长期记忆限制由 `memory_max_content_bytes`、
`memory_max_query_bytes`、`memory_max_items_per_user` 和
`memory_max_results` 控制。长期记忆保存在 Agent 自有 `xiaoxu.db`，
不会读取或写入 Knowledge Service 数据库。

## 搜索配置

网页搜索与知识库搜索的非敏感行为参数分别位于
`config/web-search.yaml` 和 `config/knowledge-search.yaml`。`AppSettings` 只保存
这两个路径；可通过 `PRIVATE_AGENT_WEB_SEARCH_CONFIG_PATH` 和
`PRIVATE_AGENT_KNOWLEDGE_SEARCH_CONFIG_PATH` 指向其它文件。SearXNG URL、
Knowledge URL/Token 和 Tavily Key 仍通过环境变量提供。

`web-search.yaml` 配置每个用户轮次的逻辑查询上限、每个查询的返回上限、请求
超时、SearXNG 后端尝试次数与退避序列，以及 Tavily fallback 开关。退避数组
长度必须等于尝试次数减一。`knowledge-search.yaml` 配置用户轮次查询上限、
默认/最大返回数和请求超时；默认返回数不能大于最大返回数，最大返回数不能
超过 Knowledge API 的协议上限 `20`。

“用户轮次查询”是主模型发起的一次工具调用；一次 `web_search` 内部可能按
配置多次请求 SearXNG，但这些后端尝试仍只消耗一个逻辑查询。全局
`max_tool_calls_per_run` 继续作为所有工具调用的最终上限。

中间件在每次搜索工具调用前以 UTF-8 读取一次对应 YAML，并把同一个不可变
配置对象交给本次工具执行，不使用进程缓存或上次有效值。修改文件后，下一次
调用立即生效，无需重启；当前调用不会因执行期间文件变化而改变。配置缺失、
为空、YAML 非法或校验失败时，本次不访问后端、不消耗查询次数，并关闭对应
搜索类型直到当前用户轮次结束。

维护配置时应先在同一目录写入完整临时文件，校验后再用 `os.replace` 原子替换
目标 YAML，避免 Agent 读到半写文件。Docker 镜像已经复制整个 `config/`；如需
在部署后外部维护，可把配置文件只读挂载到容器，并通过上述两个路径环境变量
指向挂载位置。

Knowledge Service 内部的 `candidate_limit=50` 仍由 Knowledge 自己管理，不迁入
XiaoXu，也不由 XiaoXu 覆盖。
