# Development guide

[← README](../README.md) · [简体中文](development.zh-CN.md)

Run commands from the repository root with the Python environment activated. The [user guide](usage.md) covers the TUI; this page covers the other entry points and development settings.

## Local API

```bash
python scripts/run_api.py
```

The default address is `http://127.0.0.1:8000`; interactive endpoint documentation is at `/docs`. `/health` and `/status` are public. Enable API authentication before exposing protected routes beyond local development.

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "message": "Inspect the current project structure",
    "delivery": "steer",
    "resume": true
  }'
```

`POST /chat` acknowledges durable admission, not a final model answer. Its response includes `session_id`, `message_id`, and `admitted_seq`; `resume=true` wakes background execution. Read the transcript or subscribe to events for the output.

| Method | Path | Purpose |
| --- | --- | --- |
| GET / POST | `/session` | List / create sessions |
| GET | `/session/active` | Sessions executing in this process |
| GET | `/session/{id}` | Identity and saved directory |
| GET | `/session/{id}/context` | Canonical model-visible messages |
| GET | `/session/{id}/transcript` | User-facing messages and outcomes |
| GET | `/session/{id}/history` | Durable event history |
| GET | `/session/{id}/events` | SSE live stream and reconnect snapshots |
| POST | `/session/{id}/resume` | Explicitly continue from committed history |
| POST | `/session/{id}/interrupt` | Request cooperative interruption |
| GET | `/session/{id}/pending` | Unclaimed queued / steer inputs |
| POST | `/session/{id}/pending/{message_id}/steer` | Steer an eligible queue item during execution |
| DELETE | `/session/{id}/pending/{message_id}` | Recall / remove an eligible queue item |

Session requests use the service's workspace identity and ownership checks. History `after` is an exclusive sequence cursor. Reading history does not invoke a model or tool. Queue controls can reject an item that has already been claimed or consumed. See [architecture](architecture.md) for admission, claims, and recovery semantics.

## Web interface

The `frontend` directory contains the public Horizon product and download website. It runs independently of the API:

```bash
cd frontend
npm install
npm run dev
```

Website development requires Node.js 22.12+. Run `npm run build` to generate static files in `frontend/dist`. The previous agent GUI has been removed; the website only reads public GitHub release metadata and does not access local API keys or sessions. See [website notes](../frontend/README.md).

## Legacy CLI

```bash
python main.py
```

This creates a durable session and waits synchronously for each turn's final output. The session ID can be used with the API. The TUI remains the primary interactive entry point.

## Common configuration

TUI model and search setup normally does not require editing `.env`. For development or advanced settings:

```bash
cp .env.example .env
```

Keep local credentials in the ignored `.env` file or app-managed connection files. [`.env.example`](../.env.example) is the configuration reference.

| Variable | Purpose |
| --- | --- |
| `LLM_PROVIDER` | Model adapter; normally `openai_compatible` |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | Optional environment-based model connection |
| `AGENT_ACCESS_MODE` | Default access policy (`read_only` / `full_access`) |
| `API_AUTH_ENABLED`, `API_KEYS` | Service API authentication |
| `HORIZON_USER_DATA_ROOT` | Override the local data directory |
| `WEB_SEARCH_PROVIDER` | Search provider selection |
| `MCP_ENABLED` | Optional local MCP management/runtime configuration |

Local MCP lifecycle/registry infrastructure does not mean arbitrary MCP tools are currently available to the model: AgentLoop builds its normal tool surface from repository-local schemas. Cloud MCP remains preview infrastructure. Do not advertise either as complete remote-tool execution support.

For optional browser tools:

```bash
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
```

## Validation

Repository safety check:

```bash
python scripts/check_git_safety.py
```

Focused offline TUI checks:

```bash
python -m unittest scripts.smoke_tui_ui scripts.smoke_tui_queue scripts.smoke_tui_shortcuts scripts.smoke_tui_copy_selection -q
```

Durable queue / session checks:

```bash
PYTHONPATH=. python scripts/smoke_session_queue_controls.py
PYTHONPATH=. python scripts/smoke_session_runtime.py
```

The suite is organized primarily as `scripts/smoke_*.py`, not a single exhaustive pytest suite. Check a script's environment and optional dependencies before selecting broader tests. Behavioral evals are in [evals/run_evals.py](../evals/run_evals.py).

## Contributing and security

Read [CONTRIBUTING.md](../CONTRIBUTING.md) and [SECURITY.md](../SECURITY.md). Do not commit `.env`, connection keys, local user data, or generated runtime output. The host command runner is not an isolation boundary; see [current boundaries](architecture.md#current-boundaries).

## Native terminal bundles

Install `requirements-build.txt` into a dedicated Python 3.12 environment, then run:

```sh
python scripts/build_bundle.py
```

Run this on each target OS and CPU architecture. The script builds a console executable, checks resources, Python tool execution, authenticated local service and UI in a temporary directory with Python removed from PATH, then creates a versioned archive and SHA-256 file under `dist/bundles/`. macOS archives preserve symbolic links; Windows produces a ZIP. Keep the entire extracted `horizon` directory together.

The manual **Native terminal bundles** GitHub Actions workflow builds macOS arm64, macOS x64 and Windows x64 artifacts. It does not publish a release. Only public provider catalogs and interface resources are explicitly bundled. Credentials, `.env`, sessions and private configuration are excluded; runtime data remains in the user profile. The optional Playwright browser feature is not included in these initial native bundles.

These are test builds. macOS uses ad-hoc signing; Developer ID signing/notarization and Windows release signing are not configured. Installation-channel code is described below; public distribution is still a separate publishing step.

## Installation channels

The installation-channel code is prepared but has not been published. Do not advertise public installation commands until the matching GitHub release assets and npm package exist.

Run the manual **Native terminal bundles** workflow. Each job builds and tests its native executable, records its minimum macOS version where applicable, and tests the Node downloader against a loopback server. The final job assembles all three targets into an npm tarball, a macOS `install.sh`, a Homebrew formula and a release manifest. It does not publish anything.

For local preparation using available native artifacts:

```sh
python scripts/prepare_installers.py
node --test distribution/tests/npm.test.cjs
npm pack ./dist/distribution/npm --pack-destination ./dist/distribution
```

A public release should require all intended targets using `--require-target macos-arm64 --require-target macos-x64 --require-target windows-x64`. Each archive needs its `.sha256` and `.build.json` sidecars. A local Mac build may require a much newer macOS than a CI build: the minimum is read from the bundled Mach-O images rather than guessed. Missing targets are omitted from local test installers, never assigned placeholder hashes.

Before publishing, upload matching native archives, checksums and the generated `install.sh` to GitHub Release `v<VERSION>`, then publish the prepared npm package (not `distribution/npm`, which is deliberately private). Publish the generated `Formula/horizon.rb` in the `Formula` directory of `01ny-yzx/homebrew-tap`; this enables automatic tap discovery. Review these files and package contents first; the workflow has read-only repository permissions and cannot publish a release.

After publication, the intended user commands are:

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

npm supports macOS arm64/x64 and Windows x64 when those assets are present. curl and Homebrew are macOS-only at this stage. The curl installer uses `~/.local/share/horizon/versions` and `~/.local/bin`; if the latter is not on PATH, add it once. It refuses to replace an unrelated existing `horizon` command and never edits shell profiles. `HORIZON_INSTALL_ROOT` and `HORIZON_BIN_DIR` select alternate absolute paths. npm and curl verify embedded SHA-256 values before unpacking; installation is staged and protected against concurrent writers. A crashed install may leave a lock: remove only the reported lock after confirming the installer is no longer running. API keys and session data stay outside all installation directories.

Developer tests can set `HORIZON_DOWNLOAD_BASE` to a loopback asset server. Normal downloads use HTTPS GitHub release URLs. `HORIZON_SKIP_DOWNLOAD=1` skips npm postinstall; the first launch then completes installation. This is useful for package inspection, not an offline installer.

Source exports use `python scripts/build_source.py`, producing `dist/horizon-<version>-source.zip`. All native archives use `horizon-<version>-<platform>-<arch>`; the version comes from `tui/version.py`.
