# Horizon Runtime

[English](README.md)

Horizon Runtime 是一个本地优先的 AI Agent Runtime，提供结构化工具调用、确定性的执行安全边界，以及由 LLM 决定的后续行动。

> 当前状态：首次公开版本。接口仍可能调整。在确实需要本地副作用之前，建议保持默认的只读权限模式。

## 主要能力

- 结构化 ToolCall：由模型选择工具并提交真实参数。
- 确定性安全检查：Runtime 负责 schema、访问模式、路径、工作目录、超时、浏览器、数据库和 MCP 边界。
- 忠实执行：不同 call ID 独立执行；同一 call ID 重复投递时复用原 ToolObservation。
- 以单次模型响应为作用域的 doom-loop 检测，并通过明确的 permission boundary 处理。
- ToolObservation 返回模型，由模型决定继续调用工具还是给出最终回复。
- 内置文件、文档、Shell、Git、Web 搜索与抓取、浏览器、记忆、RAG、工作区、用量和缓存工具。
- 可选的 MCP Runtime、FastAPI 服务和 React/Vite Web 界面。

## Runtime 主流程

```text
LLM structured ToolCall
-> same-call replay 检查
-> response-scoped doom-loop permission
-> execution boundary
-> 真实工具执行
-> ToolObservation
-> LLM continuation 或最终回复
```

## 环境要求

- Python 3.10 或更高版本；项目使用 Python 3.12 测试。
- 可选前端需要 Node.js 18 或更高版本。
- 一个兼容 OpenAI API 的模型服务和 API Key。

浏览器工具还需要安装 Playwright 浏览器。

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

密钥只应保存在 `.env` 中，该文件已被 Git 忽略。默认权限模式为：

```dotenv
AGENT_ACCESS_MODE=read_only
```

### 命令行

```bash
python main.py
```

输入 `exit` 或 `quit` 可退出交互会话。

### 本地 API

```bash
python scripts/run_api.py
```

默认地址为 `http://127.0.0.1:8000`。服务运行时可打开 `http://127.0.0.1:8000/docs` 查看 FastAPI 接口文档。

### 前端

先启动 API，再运行：

```bash
cd frontend
npm install
npm run dev
```

前端默认连接 `http://127.0.0.1:8000`，可在设置中修改后端地址、API Key、用户 ID 和项目 ID。

### 浏览器支持

安装 Python 依赖后，如需使用浏览器工具，再安装 Chromium：

```bash
python -m playwright install chromium
```

## 配置

请以 [.env.example](.env.example) 作为公开配置模板。常用配置包括：

| 变量 | 用途 | 默认值 |
| --- | --- | --- |
| `LLM_PROVIDER` | 模型 Provider 适配器 | `openai_compatible` |
| `LLM_BASE_URL` | 模型 API 地址 | 空 |
| `LLM_MODEL` | 模型名称 | 空 |
| `LLM_API_KEY` | 本地模型凭据 | 空 |
| `AGENT_ACCESS_MODE` | `read_only` 或 `full_access` | `read_only` |
| `API_AUTH_ENABLED` | 是否启用 API Key 校验 | `false` |
| `API_KEYS` | 逗号分隔的服务 API Key | 开发假值 |
| `WEB_SEARCH_PROVIDER` | `auto`、`searxng`、`tavily` 或 `disabled` | `auto` |
| `MCP_ENABLED` | 是否加载本地 MCP Runtime | `false` |

不要提交 `.env`、前端环境文件、本地 MCP 配置、凭据或 Runtime 数据目录。

## 测试

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
- 安全漏洞请按 [SECURITY.zh-CN.md](SECURITY.zh-CN.md) 反馈，不要在公开 Issue 中发布利用细节。

## 许可证

Horizon Runtime 使用 [MIT License](LICENSE)。项目同时提供一份[简体中文参考译文](LICENSE.zh-CN)，法律效力以英文许可证为准。
