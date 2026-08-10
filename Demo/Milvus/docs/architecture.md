# 架构与一致性边界

Knowledge Service 由独立的 `knowledge` 和 `milvus` 容器组成。前者负责 HTTP v1、认证、文档解析、脱敏、GPU Embedding、事务编排和管理；后者只负责 Dense、BM25 sparse 与检索元数据。BGE-M3 固定在 `cuda:0`，Milvus 的 HNSW 和 BM25 仍由 CPU 执行。

## 权威数据

- SQLite schema v3 是知识库、权限、文档状态、活动版本和导入幂等性的唯一权威。
- Milvus 行通过 `owner_id / kb_id / doc_id / version_id / chunk_id` 与 SQLite 关联。
- 新版本先写 Milvus并核对数量，随后才在 SQLite 事务中激活。搜索只查询 SQLite 当前活动版本，所以未激活或失败残留的向量不可见。
- `imports` 永远只读。未脱敏文件复制到 `/data/documents/<doc_id>/`；触发脱敏时只保存脱敏后的纯文本。
- Milvus 丢失后，使用 SQLite 活动版本和托管文档重建。恢复备份会留下 `/data/.reindex-required`，readiness 在 GPU 重建和数量校验成功前保持失败。

## 并发状态

普通查询、状态和列表共享读锁；导入和文档变更串行提交，搜索在此期间继续读取旧活动版本。导出、恢复和重建使用独占维护锁；一旦维护等待开始，新请求即被拒绝并返回 503，避免维护长期饥饿。

## 安全边界

普通 Token 只能搜索、查看状态和列表；管理员 Token 才能导入、变更、删除、导出、恢复和重建。XiaoXu 只持有普通 Token，不接触 SQLite、Milvus、imports、runtime 或管理员 Token。

