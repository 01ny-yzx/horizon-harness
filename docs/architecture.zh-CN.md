# 架构与运行边界

[← 项目介绍](../README.zh-CN.md) · [English](architecture.md)

## Harness 负责什么

- **Agent 循环**：处理模型回复、ToolCall、ToolObservation 和后续模型调用。
- **动态工具面**：根据工具是否可用、访问模式和任务授权决定本轮可以调用的工具。
- **执行边界**：统一检查 schema、路径、工作目录、URL、SQL、浏览器行为、超时和副作用权限。
- **可靠执行**：同一 call ID 重复投递时复用已有观察结果，并检测单次模型响应中的重复调用循环。
- **Durable Session**：用户输入先进入持久化收件箱，再提升为模型可见历史；消息、事件和上下文阶段均由 SQLite 保存。
- **长短期记忆**：运行时 Memory 负责当前进程上下文，PersistentMemory 保存用户偏好、稳定事实、项目信息、指令和按需引用。
- **上下文生命周期**：通过 Context Epoch、时间顺序 System 更新和 Durable Compaction 控制长期会话上下文。
- **显式恢复**：进程重启后可以重新读取 Session，并从确定的持久历史显式继续，不盲目重放状态不确定的工具副作用。
- **多种入口**：支持 Textual TUI、命令行、FastAPI 服务和 React/Vite Web 界面。

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
| `tui/` | 自动管理本地服务的终端交互界面 |
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
- Session API 提供 create、list、get、context、有限 event history、active、resume、interrupt，以及 `/session/{id}/events` SSE 订阅。
- TUI 支持模型文字流式显示和执行状态。完成消息仍写入原有 Session 历史；瞬时 token 使用有界内存缓冲，重连缺口通过持久化快照恢复。暂不提供 WebSocket。
- 进程重启后不会自动扫描或续跑旧 Session；Provider stream 和状态不确定的 Tool side effect 不会自动重放。
- Sandbox 是带权限、超时和输出限制的宿主机子进程执行器，不是容器或操作系统级隔离环境。
- API 默认只监听 `127.0.0.1`。当前 `/health` 和 `/status` 为公开端点，其余路由可通过 API Key 保护。

队列控制由 `session_input_control` 记录插队、取回及执行领取状态；插队保留原消息 ID 和接收时的权限。已被执行器领取的消息不能再取回。模型执行失败时保留剩余排队消息，读取历史不会自动重试。
