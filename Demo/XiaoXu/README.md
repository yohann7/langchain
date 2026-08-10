# XiaoXu

XiaoXu 是可独立运行的私人 Agent，提供 CLI 和 `/v1/runs` SSE API，
拥有模型、tools、skills、功能权限、按用户隔离的显式长期记忆与 Agent
自身 SQLite。

知识检索只有 `search_knowledge` 一个入口，并且只调用 Knowledge API；
XiaoXu 不包含 PyMilvus、文档 loader、embedding 或知识库写操作。

每个用户轮次都使用独立的检索协调状态。查询会规范化并阻止重复调用，
知识切片按 `(doc_id, chunk_id)` 去重，网页按规范化 URL 去重。两个搜索工具
分别从 `config/web-search.yaml` 与 `config/knowledge-search.yaml` 获取当前调用
的次数、结果数、超时和后端策略，并依据工具返回的 `remaining_queries`
决定是否继续；配置可热修改，无需重启 Agent。

```powershell
$env:PYTHONPATH="src"
D:\Anaconda3\envs\langchain1.2\python.exe -m pytest tests -q
D:\Anaconda3\envs\langchain1.2\python.exe -m private_agent.interfaces.cli.app
```

配置见 `docs/configuration.md`，长期记忆边界见 `docs/memory-design.md`。
