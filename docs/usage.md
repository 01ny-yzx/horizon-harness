# User guide

[← README](../README.md) · [简体中文](usage.zh-CN.md)

Complete the [source setup](../README.md#run-from-source) first. Commands below assume the repository root and an activated virtual environment, unless stated otherwise.

## First launch

```bash
python -m tui
```

Horizon starts a local API service, waits for readiness, then shows the welcome animation. Press Enter to continue. The owned service shuts down when the TUI exits; no cloud server is required for this local workflow. `NO_ANIMATION=1 python -m tui` disables welcome and home animation.

The interface starts in English. Use `/language` to select English, 中文, or 日本語. This changes interface labels and notices, not the text of model replies.

## Connect a model

1. Enter `/models` and choose **Add model** if there is no saved model to reuse.
2. Select a provider with ↑↓, then press Enter to choose a model.
3. Enter the provider API Key when prompted and save it. Built-in provider endpoints are filled automatically.

Saved concrete models appear directly in `/models`; selecting one reuses its connection. `Ctrl+E` in a model picker opens its configuration. Custom OpenAI-compatible endpoints also need a service URL, model ID, and context-token limit. Provider compatibility and streaming depend on the endpoint and model capabilities.

Every selection in `/models` updates the global default, which survives restarts. New sessions record that default when created; existing sessions retain their original model, including when the default is changed from an old conversation. A running task must finish or be interrupted before opening model settings. Until a global default is saved, the `.env` model configuration is used; see [development configuration](development.md#common-configuration).

## Working directory

With the checkout on Python's import path, the working directory defaults to the directory where the launcher is run:

```bash
python -m tui /path/to/project
```

To launch from any folder using a source checkout, use absolute paths:

```bash
/path/to/horizon-harness/.venv/bin/python /path/to/horizon-harness/scripts/horizon.py
```

An optional trailing directory selects another workspace. Without it, the launcher keeps the caller's current directory. This is the source-launcher route; it does not install a global `horizon` command.

The current session directory is shown at the lower right. Restoring a historical conversation uses its saved directory; relative file reads, project searches, Git, and commands run against that directory. A missing saved directory fails explicitly rather than silently using Horizon's source directory.

## Conversations and tabs

- `/session` lists saved conversations; `/new` creates a new conversation.
- Select a tab to switch sessions. Use `+` to create one; hover over a tab to show `×`. Closing a tab does not delete its history.
- Open tab IDs, order, and selected tab are saved across restarts. Unsent drafts and live fragments are retained only during the current TUI process.
- User messages and failure/interruption records remain available when a model request fails. Opening history does not resubmit them or restart a task.
- Titles first use the opening user message; a background model request can summarize it. If that request fails, the fallback title remains.

Replies show the model and elapsed time when the stored response includes that information. Streaming requires a streaming-capable model endpoint. Network reconnects do not resubmit messages; process restarts do not reconnect an old model stream.

## Queue and steer

While a task is running, Enter submits a follow-up to the durable queue. The input clears after successful admission.

- Queued rows appear above the input. Empty-input ↑↓ selects a row.
- With an empty input, Enter opens a steer confirmation for the selected row. Press Enter again to confirm, or Esc to keep it queued.
- Steer delivers the same message as guidance at a cooperative checkpoint; it does not forcibly kill the current task.
- `Ctrl+R` recalls a queued message for editing. Any existing draft is held; `Ctrl+G` restores held drafts.
- Row controls also allow steer, recall, and removal. Already claimed or consumed messages cannot be recalled.

Queue processing is FIFO. A model failure stops the drain and keeps remaining queue items. Restarting alone does not resume them. Each message retains the access mode captured at admission; steering does not create a second copy or change that mode.

## Keyboard and text selection

These are the defaults. `/help` → **Keyboard shortcuts** lets you change common bindings: select an action with ↑↓, press Enter, then press the new chord. Esc cancels capture. Conflicting or reserved keys are rejected; successful changes are saved and applied immediately. **Restore default shortcuts** resets the map.

| Shortcut | Action |
| --- | --- |
| Enter | Send, or queue while busy |
| Shift+Enter / Ctrl+J | Insert a newline |
| Ctrl+R / Ctrl+G | Recall a queued message / restore a held draft |
| Esc | Interrupt at a cooperative boundary |
| Ctrl+N / Ctrl+P | New conversation / history |
| Ctrl+PageUp / Ctrl+PageDown | Previous / next tab |
| Ctrl+W | Close the current tab |
| Tab | Toggle access mode in the composer |
| F1 / Ctrl+Q | Help / quit |

Choose alternatives in settings if your terminal or operating system intercepts a chord. Esc cancels key capture; F1 and Ctrl+Q remain fixed recovery controls.

Drag to select conversation text and release to copy. Multiline and cross-screen selections include intermediate text; successful copying clears the highlight and briefly displays **Copied**. Failure keeps the selection for retry. Titles, tabs, and footer status are excluded. Composer editing uses its own selection behavior. macOS uses the system clipboard as well as the terminal clipboard path.

## Search and browser tools

`/search` configures a **Tavily** search API Key, separate from the model API Key. Leaving an existing key empty keeps it. Saving does not perform a paid search or remotely validate the key; the connection is used by subsequent requests.

Browser automation additionally requires a Playwright browser installation:

```bash
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
```

Available tools depend on configuration, dependencies, and access mode. Model and search services may charge for requests under their own plans.

## Permissions and local data

Default mode is `read_only`. In the composer, Tab switches to `full_access` and back. The neutral `◆` denotes read-only; red `◆` denotes full access. Later toggles do not change permissions for an already admitted message. Full access does not provide container isolation, and interruption does not undo completed side effects.

On macOS the default data directory is `~/Library/Application Support/Horizon Runtime`. On Linux it is `$XDG_DATA_HOME/horizon-runtime`, or `~/.local/share/horizon-runtime` if that variable is unset. `HORIZON_USER_DATA_ROOT` can override it.

| Location under the data directory | Contents |
| --- | --- |
| `horizon.db` | Sessions, messages, events, queue controls, and long-term memory |
| `connections/providers.json` | Model connection configuration and keys |
| `connections/default-model.json` | Global default provider and model identifiers |
| `connections/search.json` | Search configuration and key |
| `tui/preferences.json` | Language and access preference |
| `tui/keybindings.json` | Custom shortcut map |
| `tui/tabs.json` | Open session IDs, order, and selected tab |

Connection directories use mode `0700`; credential files use `0600`. They are local files, not an encrypted vault. Keep them and `.env` out of source control and shared screenshots. Model/search requests still leave your machine for the configured services.

## Connect to an existing API

```bash
python -m tui --url http://127.0.0.1:8000
```

If authentication is enabled, supply the service key through `HORIZON_API_KEY`. Optional flags are `--api-key-header`, `--user`, and `--project`. This mode uses the existing service's directory, cannot be combined with the positional directory argument, and does not stop that service on exit.
