# SearXNG

本目录提供独立运行的 SearXNG 搜索服务，固定使用
`searxng/searxng:2026.7.17-2daa4d481`。服务仅监听宿主机回环地址
`127.0.0.1:8081`，不会直接暴露到局域网或公网。

## 目录内容

- `compose.yaml`：单容器 Compose 编排。
- `settings.yml`：从旧项目原样迁移的搜索引擎配置。
- `.env`：本机运行密钥和端口配置，不纳入版本管理。
- `image/`：固定版本镜像归档、manifest 和 SHA-256 校验文件。

## 首次启动

在 PowerShell 中进入本目录，然后执行：

```powershell
$docker = "D:\DockerDesktop\resources\bin\docker.exe"

(Get-FileHash `
  -LiteralPath ".\image\downloaded\searxng-2026.7.17-2daa4d481.tar" `
  -Algorithm SHA256).Hash

& $docker load --input ".\image\downloaded\searxng-2026.7.17-2daa4d481.tar"
& $docker compose --env-file .env config --quiet
& $docker compose --env-file .env up -d
& $docker compose --env-file .env ps
```

归档的预期 SHA-256 为：

```text
633C2A7D8E6F5AACB7EA87AFD03B6952B8684DF642427E0E676B01F838E5805F
```

## 验证搜索

```powershell
$response = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8081/search?q=Python&format=json" `
  -TimeoutSec 30
$response.results | Select-Object -First 5 title, url
```

验收时应确认接口返回合法 JSON，并且 `results` 至少包含一个具有标题和 URL
的结果。若结果为空，检查 `unresponsive_engines` 和容器日志：

```powershell
& $docker compose --env-file .env logs --no-color searxng
```

## XiaoXu 接入

XiaoXu 在宿主机运行时设置：

```powershell
$env:PRIVATE_AGENT_SEARXNG_URL = "http://127.0.0.1:8081"
```

XiaoXu 在 Docker 容器中运行时设置：

```text
PRIVATE_AGENT_SEARXNG_URL=http://host.docker.internal:8081
```

SearXNG 与 Milvus/Knowledge 使用独立 Compose 生命周期。停止本服务但保留缓存卷：

```powershell
& $docker compose --env-file .env down
```

不要使用 `down -v`，否则会删除 SearXNG 缓存卷。
