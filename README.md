# Horizon Agent Harness

[简体中文](README.zh-CN.md)

Horizon is a local-first AI agent harness with durable sessions. It sits between an LLM and local capabilities, running the agent loop, managing short- and long-term memory, persisting sessions, exposing tools, enforcing permissions, dispatching operations, and returning structured observations to the model.

The model chooses tools and arguments and decides when the task is complete. The harness makes those decisions executable, bounded, and traceable.

> Status: initial public release. Interfaces and capability boundaries may still change. Use the default `read_only` mode until local side effects are intentionally required.

## What the harness owns

- **Agent loop:** model responses, ToolCalls, ToolObservations, and continuation turns.
- **Dynamic tool surface:** tools are exposed according to availability, access mode, and task grants.
- **Execution boundary:** centralized checks for schemas, paths, working directories, URLs, SQL, browser actions, timeouts, and side effects.
- **Reliable dispatch:** repeated delivery of the same call ID replays its prior observation, while response-scoped repetition is checked for loops.
- **Durable sessions:** input enters a persistent inbox before it is promoted into model-visible history; messages, events, and context epochs are stored in SQLite.
- **Short- and long-term memory:** runtime Memory serves the current process, while PersistentMemory stores preferences, stable facts, project knowledge, instructions, and on-demand references.
- **Context lifecycle:** Context Epochs, chronological System updates, and durable Compaction control long-running model context.
- **Explicit recovery:** a process can reopen existing sessions and continue from committed history without blindly replaying uncertain tool side effects.
- **Multiple entry points:** command-line, FastAPI, and a React/Vite web interface.

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
| `main.py` | Command-line entry point |
| `core/` | AgentLoop, durable Sessions, execution coordination, context, memory, Compaction, and RAG |
| `tools/` | Local tool implementations, schemas, registry, and risk metadata |
| `providers/` | OpenAI-compatible model adapter |
| `api/` | FastAPI service |
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
- Session APIs expose list, get, context, finite event history, active, resume, and interrupt controls, but there is no completion subscription API yet.
- SSE, WebSocket, and streaming responses are not currently implemented.
- Process startup does not scan or resume old Sessions. Provider streams and uncertain Tool side effects are never replayed automatically.
- The sandbox is a bounded host subprocess executor with permission, timeout, and output controls. It is not container or operating-system isolation.
- The API binds to `127.0.0.1` by default. `/health` and `/status` are currently public; the remaining routes can be protected with API keys.

## Requirements

- Python 3.10 or newer; the project is tested with Python 3.12.
- Node.js 18 or newer for the frontend.
- An OpenAI-compatible LLM endpoint and API key.
- A Playwright browser installation for browser tools.

## Quick start

```bash
git clone https://github.com/01ny-yzx/horizon-runtime.git
cd horizon-runtime

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

cp .env.example .env
```

Configure at least these values in your local `.env`:

```dotenv
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://your-provider.example/v1
LLM_MODEL=your-model
LLM_API_KEY=your-local-api-key
```

Keep secrets in `.env`; it is ignored by Git. The default access mode is:

```dotenv
AGENT_ACCESS_MODE=read_only
```

### Command-line interface

```bash
python main.py
```

The CLI creates a durable Session, waits synchronously for each turn, and prints that turn's final answer. The displayed Session ID can be used to query context and history through the API. Type `exit` or `quit` to leave.

### Local API

```bash
python scripts/run_api.py
```

The default address is `http://127.0.0.1:8000`. FastAPI documentation is available at `http://127.0.0.1:8000/docs` while the service is running.

Admit one durable prompt:

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "user_id": "default_user",
    "project_id": "default_project",
    "message": "Inspect the current project structure",
    "delivery": "steer",
    "resume": true
  }'
```

The relevant fields in a successful response describe admission identity, not a final answer:

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

Session reopen and control endpoints:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/session` | List durable Sessions in one user/project scope |
| `GET` | `/session/active` | List Sessions active in this process |
| `GET` | `/session/{id}` | Read durable Session identity |
| `GET` | `/session/{id}/context` | Read canonical Session messages |
| `GET` | `/session/{id}/history?limit=50` | Page through durable events |
| `POST` | `/session/{id}/resume` | Explicitly continue from durable history |
| `POST` | `/session/{id}/interrupt` | Interrupt current process-local execution |

The optional history `after` value is a non-negative exclusive sequence; omit it to start with the first event. `resume` returns execution control status, not the model answer. Read the persisted Assistant or Synthetic output through `context`.

### Web interface

Start the API first, then run:

```bash
cd frontend
npm install
npm run dev
```

The frontend connects to `http://127.0.0.1:8000` by default. The backend URL, API key, user ID, and project ID can be changed in Settings.

### Browser tools

After installing Python dependencies, install Chromium:

```bash
python -m playwright install chromium
```

## Common configuration

Use [.env.example](.env.example) as the public configuration reference.

| Variable | Purpose | Default |
| --- | --- | --- |
| `LLM_PROVIDER` | Model provider adapter | `openai_compatible` |
| `LLM_BASE_URL` | Model API endpoint | empty |
| `LLM_MODEL` | Model identifier | empty |
| `LLM_API_KEY` | Local model credential | empty |
| `AGENT_ACCESS_MODE` | `read_only` or `full_access` | `read_only` |
| `API_AUTH_ENABLED` | Enable API-key checks for protected routes | `false` |
| `API_KEYS` | Comma-separated service API keys | development placeholder |
| `WEB_SEARCH_PROVIDER` | `auto`, `searxng`, `tavily`, or `disabled` | `auto` |
| `MCP_ENABLED` | Load local MCP management and connection capabilities | `false` |

Do not commit `.env`, frontend environment files, local MCP configuration, credentials, workspace data, or runtime artifacts.

## Validation

Run the repository safety check:

```bash
python scripts/check_git_safety.py
```

Run all Smoke tests:

```bash
for test in scripts/smoke_*.py; do python "$test" || exit 1; done
```

Run the Eval suite:

```bash
python evals/run_evals.py
```

## Contributing and security

- See [CONTRIBUTING.md](CONTRIBUTING.md) before submitting a change.
- Report vulnerabilities privately according to [SECURITY.md](SECURITY.md); do not publish exploit details in a public issue.

## License

Horizon Agent Harness is licensed under the [MIT License](LICENSE). An unofficial [Simplified Chinese translation](LICENSE.zh-CN) is provided for reference; the English license is authoritative.
