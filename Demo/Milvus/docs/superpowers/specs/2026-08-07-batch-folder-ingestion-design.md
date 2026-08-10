# Knowledge Service 批量目录导入设计

## 目标

为 Knowledge Service 的管理 CLI 增加递归批量导入能力。管理员可以指定
`/imports` 下的一个文件夹，批量导入其中所有支持的文档；不支持的文件和
导入失败的文件分别复制到独立的可写隔离目录，同时保留只读源文件。

本次只扩展 `knowledge-admin` CLI，不修改 HTTP API v1 契约。

## 目录与挂载

宿主机目录调整为：

```text
Demo/Milvus/imports/
├── imports/       # 待导入源文件
│   └── project-a/
├── unsupported/   # 不支持格式的隔离副本
└── failed/        # 支持格式但导入失败的隔离副本
```

Knowledge 容器挂载为：

```yaml
- ./imports/imports:/imports:ro
- ./imports/unsupported:/unsupported
- ./imports/failed:/failed
```

`KNOWLEDGE_ALLOWED_ROOTS` 继续只包含 `/imports`。批量导入只能扫描该只读根目录；
`/unsupported` 和 `/failed` 只用于保存隔离副本，不是可导入源目录。

## CLI 接口

新增命令：

```text
knowledge-admin ingest-folder SOURCE_DIRECTORY
    [--user-id USER_ID]
    [--knowledge-base KNOWLEDGE_BASE]
    [--unsupported-dir UNSUPPORTED_DIR]
    [--failed-dir FAILED_DIR]
```

默认值：

```text
user_id          = local-user
knowledge_base   = personal
unsupported_dir  = /unsupported
failed_dir       = /failed
```

最简批量命令：

```powershell
knowledge-admin ingest-folder /imports/project-a
```

现有单文件命令同时调整为默认使用 `local-user` 和 `personal`：

```powershell
knowledge-admin ingest /imports/manual.pdf
```

两个命令均允许通过现有可选参数覆盖 `user_id` 和 `knowledge_base`。`status`、
`export`、`restore`、`rebuild` 的参数规则不变。

## 组件边界

新增 `knowledge_service.batch_ingestion.BatchIngestionService`，负责目录级编排：

```python
BatchIngestionService.ingest_folder(
    *,
    owner_id: str,
    knowledge_base: str,
    source_dir: Path,
    unsupported_dir: Path,
    failed_dir: Path,
) -> BatchIngestResult
```

该服务负责：

- 校验源目录位于允许的 `/imports` 根目录内。
- 按相对路径排序递归扫描普通文件。
- 不跟随文件或目录软链接。
- 使用解析器现有的 `SUPPORTED_SUFFIXES` 分类文件。
- 调用现有 `IngestionService.ingest()` 处理支持格式，保持单文件事务行为不变。
- 复制不支持格式和导入失败文件，并保留相对目录。
- 汇总每个文件的结果和批次计数。

现有 `IngestionService`、HTTP 路由和请求/响应模型不改变目录语义。

## 数据流与隔离规则

给定 `/imports/project-a`：

1. 解析并严格校验源目录；非法、缺失、不是目录或越过允许根目录时立即失败。
2. 递归收集普通文件，按相对路径稳定排序；软链接不进入扫描目标。
3. 支持格式调用单文件导入，状态分别归入 `imported`、`unchanged` 或
   `duplicate`。
4. 不支持格式复制到 `/unsupported/project-a/<原相对路径>`。
5. 支持格式但在解析、脱敏、分块、向量化或持久化阶段失败时，复制到
   `/failed/project-a/<原相对路径>`，记录安全化错误，并继续处理其余文件。
6. 隔离目标已有同名文件时用当前源文件覆盖；原始源文件始终保留。
7. 创建隔离目录或复制隔离文件失败时记录为批次失败，不掩盖其它文件结果。

隔离根目录下使用源目录的最后一级名称作为批次目录。源目录为 `/imports`
本身时，批次目录名称固定为 `imports`。

## 输出与退出码

CLI 输出一个 JSON 对象，至少包含：

```json
{
  "status": "completed_with_failures",
  "source_directory": "/imports/project-a",
  "unsupported_directory": "/unsupported/project-a",
  "failed_directory": "/failed/project-a",
  "counts": {
    "scanned": 8,
    "imported": 4,
    "unchanged": 1,
    "duplicate": 0,
    "unsupported": 2,
    "failed": 1
  },
  "unsupported_files": [
    {
      "source": "/imports/project-a/archive.exe",
      "copied_to": "/unsupported/project-a/archive.exe"
    }
  ],
  "failed_files": [
    {
      "source": "/imports/project-a/broken.pdf",
      "copied_to": "/failed/project-a/broken.pdf",
      "error": "invalid PDF"
    }
  ]
}
```

宿主机对应目录为 `./imports/unsupported/project-a` 和
`./imports/failed/project-a`。

退出码规则：

- `0`：扫描完成且没有导入失败或隔离复制失败；存在不支持格式不影响退出码。
- `1`：至少一个支持格式文件导入失败、隔离复制失败，或源目录校验失败。

## 错误处理与安全约束

- 批量处理继续复用单文件导入的路径授权、大小限制、压缩包验证、脱敏、去重和
  事务激活规则。
- CLI 输出不包含文档内容、令牌、完整异常堆栈或其它敏感运行数据。
- 每个失败项只输出文件路径、隔离路径和稳定、安全化的错误信息。
- 扫描和复制均不跟随软链接，避免越过授权目录或写入隔离根目录之外。
- 目标路径由源文件相对于批次根目录的相对路径构造，并在复制前再次验证仍位于
  对应隔离批次目录中。

## 测试与验收

新增或修改测试覆盖：

- 递归扫描和稳定排序。
- 支持格式调用单文件导入并正确汇总三种成功状态。
- 不支持格式复制到 `unsupported`，同时保留源文件。
- 支持格式导入异常复制到 `failed`，且后续文件继续处理。
- 重复运行覆盖已有隔离副本。
- 源目录越界、缺失、非目录及软链接处理。
- 隔离复制失败的计数、输出和退出码。
- `ingest` 和 `ingest-folder` 的默认参数与覆盖参数。
- JSON 输出结构和批次失败退出码。
- Compose 的一个只读源挂载和两个读写隔离挂载。
- 完整测试、架构校验和 Compose 配置校验。

HTTP API v1 契约文件必须保持不变。

## 文档与部署

同步更新 `README.md`、`imports/README.md` 和 `docs/operations.md`，记录新目录、
默认命令、覆盖参数、隔离行为和宿主机路径映射。

Python 源码会被复制进 Knowledge 镜像，Compose 挂载也会改变，因此部署时必须：

```powershell
docker compose build knowledge
docker compose up -d --force-recreate knowledge
```

不需要重建或重新下载 Milvus 镜像，不删除 `milvus-data` 数据卷，也不需要重新
导入已经存在的有效知识。
