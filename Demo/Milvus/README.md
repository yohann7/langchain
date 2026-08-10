# XiaoXu Knowledge Service（GPU / Milvus）

这是在 `Demo/Milvus` 中重新实现的知识库服务。它从空库启动，不包含旧项目的 imports、SQLite、Milvus volume、模型缓存、密钥或备份；源项目不参与运行。

核心约束：

- `BAAI/bge-m3` 固定 revision，输出 1024 维归一化 Dense 向量，只允许 RTX 4070 Laptop 的 `cuda:0`；OOM 时 batch 从 4 依次减半到 1，仍失败即任务失败。
- SQLite schema v3 管理用户隔离、知识库、文档、活动版本和请求幂等；Milvus 保存 chunk、Dense、BM25 sparse 和检索元数据。
- 支持 TXT、Markdown、HTML、JSON、CSV、PDF、DOCX、XLSX、PPTX，统一执行路径授权、资源限制、脱敏、分块和安全解析。
- 查询使用 GPU Dense + Milvus BM25，再以 RRF 融合；所有查询都过滤用户、知识库及 SQLite 活动版本。
- 默认检索返回 `top_k=10` 个切片，并从 Dense/BM25 各自最多 `candidate_limit=50` 的候选中融合；请求显式 `limit` 仍限制为 `1～20`。
- `imports/imports` 只读；批量导入会把不支持和失败文件的副本分别写入 `imports/unsupported` 与 `imports/failed`。运行数据位于 `runtime/knowledge`，Milvus 索引位于 Docker named volume `milvus-data`。
- HTTP `/health/*` 与 `/v1/knowledge/*` 使用 v1 路径；搜索命中和来源以必需的 `doc_id`、`chunk_id` 标识，不再输出旧 `source_id`。内部 schema 和备份格式不兼容旧项目。

## 快速开始

```powershell
Copy-Item .env.example .env
# 编辑 .env 中两个互不相同的 Token
docker compose build
docker compose up -d
Invoke-RestMethod http://127.0.0.1:8080/health/ready
```

将文档放入 `imports/imports/project-a/` 后，可以使用默认用户 `local-user` 和默认知识库 `personal` 递归导入：

```powershell
docker compose exec -T knowledge knowledge-admin ingest-folder /imports/project-a
```

不支持格式的副本位于 `imports/unsupported/project-a/`，格式受支持但处理失败的副本位于 `imports/failed/project-a/`；只读源文件不会被移动或删除。单文件导入可使用：

```powershell
docker compose exec -T knowledge knowledge-admin ingest /imports/manual.pdf
```

修改 Knowledge Python 代码或 Compose 挂载后，需要执行 `docker compose build knowledge` 和 `docker compose up -d --force-recreate knowledge`；不需要重建 Milvus 或删除其数据卷。

需要 Docker Desktop、Linux 容器、NVIDIA GPU 容器支持，以及首次构建时的外网访问。详细说明见 [架构](docs/architecture.md)、[API](docs/api.md) 和 [运维](docs/operations.md)。

## 本地验证

普通测试使用假 Embedding 和假 Milvus，不要求 CUDA：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='src'
python -m pytest
python scripts/validate_architecture.py
```

真实容器验证必须额外检查：模型参数位于 CUDA、向量维度与归一化、显存占用、所有格式导入、混合搜索、OOM 降批、备份恢复及 Milvus 丢失后的全量重建。
