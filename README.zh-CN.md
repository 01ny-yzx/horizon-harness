# Horizon Agent Harness

[English](README.md)

Horizon 是一个本地优先、具备持久会话能力的 AI Agent Harness。它位于大语言模型与本地能力之间，负责组织 Agent 循环、管理长短期记忆、持久化会话、暴露工具、校验权限、执行操作，并把结构化结果返回给模型继续判断。

模型决定调用什么工具、使用什么参数，以及何时结束任务；Harness 负责让这些决定能够被安全、可追踪地执行。

> 当前状态：首次公开版本。接口和能力边界仍可能调整。默认使用 `read_only`，只有确实需要修改本地状态时才启用 `full_access`。

## Harness 负责什么

- **Agent 循环**：处理模型回复、ToolCall、ToolObservation 和后续模型调用。
- **动态工具面**：根据工具是否可用、访问模式和任务授权决定本轮可以调用的工具。
- **执行边界**：统一检查 schema、路径、工作目录、URL、SQL、浏览器行为、超时和副作用权限。
- **可靠执行**：同一 call ID 重复投递时复用已有观察结果，并检测单次模型响应中的重复调用循环。
- **Durable Session**：用户输入先进入持久化收件箱，再提升为模型可见历史；消息、事件和上下文阶段均由 SQLite 保存。
- **长短期记忆**：运行时 Memory 负责当前进程上下文，PersistentMemory 保存用户偏好、稳定事实、项目信息、指令和按需引用。
- **上下文生命周期**：通过 Context Epoch、时间顺序 System 更新和 Durable Compaction 控制长期会话上下文。
- **显式恢复**：进程重启后可以重新读取 Session，并从确定的持久历史显式继续，不盲目重放状态不确定的工具副作用。
- **多种入口**：支持命令行、FastAPI 服务和 React/Vite Web 界面。

## 执行流程

```text
用户输入
-> PromptAdmitted：先写入 durable session_input
-> SessionExecution：同一 Session 在当前进程内串行执行
-> Prompted：输入进入 canonical session_message
-> Context Epoch + Session History：重建本轮 Provider Context
-> LLM 返回普通回复或结构化 ToolCall
-> 权限和 ExecutionBoundary 校验
-> 执行真实工具并持久化 ToolObservation
-> LLM 继续调用工具或给出最终回复
-> Assistant / Synthetic 输出写入 durable Session History
```

## 会话、记忆与恢复

Horizon 明确区分运行时上下文、会话历史和长期记忆，避免把不同生命周期的数据混成一份聊天记录。

| 层 | Authority | 生命周期与职责 |
| --- | --- | --- |
| `Memory.messages` | 当前进程运行时 overlay | 保存当前 Agent runtime 的短期上下文和任务级提示；不是跨进程会话 authority。 |
| `session_input` | Durable Input Inbox | 保存已可靠接收但可能尚未进入模型历史的输入，支持 `steer`、`queue` 和 `message_id` 幂等。 |
| `session_message` | Canonical Session History | 保存模型可见的 User、Assistant（含 Tool lifecycle）、System、Synthetic 和 Compaction 消息。 |
| `event` | Durable chronology | 使用每个 Session 的单调序号记录 Prompt、Step、Tool、Context 和 Compaction 事实。 |
| `PersistentMemory` | SQLite 长期记忆 | 按用户与项目作用域保存偏好、稳定事实、项目摘要、项目指令、任务历史和记忆引用。 |
| `session_context_epoch` | Durable privileged context | 保存真实发送给模型的 System Context baseline、结构化 Source snapshot 和 `baseline_seq`。 |

### 输入接收与调度

- `PromptAdmitted` 只表示输入已可靠接收，不等于 User Session Message。
- `Prompted` 才会把同一个 `message_id` 提升为模型可见 User Message。
- `steer` 按 durable sequence 顺序并入安全的 Provider turn；`queue` 按 FIFO 一次处理一条。
- SessionRunCoordinator 只负责当前进程内同一 Session 串行化；durable inbox 才是待处理工作的 authority。

### Context Epoch 与 Compaction

- 第一次执行前建立 exact model-visible System Context baseline。
- 后续 Provider turn 在安全边界重新观察环境、日期、根 `HORIZON.md`、持久指令和记忆引用目录。
- Context 变化通过 durable `ContextUpdated` System Message 进入时间线；临时读取失败不会被误判为 Source 删除。
- 长会话超过模型预算时生成 durable Compaction checkpoint。原始事件和消息仍保留，Runner 从最新 checkpoint 边界构建有效历史。

### 进程重启与显式恢复

进程重启不会重启 Session。`session`、`session_input`、`session_message`、`event`、Context Epoch 和长期记忆仍然保留；线程、Provider stream、Tool Python 调用栈和 `SessionExecution.active()` 会清零。

- 读取 Session、context 或 history 不会触发 Provider 或 Tool。
- 未提升的 pending input 只有在新的 wake 或显式 `resume` 后才会执行。
- 已提升的输入不会被普通 wake 当作新工作重复执行；显式 `resume` 会从当前 durable history 开启新的 Provider turn。
- 崩溃前处于 pending/running 的 Tool 会在下一次真实执行时标记为 interrupted/error，不会自动重放其副作用。
- Horizon 当前不恢复旧 Provider stream，也不提供自动 crash replay。

## 内置能力

当前内置工具覆盖：

- 有界分页文本读取、文件搜索、写入和文本替换
- 文档加载、分块和本地 RAG
- Shell 命令和受约束的 Python 执行
- Git 状态、差异、分支及受控修改
- Web 搜索、网页抓取和浏览器自动化
- Durable Session、显式恢复、Context Epoch 和会话压缩
- 短期运行时记忆、长期 PersistentMemory 和向量存储
- 多用户、多项目工作区
- 用量统计、缓存、Trace 和运行指标
- 本地 MCP 服务的安装、配置、启停和状态管理

## 项目结构

| 路径 | 作用 |
| --- | --- |
| `main.py` | 命令行入口 |
| `core/` | AgentLoop、Durable Session、执行协调、上下文、记忆、Compaction 和 RAG |
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
- `/chat` 是 durable admission 接口：返回 `session_id`、`message_id` 和 `admitted_seq`，不等待或返回最终模型答案。默认 `resume=true` 时只负责唤醒后台 Session execution。
- CLI 使用 `prompt(resume=False) -> resume()` 同步等待当前 Session drain，再读取并打印本轮最终输出；这与 API admission 语义有意区分。
- Session API 提供 list、get、context、有限 event history、active、resume 和 interrupt，但当前没有完成通知或结果订阅接口。
- 暂不支持 SSE、WebSocket 或流式响应。
- 进程重启后不会自动扫描或续跑旧 Session；Provider stream 和状态不确定的 Tool side effect 不会自动重放。
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

CLI 会创建一个 durable Session，逐轮同步等待执行完成并显示当前轮最终回答。启动时输出的 Session ID 可用于 API 查询其 context 和 history。输入 `exit` 或 `quit` 退出。

### 本地 API

```bash
python scripts/run_api.py
```

默认地址为 `http://127.0.0.1:8000`。服务运行时可打开 `http://127.0.0.1:8000/docs` 查看接口文档。

提交一条 durable prompt：

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "user_id": "default_user",
    "project_id": "default_project",
    "message": "检查当前项目结构",
    "delivery": "steer",
    "resume": true
  }'
```

成功响应的关键字段是 admission identity，而不是最终答案：

```json
{
  "success": true,
  "answer": null,
  "session_id": "ses_...",
  "message_id": "msg_...",
  "admitted_seq": 1,
  "delivery": "steer"
}
```

Session reopen/control 接口：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/session` | 按用户与项目列出 durable Sessions |
| `GET` | `/session/active` | 返回当前进程正在执行的 Sessions |
| `GET` | `/session/{id}` | 读取 Session identity |
| `GET` | `/session/{id}/context` | 读取 canonical Session messages |
| `GET` | `/session/{id}/history?limit=50` | 分页读取 durable events |
| `POST` | `/session/{id}/resume` | 从当前 durable history 显式继续 |
| `POST` | `/session/{id}/interrupt` | 中断当前进程内 execution |

history 的 `after` 是可选的非负 exclusive sequence；不传时从第一条事件开始。`resume` 返回执行控制结果，不返回模型答案。需要通过 `context` 读取已经持久化的 Assistant 或 Synthetic 输出。

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
