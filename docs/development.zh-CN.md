# 开发指南

[← 项目介绍](../README.zh-CN.md) · [English](development.md)

以下命令在仓库根目录、已激活 Python 环境时执行。TUI 日常使用见[使用指南](usage.zh-CN.md)，本页说明其他入口和开发配置。

## 本地 API

```bash
python scripts/run_api.py
```

默认地址为 `http://127.0.0.1:8000`，访问 `/docs` 查看接口文档。`/health` 和 `/status` 为公开接口；需要将受保护接口用于本地开发以外的场景时，应先配置 API 认证。

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "message": "检查当前项目结构",
    "delivery": "steer",
    "resume": true
  }'
```

`POST /chat` 返回可靠接收结果，不是模型最终答案。响应包含 `session_id`、`message_id` 和 `admitted_seq`；`resume=true` 唤醒后台执行，回答需读取 transcript 或订阅事件。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET / POST | `/session` | 列出 / 创建会话 |
| GET | `/session/active` | 当前进程正在执行的会话 |
| GET | `/session/{id}` | 会话身份与保存的目录 |
| GET | `/session/{id}/context` | 规范的模型可见消息 |
| GET | `/session/{id}/transcript` | 面向用户的消息与结果 |
| GET | `/session/{id}/history` | 持久化事件历史 |
| GET | `/session/{id}/events` | SSE 实时事件与重连快照 |
| POST | `/session/{id}/resume` | 从已提交历史显式继续 |
| POST | `/session/{id}/interrupt` | 请求协作式中断 |
| GET | `/session/{id}/pending` | 尚未领取的排队 / 插队输入 |
| POST | `/session/{id}/pending/{message_id}/steer` | 运行中将可用队列消息插队 |
| DELETE | `/session/{id}/pending/{message_id}` | 取回 / 移除可用队列消息 |

会话请求使用服务的工作区身份和归属检查。history 的 `after` 是 exclusive sequence 游标；读取历史不会调用模型或工具。队列消息若已被领取或消费，控制请求可能被拒绝。接收、领取和恢复语义见[架构说明](architecture.zh-CN.md)。

## Web 界面

仓库保留了 React/Vite 前端。先单独启动 API，再执行：

```bash
cd frontend
npm install
npm run dev
```

此可选前端需要与 [frontend/package.json](../frontend/package.json) 中版本兼容的 Node.js，Python TUI 不需要 Node.js。前端默认连接本地 API，提供连接设置；这是应用客户端，不是计划中的产品官网。

## 原有 CLI

```bash
python main.py
```

该入口创建持久化会话，并同步等待每轮最终输出，Session ID 可用于 API 查询。TUI 是主要交互入口。

## 常用配置

通常在 TUI 内配置模型和搜索即可，无需编辑 `.env`。开发和高级配置可以：

```bash
cp .env.example .env
```

密钥只保留在被忽略的本地 `.env` 或应用连接文件中。完整配置以 [`.env.example`](../.env.example) 为准。

| 变量 | 用途 |
| --- | --- |
| `LLM_PROVIDER` | 模型适配器，通常为 `openai_compatible` |
| `LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY` | 可选的环境变量模型连接 |
| `AGENT_ACCESS_MODE` | 默认权限，`read_only` / `full_access` |
| `API_AUTH_ENABLED`、`API_KEYS` | 服务 API 认证 |
| `HORIZON_USER_DATA_ROOT` | 覆盖本地数据目录 |
| `WEB_SEARCH_PROVIDER` | 搜索服务选择 |
| `MCP_ENABLED` | 可选的本地 MCP 管理与运行配置 |

已有本地 MCP 生命周期、注册表等基础设施，不等于模型现在能调用任意 MCP 工具：AgentLoop 的常规工具面来自仓库本地 Schema。Cloud MCP 仍为预览基础设施，不能将这些描述为完整的远程工具执行支持。

可选浏览器工具需要：

```bash
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
```

## 验证

仓库安全检查：

```bash
python scripts/check_git_safety.py
```

部分离线 TUI 检查：

```bash
python -m unittest scripts.smoke_tui_ui scripts.smoke_tui_queue scripts.smoke_tui_shortcuts scripts.smoke_tui_copy_selection -q
```

持久化队列与会话检查：

```bash
PYTHONPATH=. python scripts/smoke_session_queue_controls.py
PYTHONPATH=. python scripts/smoke_session_runtime.py
```

测试主要组织为 `scripts/smoke_*.py`，并非一个覆盖全部检查的 pytest 测试集。选择更广的检查前，先核对脚本环境和可选依赖。行为评估入口为 [evals/run_evals.py](../evals/run_evals.py)。

## 贡献与安全

请阅读[贡献指南](../CONTRIBUTING.zh-CN.md)和[安全说明](../SECURITY.zh-CN.md)。不要提交 `.env`、连接密钥、本地用户数据和运行产物。宿主命令执行器不提供隔离边界，详见[当前版本边界](architecture.zh-CN.md#当前版本边界)。

## 终端独立运行包

在独立的 Python 3.12 环境中安装 `requirements-build.txt`，然后执行：

```sh
python scripts/build_bundle.py
```

在对应系统和 CPU 架构上分别构建。脚本先打包终端程序，再在临时目录、PATH 中没有 Python 的环境下检查资源、Python 工具子进程、本地服务鉴权与界面，最后在 `dist/bundles/` 生成带版本的压缩包和 SHA-256 校验文件。Mac 压缩包保留符号链接，Windows 使用 ZIP；解压后需保留完整的 `horizon` 文件夹。

GitHub Actions 中可手动运行 **Native terminal bundles**，分别生成 macOS arm64、macOS x64 与 Windows x64 产物，不会自动发布版本。显式资源清单仅包含公开模型目录和界面资源；密钥、`.env`、会话和私有配置不进入运行包，运行数据仍保存在用户目录。首批独立运行包暂不包含可选的 Playwright 浏览器功能。

目前是测试构建：Mac 使用临时签名，尚未配置 Developer ID 签名与公证、Windows 发布签名。npm、Homebrew、curl 安装入口代码见下一节，公开发布仍需另行操作。

## npm、curl 与 Homebrew 安装入口

安装入口代码已经准备，但还没有公开发布。GitHub Release 与 npm 包实际存在之前，不要把下面的安装命令当成已经可用。

手动运行 **Native terminal bundles** 工作流：各平台先构建、自检，记录运行包实际要求的最低 macOS 版本，再通过本地下载服务器测试 Node 安装器。最后汇总三个平台，生成 npm 压缩包、macOS 的 `install.sh`、Homebrew Formula 与发布清单，整个流程不会自动发布。

本地生成测试用安装文件：

```sh
python scripts/prepare_installers.py
node --test distribution/tests/npm.test.cjs
npm pack ./dist/distribution/npm --pack-destination ./dist/distribution
```

正式发布前应加上 `--require-target macos-arm64 --require-target macos-x64 --require-target windows-x64`，确保三个平台齐全。每个运行包都需要对应的 `.sha256` 与 `.build.json` 文件。本机 Mac 构建可能要求较新的系统，最低版本从运行包的 Mach-O 文件读取；正式版本应使用兼容性更广的 CI 产物重新生成安装文件。缺失平台会在本地测试包里省略，不会生成虚假的校验值。

发布时先把对应运行包、校验文件与生成的 `install.sh` 上传到 GitHub 的 `v<版本>` Release，再发布生成目录中的 npm 包。`distribution/npm` 是私有源码模板，不能直接发布。Homebrew 还需把生成的 `Formula/horizon.rb` 放进 `01ny-yzx/homebrew-tap` 仓库的 `Formula` 目录，以支持一行安装。以上操作都应先检查产物；当前工作流只有仓库只读权限，不会替你发布。

正式发布后的使用方式：

```sh
npm install -g horizon-harness
horizon
```

```sh
curl -fsSL https://github.com/01ny-yzx/horizon-harness/releases/latest/download/install.sh | sh
```

```sh
brew install 01ny-yzx/tap/horizon
```

npm 对应 macOS arm64/x64 和 Windows x64，前提是该平台的运行包已经发布。curl 与 Homebrew 目前用于 Mac。curl 默认安装到 `~/.local/share/horizon/versions`，命令链接放在 `~/.local/bin`；该目录不在 PATH 时，需要添加一次。安装器不会修改 shell 配置文件，也不会覆盖来历不明的同名命令。可用 `HORIZON_INSTALL_ROOT`、`HORIZON_BIN_DIR` 指定其他绝对路径。

npm 与 curl 都会在解压前校验内嵌的 SHA-256，使用临时目录安装，并避免多个安装器同时修改同一目录。强制结束安装可能留下锁文件，只有确认安装器已退出后才清理提示中的锁文件。API Key 与历史会话仍在用户数据目录，不随安装目录替换。

开发测试可以用 `HORIZON_DOWNLOAD_BASE` 指向本地资源服务器，正常下载使用 HTTPS 的 GitHub Release 地址。`HORIZON_SKIP_DOWNLOAD=1` 可跳过 npm 安装时的下载，首次启动再补齐；它不代表离线运行包已经安装。

源码包通过 `python scripts/build_source.py` 生成，统一命名为 `dist/horizon-<版本>-source.zip`；运行包统一为 `horizon-<版本>-<平台>-<架构>`。版本号取自 `tui/version.py`。
