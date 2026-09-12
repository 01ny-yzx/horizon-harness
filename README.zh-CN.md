# Horizon Agent Harness

[English](README.md)

Horizon 是一个本地优先的 AI Agent Harness。它位于大语言模型与本地能力之间，负责组织 Agent 循环、暴露工具、校验权限、执行操作，并把结构化结果返回给模型继续判断。

模型决定调用什么工具、使用什么参数，以及何时结束任务；Harness 负责让这些决定能够被安全、可追踪地执行。

> 当前状态：首次公开版本。接口和能力边界仍可能调整。默认使用 `read_only`，只有确实需要修改本地状态时才启用 `full_access`。

## Harness 负责什么

- **Agent 循环**：处理模型回复、ToolCall、ToolObservation 和后续模型调用。
- **动态工具面**：根据工具是否可用、访问模式和任务授权决定本轮可以调用的工具。
- **执行边界**：统一检查 schema、路径、工作目录、URL、SQL、浏览器行为、超时和副作用权限。
- **可靠执行**：同一 call ID 重复投递时复用已有观察结果，并检测单次模型响应中的重复调用循环。
- **上下文与数据**：提供会话记忆、持久记忆、文档、RAG、工作区、用量、缓存和 Trace。
- **多种入口**：支持命令行、FastAPI 服务和 React/Vite Web 界面。

## 执行流程

```text
用户请求
-> Harness 组装上下文和当前工具面
-> LLM 返回普通回复或结构化 ToolCall
-> call ID 重放与重复调用检查
-> 权限和 ExecutionBoundary 校验
-> 执行真实工具
-> 生成 ToolObservation
-> LLM 继续调用工具或给出最终回复
```

## 内置能力

当前内置工具覆盖：

- 文件读取、搜索、写入和文本替换
- 文档加载、分块和本地 RAG
- Shell 命令和受约束的 Python 执行
- Git 状态、差异、分支及受控修改
- Web 搜索、网页抓取和浏览器自动化
- 会话记忆、持久记忆和向量存储
- 多用户、多项目工作区
- 用量统计、缓存、Trace 和运行指标
- 本地 MCP 服务的安装、配置、启停和状态管理

## 项目结构

| 路径 | 作用 |
| --- | --- |
| `main.py` | 命令行入口 |
| `core/` | AgentLoop、执行边界、状态、上下文、记忆和 RAG |
| `tools/` | 本地工具实现、Schema、注册表和风险元数据 |
| `providers/` | OpenAI-compatible 模型适配层 |
| `api/` | FastAPI 接口 |
| `frontend/` | React/Vite Web 界面 |
| `mcp_servers/` | 仓库内置的本地 MCP Server |
| `cloud_mcp/` | Cloud MCP 预览基础设施 |
| `scripts/` | Smoke、审计和运行脚本 |
| `evals/` | 行为 Eval |

## 当前版本边界

- AgentLoop 当前使用仓库内置的本地工具。MCP 已有管理、连接和权限基础设施，但 MCP 工具尚未注入模型可调用的工具面。
- Cloud MCP 仍处于预览搭建阶段，远程工具执行尚未开放。
- `/chat` 当前为同步请求，前端会等待完整任务结果；暂不支持 SSE、WebSocket 或流式输出。
- Sandbox 是带权限、超时和输出限制的宿主机子进程执行器，不是容器或操作系统级隔离环境。
- API 默认只监听 `127.0.0.1`。当前 `/health` 和 `/status` 为公开端点，其余路由可通过 API Key 保护。

## 环境要求

- Python 3.10 或更高版本；项目使用 Python 3.12 测试。
- 前端需要 Node.js 18 或更高版本。
- 一个兼容 OpenAI API 的模型服务和 API Key。
- 浏览器工具需要安装 Playwright 浏览器。

## 快速开始

```bash
git clone https://github.com/01ny-yzx/horizon-runtime.git
cd horizon-runtime

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

cp .env.example .env
```

至少在本地 `.env` 中配置：

```dotenv
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://your-provider.example/v1
LLM_MODEL=your-model
LLM_API_KEY=your-local-api-key
```

密钥只应保存在 `.env` 中，该文件已被 Git 忽略。默认访问模式为：

```dotenv
AGENT_ACCESS_MODE=read_only
```

### 命令行

```bash
python main.py
```

输入 `exit` 或 `quit` 退出会话。

### 本地 API

```bash
python scripts/run_api.py
```

默认地址为 `http://127.0.0.1:8000`。服务运行时可打开 `http://127.0.0.1:8000/docs` 查看接口文档。

### Web 界面

先启动 API，再运行：

```bash
cd frontend
npm install
npm run dev
```

前端默认连接 `http://127.0.0.1:8000`。可以在设置中修改后端地址、API Key、用户 ID 和项目 ID。

### 浏览器工具

安装 Python 依赖后，再安装 Chromium：

```bash
python -m playwright install chromium
```

## 常用配置

公开配置以 [.env.example](.env.example) 为准。

| 变量 | 用途 | 默认值 |
| --- | --- | --- |
| `LLM_PROVIDER` | 模型 Provider 适配器 | `openai_compatible` |
| `LLM_BASE_URL` | 模型 API 地址 | 空 |
| `LLM_MODEL` | 模型名称 | 空 |
| `LLM_API_KEY` | 本地模型凭据 | 空 |
| `AGENT_ACCESS_MODE` | `read_only` 或 `full_access` | `read_only` |
| `API_AUTH_ENABLED` | 为受保护的 API 路由启用 API Key 校验 | `false` |
| `API_KEYS` | 逗号分隔的服务 API Key | 开发假值 |
| `WEB_SEARCH_PROVIDER` | `auto`、`searxng`、`tavily` 或 `disabled` | `auto` |
| `MCP_ENABLED` | 加载本地 MCP 管理与连接能力 | `false` |

不要提交 `.env`、前端环境文件、本地 MCP 配置、凭据、工作区数据或运行产物。

## 验证

运行 Git 安全检查：

```bash
python scripts/check_git_safety.py
```

运行全部 Smoke：

```bash
for test in scripts/smoke_*.py; do python "$test" || exit 1; done
```

运行 Eval：

```bash
python evals/run_evals.py
```

## 贡献与安全

- 提交修改前请阅读 [CONTRIBUTING.zh-CN.md](CONTRIBUTING.zh-CN.md)。
- 安全漏洞请按 [SECURITY.zh-CN.md](SECURITY.zh-CN.md) 私密反馈，不要在公开 Issue 中发布利用细节。

## 许可证

Horizon Agent Harness 使用 [MIT License](LICENSE)。项目同时提供一份[简体中文参考译文](LICENSE.zh-CN)，法律效力以英文许可证为准。
