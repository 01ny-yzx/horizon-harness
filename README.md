# Horizon Runtime

[简体中文](README.zh-CN.md)

Horizon Runtime is a local-first AI agent runtime with structured tool calling, deterministic execution safety boundaries, and LLM-driven continuation.

> Status: initial public release. Interfaces may still change. Keep the default read-only access mode until you intentionally need local side effects.

## Highlights

- Structured ToolCalls: the model selects the tool and supplies its arguments.
- Deterministic safety: schema, access mode, path, working directory, timeout, browser, database, and MCP checks remain runtime responsibilities.
- Execution fidelity: distinct call IDs execute independently, while duplicate delivery of the same call ID replays its prior observation.
- Response-scoped doom-loop protection through an explicit permission boundary.
- ToolObservation-driven continuation until the model returns a final response.
- Local tools for files, documents, shell execution, Git, web search/fetch, browser automation, memory, RAG, workspaces, usage, and cache management.
- Optional MCP runtime, FastAPI service, and React/Vite desktop-style web interface.

## Runtime flow

```text
LLM structured ToolCall
-> same-call replay check
-> response-scoped doom-loop permission
-> execution boundary
-> real tool dispatch
-> ToolObservation
-> LLM continuation or final response
```

## Requirements

- Python 3.10 or newer; the project is tested with Python 3.12.
- Node.js 18 or newer for the optional frontend.
- An OpenAI-compatible LLM endpoint and API key.

Browser tools additionally require a Playwright browser installation.

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

Type `exit` or `quit` to leave the interactive session.

### Local API

```bash
python scripts/run_api.py
```

The default address is `http://127.0.0.1:8000`. FastAPI documentation is available at `http://127.0.0.1:8000/docs` while the service is running.

### Frontend

Start the API first, then run:

```bash
cd frontend
npm install
npm run dev
```

The frontend uses `http://127.0.0.1:8000` by default and lets you change the backend URL, API key, user ID, and project ID in Settings.

### Browser support

After installing Python dependencies, install Chromium if you want to use browser tools:

```bash
python -m playwright install chromium
```

## Configuration

Use [.env.example](.env.example) as the authoritative public template. Important settings include:

| Variable | Purpose | Default |
| --- | --- | --- |
| `LLM_PROVIDER` | Provider adapter | `openai_compatible` |
| `LLM_BASE_URL` | Model API endpoint | empty |
| `LLM_MODEL` | Model identifier | empty |
| `LLM_API_KEY` | Local model credential | empty |
| `AGENT_ACCESS_MODE` | `read_only` or `full_access` | `read_only` |
| `API_AUTH_ENABLED` | Enable API key protection | `false` |
| `API_KEYS` | Comma-separated service API keys | development placeholder |
| `WEB_SEARCH_PROVIDER` | `auto`, `searxng`, `tavily`, or `disabled` | `auto` |
| `MCP_ENABLED` | Enable local MCP runtime loading | `false` |

Do not commit `.env`, frontend environment files, local MCP configuration, credentials, or runtime stores.

## Tests

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
- Report vulnerabilities according to [SECURITY.md](SECURITY.md); do not publish exploit details in a public issue.

## License

Horizon Runtime is licensed under the [MIT License](LICENSE). An unofficial [Simplified Chinese translation](LICENSE.zh-CN) is provided for reference; the English license is authoritative.
