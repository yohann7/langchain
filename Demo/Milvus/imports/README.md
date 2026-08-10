# imports

该目录包含三个用途分离的子目录：

- `imports/`：待导入文档，容器挂载为 `/imports:ro`，服务不修改原文件。
- `unsupported/`：批量扫描发现的不支持格式副本，容器挂载为 `/unsupported`。
- `failed/`：格式受支持但导入失败的文件副本，容器挂载为 `/failed`。

例如把项目资料放入 `imports/project-a/`，然后执行：

```powershell
docker compose exec -T knowledge knowledge-admin ingest-folder /imports/project-a
```

隔离副本将分别出现在 `unsupported/project-a/` 和 `failed/project-a/`，源文件始终保留。
