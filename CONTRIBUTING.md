# Contributing to Horizon Runtime

[简体中文](CONTRIBUTING.zh-CN.md)

Thank you for helping improve Horizon Runtime.

## Before you start

- Use a public issue for bugs, feature proposals, and design discussion.
- Use the private process in [SECURITY.md](SECURITY.md) for vulnerabilities.
- Keep changes focused and preserve the current ToolCall execution contract and deterministic safety boundaries.

## Development setup

```bash
git clone https://github.com/01ny-yzx/horizon-runtime.git
cd horizon-runtime
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

For frontend changes:

```bash
cd frontend
npm install
```

Never commit real credentials, `.env`, local MCP configuration, runtime stores, browser artifacts, or generated build output.

## Making a change

1. Create a branch from `main`.
2. Follow the existing project structure and naming style.
3. Keep runtime policy deterministic: metrics and traces must remain observer-only.
4. Add or update a focused Smoke test for behavior changes.
5. Avoid unrelated cleanup in the same pull request.

## Validation

Run the checks relevant to your change. Before opening a pull request, the expected full validation is:

```bash
python scripts/check_git_safety.py
for test in scripts/smoke_*.py; do python "$test" || exit 1; done
python evals/run_evals.py
python -m compileall -q core tools prompts workflows providers api desktop cloud_mcp scripts evals
git diff --check
```

For frontend changes, also run:

```bash
cd frontend
npm run build
```

## Pull requests

Include:

- what changed and why;
- affected runtime or safety behavior;
- tests performed and their results;
- any compatibility or migration notes.

By contributing, you agree that your contribution will be licensed under the repository's MIT License.
