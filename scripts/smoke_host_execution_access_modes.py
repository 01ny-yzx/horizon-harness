"""Smoke checks for local-host command execution and simplified file access."""

from __future__ import annotations

import json
import os
import shlex
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent_access_policy import evaluate_agent_file_access
from core.command_execution_context import get_host_command_context, host_command_context
from core.path_grounding import is_host_absolute_path
from core.sandbox import SandboxManager
from tools.file_tools import read_file, write_file


def _data(result: dict[str, object]) -> dict[str, object]:
    value = result.get("data")
    return value if isinstance(value, dict) else {}


def main() -> None:
    saved = {key: os.environ.get(key) for key in ("AGENT_ACCESS_MODE", "HORIZON_USER_DATA_ROOT")}
    original_cwd = Path.cwd()
    try:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            project = base / "project"
            external = base / "external"
            sandbox = base / "runtime-temp"
            subdir = project / "subdir"
            for directory in (project, external, sandbox, subdir):
                directory.mkdir(parents=True, exist_ok=True)
            fixture = project / "exports" / "manual_regression" / "large.xlsx"
            fixture.parent.mkdir(parents=True)
            fixture.write_bytes(b"fixture")
            os.chdir(project)
            os.environ["AGENT_ACCESS_MODE"] = "full_access"
            os.environ["HORIZON_USER_DATA_ROOT"] = str(base / "userdata")
            manager = SandboxManager()

            with host_command_context(
                project_root=project,
                session_directory=project,
                sandbox_dir=sandbox,
                access_mode="full_access",
                runtime_lane="command_exec",
                task_id="host-smoke",
                source="smoke",
            ):
                context = get_host_command_context()
                assert context.project_root == project.resolve()
                default = manager.run_local_command("pwd")
                assert default["success"] is True
                default_data = _data(default)
                assert default_data["cwd"] == str(project.resolve())
                assert default_data["path_kind"] == "project_default_cwd"
                assert default_data["mode"] == "local_host"

                relative = manager.run_local_command("pwd", cwd="subdir")
                assert relative["success"] is True
                assert _data(relative)["cwd"] == str(subdir.resolve())

                outside = manager.run_local_command("pwd", cwd=str(external))
                assert outside["success"] is True
                assert _data(outside)["logical_root"] == "external_directory"

                traversal = manager.run_local_command("pwd", cwd="../external")
                assert traversal["success"] is True
                assert _data(traversal)["cwd"] == str(external.resolve())

                direct_code = "from pathlib import Path; print(Path('exports/manual_regression/large.xlsx').exists())"
                direct = manager.run_local_command(
                    f"{shlex.quote(sys.executable)} -c {shlex.quote(direct_code)}"
                )
                assert direct["success"] is True
                assert "True" in str(_data(direct)["stdout"])

                write_code = "from pathlib import Path; Path('host.txt').write_text('ok')"
                write = manager.run_local_command(
                    f"{shlex.quote(sys.executable)} -c {shlex.quote(write_code)}",
                    cwd=str(external),
                )
                assert write["success"] is True
                assert (external / "host.txt").read_text(encoding="utf-8") == "ok"

                environment_code = (
                    "import json,os; print(json.dumps({k:os.environ.get(k) for k in "
                    "['TEMP','TMP','TMPDIR','SANDBOX_ROOT','HORIZON_PROJECT_ROOT','PATH']}))"
                )
                environment = manager.run_local_command(
                    f"{shlex.quote(sys.executable)} -c {shlex.quote(environment_code)}"
                )
                env_payload = json.loads(str(_data(environment)["stdout"]).strip())
                for key in ("TEMP", "TMP", "TMPDIR", "SANDBOX_ROOT"):
                    assert env_payload[key] == str(sandbox.resolve())
                assert env_payload["HORIZON_PROJECT_ROOT"] == str(project.resolve())
                assert env_payload["PATH"]

                missing = manager.run_local_command("pwd", cwd=str(base / "missing"))
                assert missing["success"] is False
                assert missing["error_code"] == "cwd_not_found"
                cwd_file = base / "cwd-file"
                cwd_file.write_text("x", encoding="utf-8")
                not_directory = manager.run_local_command("pwd", cwd=str(cwd_file))
                assert not_directory["success"] is False
                assert not_directory["error_code"] == "cwd_not_directory"

                status = manager.status()
                status_text = json.dumps(status, ensure_ascii=False).lower()
                assert "local_host" in status_text
                assert "docker" not in status_text

            for relative_name in (".env", ".git/config", "token.txt"):
                target = external / relative_name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(relative_name, encoding="utf-8")
                assert read_file(str(target))["success"] is True
                written = external / f"{target.name}.written"
                assert write_file(str(written), "ok", overwrite=True)["success"] is True

            os.environ["AGENT_ACCESS_MODE"] = "read_only"
            assert read_file(str(external / "token.txt"))["success"] is True
            denied = write_file(str(external / "denied.txt"), "no", overwrite=True)
            assert denied["success"] is False
            assert evaluate_agent_file_access(
                str(external / "denied.txt"),
                operation="write",
                project_root=project,
            ).code == "agent_access_mode_read_only"

            sentinel = external / "read-only-direct-command.txt"
            with host_command_context(
                project_root=project,
                session_directory=project,
                sandbox_dir=sandbox,
                access_mode="read_only",
                runtime_lane="command_exec",
                task_id="host-smoke-read-only",
                source="smoke",
            ):
                blocked = manager.run_local_command(
                    f"touch {shlex.quote(str(sentinel))}"
                )
                assert blocked["success"] is False
                assert blocked["error_code"] == "agent_access_mode_read_only"
                assert not sentinel.exists()

            with host_command_context(
                project_root=project,
                session_directory=project,
                sandbox_dir=sandbox,
                access_mode="full_access",
                runtime_lane="command_exec",
                task_id="host-smoke-full-access-direct",
                source="smoke",
            ):
                allowed = manager.run_local_command("printf direct-host-ok")
                assert allowed["success"] is True
                assert "direct-host-ok" in str(_data(allowed)["stdout"])

            for value in (r"C:\Users\name", r"D:\资料", r"\\server\share\folder"):
                assert is_host_absolute_path(value)
    finally:
        os.chdir(original_cwd)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_host_execution_access_modes: PASS")


if __name__ == "__main__":
    main()
