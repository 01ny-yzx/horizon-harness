"""Smoke checks for host path grounding and read metadata."""

from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.file_access_policy import FileAccessPolicy
from core.path_grounding import (
    build_path_context,
    ground_exec_cwd,
    ground_read_path,
    ground_write_path,
    is_host_absolute_path,
)
from core.path_zone_policy import evaluate_file_output_path_zone


def main() -> None:
    original_cwd = Path.cwd()
    saved = {key: os.environ.get(key) for key in ("AGENT_ACCESS_MODE", "HORIZON_USER_DATA_ROOT")}
    try:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            project = base / "project"
            sandbox = base / "runtime-temp"
            external = base / "external"
            project.mkdir()
            sandbox.mkdir()
            external.mkdir()
            os.chdir(project)
            os.environ["AGENT_ACCESS_MODE"] = "full_access"
            os.environ["HORIZON_USER_DATA_ROOT"] = str(base / "userdata")
            context = replace(
                build_path_context(project_root=project),
                sandbox_dir=sandbox,
            )

            default = ground_exec_cwd(None, context=context)
            assert default.allowed
            assert Path(default.resolved_path) == project.resolve()
            assert default.path_kind == "project_default_cwd"

            relative = ground_exec_cwd("../external", context=context)
            assert relative.allowed
            assert Path(relative.resolved_path) == external.resolve()
            assert relative.logical_root == "external_directory"
            assert relative.path_kind == "project_relative_cwd"

            absolute = ground_exec_cwd(str(external), context=context)
            assert absolute.allowed
            assert Path(absolute.resolved_path) == external.resolve()
            assert absolute.logical_root == "external_directory"

            read = ground_read_path("../external/input.txt", context=context)
            assert read.allowed
            assert Path(read.resolved_path) == (external / "input.txt").resolve()
            write = ground_write_path(
                "../external/output.txt",
                context=context,
                default_bare_filename_to_output_dir=False,
            )
            assert write.allowed
            assert Path(write.resolved_path) == (external / "output.txt").resolve()

            for value in (
                r"C:\Users\name",
                r"D:\资料",
                r"\\server\share\folder",
            ):
                assert is_host_absolute_path(value), value

            env_file = external / ".env"
            env_file.write_text("LOCAL_TEST=1", encoding="utf-8")
            git_config = external / ".git" / "config"
            git_config.parent.mkdir()
            git_config.write_text("[core]", encoding="utf-8")
            for target in (env_file, git_config):
                decision = FileAccessPolicy(project).evaluate(str(target), operation="read")
                assert decision.allowed, decision

            zone = evaluate_file_output_path_zone(
                requested_path=str(external / ".env"),
                raw_requested_path=str(external / ".env"),
                project_root=project,
                default_relative_to_output_root=False,
            )
            assert zone.allowed, zone
            traversal_zone = evaluate_file_output_path_zone(
                requested_path="../external/token.txt",
                raw_requested_path="../external/token.txt",
                project_root=project,
                default_relative_to_output_root=False,
            )
            assert traversal_zone.allowed, traversal_zone
    finally:
        os.chdir(original_cwd)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("smoke_path_grounding: PASS")


if __name__ == "__main__":
    main()
