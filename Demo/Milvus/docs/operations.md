# 部署与运维

## 首次启动

1. 将 `.env.example` 复制为 `.env`，为普通和管理员 Token 设置两个独立随机值。
2. 将待导入文件放入 `imports/imports` 下的项目文件夹。
3. 执行 `docker compose build`，构建阶段会下载固定 revision 的 BGE-M3 和 CUDA 13.0 PyTorch wheel。
4. 执行 `docker compose up -d`。
5. 检查 `docker compose ps`、`docker compose logs knowledge` 与 `http://127.0.0.1:8080/health/ready`。

构建完成后，运行镜像启用 Hugging Face/Transformers 离线模式，不会把模型写入 `runtime`。CUDA、模型参数设备或最小向量探针失败会直接终止 Knowledge 容器，不会回退 CPU。

## 管理 CLI

容器内提供以下命令：

```text
knowledge-admin status --user-id alice
knowledge-admin ingest /imports/manual.pdf
knowledge-admin ingest-folder /imports/project-a
knowledge-admin export /data/transfers/backup.zip
knowledge-admin restore /data/transfers/backup.zip
knowledge-admin rebuild
```

`ingest` 和 `ingest-folder` 默认使用 `--user-id local-user` 与
`--knowledge-base personal`。批量命令还默认使用
`--unsupported-dir /unsupported` 和 `--failed-dir /failed`；只有需要覆盖默认值时
才传入这些参数：

```powershell
knowledge-admin ingest-folder /imports/project-a `
  --user-id alice `
  --knowledge-base project-docs `
  --unsupported-dir /unsupported `
  --failed-dir /failed
```

批量导入递归扫描普通文件且不跟随软链接。支持格式逐个进入现有单文件事务；
不支持格式复制到宿主机 `imports/unsupported/<源文件夹>/`，导入失败文件复制到
`imports/failed/<源文件夹>/`，两者都保留源文件和相对目录。某个文件失败不会
阻止后续文件；存在导入失败或隔离复制失败时命令退出码为 1，仅有不支持格式时
退出码仍为 0。CLI 的 JSON 会输出各文件及隔离目录地址。

CLI 的 `restore` 会在同一个独占维护窗口中立即执行 GPU 全量重建；API 则保留 v1 的 restore/rebuild 两步契约，restore 后 readiness 保持 503，直到 rebuild 成功。

## 更新 Knowledge 镜像

Python 源码会复制进镜像，Compose 挂载也在容器创建时确定。修改代码或挂载后执行：

```powershell
docker compose build knowledge
docker compose up -d --force-recreate knowledge
```

该操作复用现有 Milvus 容器和 `milvus-data` 数据卷，不需要重新拉取或重建 Milvus。

## 备份与恢复

新备份是 ZIP，包含 manifest、SQLite 快照和托管文档。恢复会校验路径、软链接、成员数、压缩比、大小、哈希与 SQLite 完整性，并在替换前生成回滚副本。旧项目备份格式明确拒绝。失败时当前数据保持不变。

## XiaoXu

本机 XiaoXu 配置 `PRIVATE_AGENT_KNOWLEDGE_API_URL=http://127.0.0.1:8080`；同一 Docker 网络使用 `http://knowledge:8080`。仅传入普通 API Token。XiaoXu 应把返回分块标为不可信检索材料，再交给模型生成回答。
