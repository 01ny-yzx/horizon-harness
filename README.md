# Horizon Agent Harness

[简体中文](README.zh-CN.md)

Horizon is a local-first AI agent harness. It sits between an LLM and local capabilities, running the agent loop, exposing tools, enforcing permissions, dispatching operations, and returning structured observations to the model.

The model chooses tools and arguments and decides when the task is complete. The harness makes those decisions executable, bounded, and traceable.

> Status: initial public release. Interfaces and capability boundaries may still change. Use the default `read_only` mode until local side effects are intentionally required.

## What the harness owns

- **Agent loop:** model responses, ToolCalls, ToolObservations, and continuation turns.
- **Dynamic tool surface:** tools are exposed according to availability, access mode, and task grants.
- **Execution boundary:** centralized checks for schemas, paths, working directories, URLs, SQL, browser actions, timeouts, and side effects.
- **Reliable dispatch:** repeated delivery of the same call ID replays its prior observation, while response-scoped repetition is checked for loops.
- **Context and data:** session memory, persistent memory, documents, RAG, workspaces, usage, cache, and traces.
- **Multiple entry points:** command-line, FastAPI, and a React/Vite web interface.

## Execution flow

```text
User request
-> harness assembles context and the current tool surface
-> LLM returns prose or structured ToolCalls
-> call-ID replay and repeated-call checks
-> permission and ExecutionBoundary validation
-> real tool dispatch
-> ToolObservation
-> LLM continuation or final response
```

## Built-in capabilities

The local tool set covers:

- file reads, search, writes, and text replacement;
- document ingestion, chunking, and local RAG;
- shell commands and bounded Python execution;
- Git status, diffs, branches, and guarded mutations;
- web search, page fetching, and browser automation;
- session memory, persistent memory, and vector storage;
- multi-user and multi-project workspaces;
- usage, cache, traces, and runtime metrics;
- installation, configuration, lifecycle, and status management for local MCP services.

## Repository map

| Path | Responsibility |
| --- | --- |
| `main.py` | Command-line entry point |
| `core/` | AgentLoop, execution boundary, state, context, memory, and RAG |
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
- `/chat` is synchronous and the frontend waits for the completed task result. SSE, WebSocket, and streaming responses are not currently implemented.
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

Type `exit` or `quit` to leave the session.

### Local API

```bash
python scripts/run_api.py
```

The default address is `http://127.0.0.1:8000`. FastAPI documentation is available at `http://127.0.0.1:8000/docs` while the service is running.

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
