# Docker 命令使用手册

本文只汇总本项目实际使用的 Docker 与 Docker Compose 命令，适用于 Windows PowerShell。项目中有两套相互独立的 Compose 容器组：

- `Demo\Milvus`：包含 `milvus` 和 `knowledge` 两个服务。
- `Demo\SearXNG`：包含 `searxng` 服务。

两套容器组需要在各自目录中单独执行 Compose 命令，启动、停止和删除其中一组不会自动操作另一组。

## 1. PowerShell 准备

Docker Desktop 安装在自定义目录时，先把 Docker CLI 的完整路径保存到变量中：

```powershell
$docker = "D:\DockerDesktop\resources\bin\docker.exe"
```

`$docker` 只在当前 PowerShell 会话中有效。打开新的 PowerShell 窗口后，需要重新执行这行命令。

检查 Docker 客户端、Docker Engine 和 Compose 是否可用：

```powershell
& $docker version
& $docker info
& $docker compose version
```

- `version` 同时显示客户端和服务端版本；如果只能看到客户端信息，通常说明 Docker Desktop 尚未启动。
- `info` 显示 Docker Engine、存储、运行容器和镜像等信息，可用于判断引擎是否正常。
- `compose version` 检查 Compose V2 子命令是否可用。
- PowerShell 中的 `&` 是调用运算符，用于执行变量中保存的程序路径。

## 2. 常用镜像命令

### 2.1 查看本地镜像

```powershell
& $docker image ls
```

只检查本项目固定使用的镜像：

```powershell
& $docker image inspect milvusdb/milvus:v2.6.22
& $docker image inspect searxng/searxng:2026.7.17-2daa4d481
```

- `image ls` 列出所有本地镜像及其标签、ID、创建时间和大小。
- `image inspect` 返回指定镜像的详细元数据；命令成功表示该镜像已经存在于本机。
- 当前 Compose 配置使用 `pull_policy: never`。镜像不存在时，Compose 会直接失败，不会自动从互联网拉取。

### 2.2 查看某套 Compose 使用的镜像

进入对应目录后执行：

```powershell
& $docker compose images
& $docker compose config --images
```

- `compose images` 显示当前项目容器实际使用的镜像。
- `compose config --images` 从合并后的 Compose 配置中列出计划使用的镜像名称。

### 2.3 从离线归档导入镜像

SearXNG 首次离线部署时使用：

```powershell
Set-Location "D:\Code\Python\langchain\Demo\SearXNG"
& $docker load --input ".\image\downloaded\searxng-2026.7.17-2daa4d481.tar"
```

`docker load` 从 `.tar` 归档恢复镜像及其标签。导入后可用 `docker image inspect` 确认固定版本镜像已经存在。

## 3. Milvus 与 Knowledge 容器组

以下命令都在该目录中执行：

```powershell
Set-Location "D:\Code\Python\langchain\Demo\Milvus"
$docker = "D:\DockerDesktop\resources\bin\docker.exe"
```

### 3.1 检查 Compose 配置

```powershell
& $docker compose config --quiet
```

该命令解析 `compose.yaml`、读取 `.env` 并验证变量替换和配置格式。成功时通常没有输出；配置缺失或环境变量未设置时会返回错误。

### 3.2 离线启动全部服务

```powershell
& $docker compose up -d --no-build --pull never
```

- `up` 创建并启动 Compose 定义的服务、默认网络和所需数据卷。
- `-d` 表示后台运行，命令完成后不会持续占用当前终端。
- `--no-build` 禁止在启动过程中构建 Knowledge 镜像。
- `--pull never` 禁止拉取镜像，确保只使用本地已有镜像。
- Compose 会先启动 `milvus`；Milvus 健康检查通过后，才会启动依赖它的 `knowledge`。

如果镜像已经准备好，日常启动也可使用较短命令：

```powershell
& $docker compose up -d
```

当前 `compose.yaml` 已为两个服务配置 `pull_policy: never`，因此该命令也不会主动拉取镜像。不过，明确写出 `--no-build --pull never` 更容易看出这是一次纯本地启动。

### 3.3 查看服务状态

```powershell
& $docker compose ps
& $docker compose ps -a
```

- `compose ps` 查看当前 Compose 项目正在运行的容器、端口和健康状态。
- `compose ps -a` 也显示已经停止或启动失败的容器。
- 正常情况下，`milvus` 和 `knowledge` 都应显示为 `healthy`。

### 3.4 停止但保留容器

```powershell
& $docker compose stop
```

`stop` 只停止容器，不删除容器、Compose 网络或数据卷。之后可以用 `start` 原样恢复，适合临时关闭服务。

只停止其中一个服务：

```powershell
& $docker compose stop knowledge
& $docker compose stop milvus
```

停止 Milvus 时，Knowledge 可能仍保持运行状态，但其向量数据库连接将不可用。日常维护建议停止整个容器组。

### 3.5 重新启动已停止的容器

```powershell
& $docker compose start
```

`start` 只启动已经存在但处于停止状态的容器，不会重新读取修改后的 Compose 配置，也不会创建新容器。

### 3.6 重启容器

```powershell
& $docker compose restart
```

只重启单个服务：

```powershell
& $docker compose restart knowledge
& $docker compose restart milvus
```

`restart` 对现有容器执行停止和启动，但不会应用新镜像、Dockerfile 或 `compose.yaml` 的配置变化。配置或镜像有变化时应使用 `up -d --force-recreate`。

### 3.7 删除容器但保留数据卷

```powershell
& $docker compose down
```

`down` 会停止并删除本组 Compose 容器和默认网络，但不会删除命名数据卷。下次执行 `up -d` 时会创建新容器并重新挂载原数据卷。

禁止在需要保留数据时执行：

```powershell
& $docker compose down -v
```

`-v` 会连同 Compose 管理的命名卷一起删除。对本项目而言，这可能删除 Milvus 索引数据，因此该命令只作为风险说明，不能用于日常停止。

## 4. 修改 Knowledge 代码后的构建与更新

只构建 `knowledge` 服务：

```powershell
Set-Location "D:\Code\Python\langchain\Demo\Milvus"
& $docker compose build knowledge
```

- 命令末尾的 `knowledge` 将构建范围限制为 Knowledge 服务。
- 该命令不会重新构建 Milvus，也不会因为构建 Knowledge 而重新拉取 `milvusdb/milvus:v2.6.22`。
- Knowledge 的 Dockerfile 基础镜像、系统包、Python 包或模型缓存不完整时，构建过程仍可能访问互联网。
- Dockerfile 第一行使用 Dockerfile frontend；本地没有对应缓存时，也可能访问 Docker Hub。因此网络不可用时，构建可能在解析 Dockerfile frontend 或获取构建依赖时失败。

构建完成后，只重建 Knowledge 容器：

```powershell
& $docker compose up -d --no-deps --force-recreate --pull never knowledge
```

- `knowledge` 指定只操作该服务。
- `--no-deps` 不启动或重建它依赖的 Milvus 服务。
- `--force-recreate` 即使 Compose 判断配置未变化，也强制创建新的 Knowledge 容器。
- `--pull never` 禁止拉取镜像。
- 该操作不会删除或重建 Milvus 数据卷。

如果 Knowledge 镜像已经构建完成，并且只需要使用现有镜像重新创建容器，可增加 `--no-build`：

```powershell
& $docker compose up -d --no-deps --no-build --force-recreate --pull never knowledge
```

## 5. SearXNG 容器组

以下命令都在该目录中执行：

```powershell
Set-Location "D:\Code\Python\langchain\Demo\SearXNG"
$docker = "D:\DockerDesktop\resources\bin\docker.exe"
```

SearXNG 的 Compose 命令显式使用 `.env`：

### 5.1 检查配置

```powershell
& $docker compose --env-file .env config --quiet
```

`--env-file .env` 指定 Compose 变量文件。它应放在 `compose` 之后、具体子命令之前。

### 5.2 离线启动

```powershell
& $docker compose --env-file .env up -d --no-build --pull never
```

该命令使用本地固定版本镜像启动 SearXNG，不构建也不拉取镜像。

### 5.3 查看状态

```powershell
& $docker compose --env-file .env ps
& $docker compose --env-file .env ps -a
```

正常情况下，`searxng` 应显示为 `healthy`，主机端口默认绑定到 `127.0.0.1:8081`。

### 5.4 停止、启动和重启

```powershell
& $docker compose --env-file .env stop
& $docker compose --env-file .env start
& $docker compose --env-file .env restart
```

- `stop` 停止并保留现有容器。
- `start` 启动已经存在的已停止容器。
- `restart` 重启现有容器，但不应用 Compose 配置或镜像变化。

### 5.5 删除容器但保留缓存卷

```powershell
& $docker compose --env-file .env down
```

该命令删除 SearXNG 容器和 Compose 网络，但保留 `searxng-data` 缓存卷。不要附加 `-v`，否则缓存卷也会被删除。

## 6. 日志与故障排查命令

### 6.1 查看最近日志

Milvus 与 Knowledge：

```powershell
Set-Location "D:\Code\Python\langchain\Demo\Milvus"
& $docker compose logs --no-color --tail 200
& $docker compose logs --no-color --tail 200 milvus
& $docker compose logs --no-color --tail 200 knowledge
```

SearXNG：

```powershell
Set-Location "D:\Code\Python\langchain\Demo\SearXNG"
& $docker compose --env-file .env logs --no-color --tail 200 searxng
```

- `logs` 读取 Compose 服务的标准输出和标准错误。
- `--tail 200` 只显示最后 200 行，避免一次输出过多历史日志。
- `--no-color` 去掉 ANSI 颜色控制字符，便于复制、保存和搜索。

### 6.2 持续跟踪日志

```powershell
& $docker compose logs -f milvus
& $docker compose logs -f knowledge
```

SearXNG：

```powershell
& $docker compose --env-file .env logs -f searxng
```

`-f` 表示持续跟踪新日志，作用类似实时日志窗口。按 `Ctrl+C` 只会结束日志跟踪，不会停止容器。

### 6.3 查看所有 Docker 容器

```powershell
& $docker ps
& $docker ps -a
```

- `docker ps` 显示所有正在运行的容器。
- `docker ps -a` 同时显示已停止、异常退出或尚未成功启动的容器。
- 与 `compose ps` 不同，这两个命令不受当前目录限制，会查看整个 Docker Engine。

### 6.4 查看容器详细状态

```powershell
& $docker inspect milvus-milvus-1
& $docker inspect milvus-knowledge-1
& $docker inspect xiaoxu-searxng-searxng-1
```

只查看健康检查结果：

```powershell
& $docker inspect --format '{{json .State.Health}}' milvus-milvus-1
& $docker inspect --format '{{json .State.Health}}' milvus-knowledge-1
& $docker inspect --format '{{json .State.Health}}' xiaoxu-searxng-searxng-1
```

`docker inspect` 返回容器配置、挂载、网络、运行状态、重启次数和健康检查记录。容器名可先通过 `docker ps -a` 或 `docker compose ps -a` 确认。

### 6.5 查看一次资源占用

```powershell
& $docker stats --no-stream
```

`docker stats` 显示 CPU、内存、网络和磁盘 I/O；`--no-stream` 只采集一次后退出，适合快速检查。

### 6.6 在容器内执行命令

打开 Knowledge 容器内的交互式 shell：

```powershell
Set-Location "D:\Code\Python\langchain\Demo\Milvus"
& $docker compose exec knowledge sh
```

执行一条非交互命令：

```powershell
& $docker compose exec -T knowledge knowledge-admin status --user-id local-user
```

- `compose exec` 在已经运行的容器内执行程序，不会创建新容器。
- `-T` 禁用伪终端，适合脚本、重定向或自动化调用。
- 输入 `exit` 可退出交互式 shell；退出 shell 不会停止容器。

## 7. 数据卷与网络检查

### 7.1 查看和检查数据卷

```powershell
& $docker volume ls
& $docker volume inspect milvus-data-recovered-20260808
& $docker volume inspect xiaoxu-searxng_searxng-data
```

- `volume ls` 列出 Docker Engine 中的全部数据卷。
- `volume inspect` 显示卷的真实名称、驱动、挂载点和标签。
- Compose 删除容器后，命名卷仍可独立存在并在下次启动时重新挂载。

### 7.2 查看 Docker 网络

```powershell
& $docker network ls
& $docker network inspect milvus_default
& $docker network inspect xiaoxu-searxng_default
```

- `network ls` 列出 Docker 网络。
- `network inspect` 显示连接到网络的容器、IP 地址和网络配置。
- Compose 默认会为每个项目创建独立的 `default` 网络；执行 `compose down` 后该默认网络会被删除，下次 `up` 时重新创建。

### 7.3 查看 Docker 磁盘占用

```powershell
& $docker system df
& $docker system df -v
```

- `system df` 汇总镜像、容器、数据卷和构建缓存占用。
- `-v` 显示更详细的逐项数据。
- 这两个命令只读取状态，不会清理任何数据。

## 8. 常用参数速查

| 参数 | 作用 | 注意事项 |
| --- | --- | --- |
| `-d` | 在后台启动容器 | 不会持续占用当前终端 |
| `-a` | 显示包括已停止容器在内的全部状态 | 常与 `ps` 一起使用 |
| `-f` | 持续跟踪日志 | `Ctrl+C` 只退出日志查看 |
| `--env-file .env` | 指定 Compose 环境变量文件 | 放在 `compose` 后、子命令前 |
| `--no-build` | 启动时禁止构建镜像 | 本地镜像不存在时启动会失败 |
| `--pull never` | 禁止拉取镜像 | 适合本项目的离线启动要求 |
| `--no-deps` | 不操作目标服务的依赖服务 | 更新 Knowledge 时避免重建 Milvus |
| `--force-recreate` | 强制重建容器 | 不会自动删除命名数据卷 |
| `--tail 200` | 只显示最后 200 行日志 | 数字可按需要调整 |
| `--no-color` | 关闭彩色日志控制字符 | 适合复制和保存日志 |
| `-T` | 禁用 `exec` 的伪终端 | 适合脚本或非交互命令 |
| `-v`（用于 `down`） | 删除 Compose 命名卷 | 有数据丢失风险，不用于日常停止 |

## 9. 启停命令速查

### Milvus 与 Knowledge

```powershell
$docker = "D:\DockerDesktop\resources\bin\docker.exe"
Set-Location "D:\Code\Python\langchain\Demo\Milvus"

# 离线启动
& $docker compose up -d --no-build --pull never

# 查看状态
& $docker compose ps -a

# 临时停止并保留容器
& $docker compose stop

# 启动已停止的容器
& $docker compose start

# 删除容器和网络，但保留数据卷
& $docker compose down
```

### SearXNG

```powershell
$docker = "D:\DockerDesktop\resources\bin\docker.exe"
Set-Location "D:\Code\Python\langchain\Demo\SearXNG"

# 离线启动
& $docker compose --env-file .env up -d --no-build --pull never

# 查看状态
& $docker compose --env-file .env ps -a

# 临时停止并保留容器
& $docker compose --env-file .env stop

# 启动已停止的容器
& $docker compose --env-file .env start

# 删除容器和网络，但保留缓存卷
& $docker compose --env-file .env down
```

## 10. 高风险清理命令

以下命令不是本项目的日常操作命令。执行前必须明确确认目标和数据是否已有备份：

```powershell
& $docker compose down -v
& $docker volume rm <卷名>
& $docker system prune --volumes
```

- `compose down -v` 删除当前 Compose 项目的容器、网络和命名卷。
- `volume rm` 删除指定数据卷；卷内数据不会随容器重建而恢复。
- `system prune --volumes` 会清理 Docker Engine 中未使用的资源，并可能影响当前项目之外的其他容器组。

需要释放空间时，应先使用 `docker system df -v`、`docker ps -a`、`docker image ls` 和 `docker volume ls` 确认实际占用与准确目标，不要直接执行全局清理。
