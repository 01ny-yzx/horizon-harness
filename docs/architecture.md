# Architecture

[← README](../README.md) · [简体中文](architecture.zh-CN.md)

## What the harness owns

- **Agent loop:** model responses, ToolCalls, ToolObservations, and continuation turns.
- **Dynamic tool surface:** tools are exposed according to availability, access mode, and task grants.
- **Execution boundary:** centralized checks for schemas, paths, working directories, URLs, SQL, browser actions, timeouts, and side effects.
- **Reliable dispatch:** repeated delivery of the same call ID replays its prior observation, while response-scoped repetition is checked for loops.
- **Durable sessions:** input enters a persistent inbox before it is promoted into model-visible history; messages, events, and context epochs are stored in SQLite.
- **Short- and long-term memory:** runtime Memory serves the current process, while PersistentMemory stores preferences, stable facts, project knowledge, instructions, and on-demand references.
- **Context lifecycle:** Context Epochs, chronological System updates, and durable Compaction control long-running model context.
- **Explicit recovery:** a process can reopen existing sessions and continue from committed history without blindly replaying uncertain tool side effects.
- **Multiple entry points:** Textual terminal UI, command-line, FastAPI, and a React/Vite web interface.

## Execution flow

```text
User input
-> PromptAdmitted: write durable session_input first
-> SessionExecution: serialize this Session in the current process
-> Prompted: promote the input into canonical session_message
-> Context Epoch + Session History: rebuild Provider context
-> LLM returns prose or structured ToolCalls
-> permission and ExecutionBoundary validation
-> real tool dispatch and durable ToolObservation
-> LLM continuation or final response
-> durable Assistant or Synthetic Session output
```

## Sessions, memory, and recovery

Horizon separates runtime context, conversation history, and long-term memory instead of treating them as one chat transcript.

| Layer | Authority | Lifetime and responsibility |
| --- | --- | --- |
| `Memory.messages` | Current-process runtime overlay | Holds the active Agent runtime's short-term context and task-scoped notes; it is not the cross-process Session authority. |
| `session_input` | Durable Input Inbox | Stores reliably admitted input that may not yet be visible to the model. Supports `steer`, `queue`, and idempotent `message_id` reuse. |
| `session_input_control` | Queue controls | Records steer/recall state and work-item claims without changing admission identity. |
| `session_message` | Canonical Session History | Stores model-visible User, Assistant (including Tool lifecycle), System, Synthetic, and Compaction messages. |
| `event` | Durable chronology | Records Prompt, Step, Tool, Context, and Compaction facts under a monotonic per-Session sequence. |
| `PersistentMemory` | SQLite long-term memory | Stores user- and project-scoped preferences, stable facts, project summaries, instructions, task history, and memory references. |
| `session_context_epoch` | Durable privileged context | Stores the exact model-visible System Context baseline, structured source snapshot, and `baseline_seq`. |

### Input admission and scheduling

- `PromptAdmitted` means input was reliably received; it does not create a User Session Message.
- `Prompted` promotes the same `message_id` into model-visible User history.
- `steer` inputs join an eligible Provider turn in durable sequence order; `queue` inputs are consumed one at a time in FIFO order.
- SessionRunCoordinator only serializes one Session inside the current process. The durable inbox remains the work authority.

### Context Epoch and Compaction

- The first execution establishes an exact model-visible System Context baseline before prompt promotion.
- Later Provider turns re-observe the environment, date, root `HORIZON.md`, persistent instructions, and memory-reference catalog at safe boundaries.
- Context changes become durable chronological `ContextUpdated` System Messages. Temporary source failures are not treated as removals.
- When a long Session exceeds the model budget, Horizon writes a durable Compaction checkpoint. Original events and messages remain stored while the Runner builds effective history from the latest checkpoint boundary.

### Process restart and explicit recovery

A process restart is not a Session restart. `session`, `session_input`, `session_message`, `event`, Context Epoch state, and long-term memory survive. Threads, Provider streams, Tool Python stacks, and `SessionExecution.active()` do not.

- Reading Session identity, context, or history never invokes the Provider or a Tool.
- Pending unpromoted input runs only after a new wake or explicit `resume`.
- Ordinary wake does not replay already promoted input. Explicit `resume` starts a new Provider turn from committed history.
- A Tool left pending or running is durably settled as interrupted/error when real execution next starts; its side effect is never replayed automatically.
- Horizon does not reconnect old Provider streams or provide automatic crash replay.

## Built-in capabilities

The local tool set covers:

- bounded paginated text reads, file search, writes, and text replacement;
- document ingestion, chunking, and local RAG;
- shell commands and bounded Python execution;
- Git status, diffs, branches, and guarded mutations;
- web search, page fetching, and browser automation;
- durable Sessions, explicit reopen, Context Epochs, and Session Compaction;
- short-term runtime memory, long-term PersistentMemory, and vector storage;
- multi-user and multi-project workspaces;
- usage, cache, traces, and runtime metrics;
- installation, configuration, lifecycle, and status management for local MCP services.

## Repository map

| Path | Responsibility |
| --- | --- |
| `main.py` | Legacy synchronous command-line entry point |
| `core/` | AgentLoop, durable Sessions, execution coordination, context, memory, Compaction, and RAG |
| `tools/` | Local tool implementations, schemas, registry, and risk metadata |
| `providers/` | OpenAI-compatible model adapter |
| `api/` | FastAPI service |
| `tui/` | Terminal interface with automatic local-service lifecycle |
| `frontend/` | React/Vite web interface |
| `mcp_servers/` | Repository-provided local MCP servers |
| `cloud_mcp/` | Preview Cloud MCP infrastructure |
| `scripts/` | Smoke, audit, and launch scripts |
| `evals/` | Behavioral evals |

## Current boundaries

- AgentLoop currently exposes repository-local tools. MCP management, connection, and permission infrastructure exists, but MCP tools are not yet injected into the model-visible tool surface.
- Cloud MCP remains preview infrastructure; remote tool execution is not enabled.
- `/chat` is a durable admission endpoint. It returns `session_id`, `message_id`, and `admitted_seq` instead of waiting for a final model answer. With the default `resume=true`, it only wakes background Session execution.
- The CLI intentionally uses `prompt(resume=False) -> resume()` to wait for the Session drain and print the current turn's final output.
- Session APIs expose create, list, get, context, finite event history, active, resume, interrupt, and SSE subscriptions at `/session/{id}/events`.
- The TUI streams model text and execution status. Completed messages remain in durable Session history; transient tokens use a bounded in-memory journal, with durable snapshots for expired reconnect cursors. WebSocket is not implemented.
- Process startup does not scan or resume old Sessions. Provider streams and uncertain Tool side effects are never replayed automatically.
- The sandbox is a bounded host subprocess executor with permission, timeout, and output controls. It is not container or operating-system isolation.
- The API binds to `127.0.0.1` by default. `/health` and `/status` are currently public; the remaining routes can be protected with API keys.
