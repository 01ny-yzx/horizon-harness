"""Read-only session diagnostics. Never print credentials or conversation text.

Windows native installation: horizon --_horizon-python diagnose_sessions.py
Source checkout: python scripts/diagnose_sessions.py [--database PATH]
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import platform
import sqlite3
import sys

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def default_database() -> Path:
    override = os.environ.get("HORIZON_USER_DATA_ROOT") or os.environ.get("AGENT_USER_DATA_ROOT")
    if override:
        return Path(override) / "horizon.db"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming"))) / "Horizon Runtime/horizon.db"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Horizon Runtime/horizon.db"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "horizon-runtime/horizon.db"


def diagnose(path: Path) -> dict:
    from core.session_context_epoch import _decode
    from core.session_history import _latest_compaction_seq, _load_rows
    from core.session_message import decode_session_message_row
    from core.session_runtime_projection import project_session_history

    result = {"platform": platform.system(), "database_exists": path.is_file(), "sessions": []}
    if not path.is_file():
        return result
    # mode=ro prevents schema creation, migration, and repair.
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        sessions = db.execute("SELECT id, directory FROM session ORDER BY time_updated DESC LIMIT 12").fetchall()
        for index, session in enumerate(sessions, 1):
            sid = session["id"]
            report = {"index": index, "directory_exists": Path(session["directory"]).is_dir()}
            result["sessions"].append(report)
            try:
                last = db.execute("SELECT data FROM event WHERE aggregate_id=? AND type='session.run.finished' ORDER BY seq DESC LIMIT 1", (sid,)).fetchone()
                if last:
                    data = json.loads(last["data"])
                    report["last_run"] = data.get("status")
                    report["error_code"] = data.get("error", {}).get("code")
                choice = db.execute("SELECT type,data FROM event WHERE aggregate_id=? AND type IN ('SessionCreated','session.model.selected') ORDER BY seq DESC LIMIT 1", (sid,)).fetchone()
                report["saved_model_selection"] = bool(choice and (choice["type"] == "session.model.selected" or json.loads(choice["data"]).get("model_selection")))
                epoch = db.execute("SELECT * FROM session_context_epoch WHERE session_id=?", (sid,)).fetchone()
                baseline_seq = -1
                if epoch:
                    report["checking"] = "saved_context_snapshot"
                    baseline_seq = _decode(epoch).baseline_seq
                report["checking"] = "saved_message_structure"
                rows = _load_rows(db, sid, baseline_seq, _latest_compaction_seq(db, sid))
                messages = [decode_session_message_row(row) for row in rows]
                report["message_types"] = dict(Counter(message.type for message in messages))
                unresolved = 0
                for message in messages:
                    if message.type == "assistant" and isinstance(message.data.get("content"), list):
                        unresolved += sum(1 for item in message.data["content"] if isinstance(item, dict) and item.get("type") == "tool" and isinstance(item.get("state"), dict) and item["state"].get("status") in {"pending", "running"})
                report["unsettled_tools"] = unresolved
                report["checking"] = "provider_message_projection"
                if unresolved:
                    # The real runner settles interrupted calls first. Do not do that here.
                    report["history_check"] = "interrupted_tools_require_runner_settlement"
                else:
                    projected = project_session_history(messages)
                    report["history_check"] = "ok"
                    report["provider_message_count"] = len(projected)
                report.pop("checking", None)
            except Exception as exc:
                # Exception text may contain private payloads: report only the class.
                report["history_check"] = "failed"
                report["exception_type"] = type(exc).__name__
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=None)
    args = parser.parse_args()
    try:
        report = diagnose(args.database or default_database())
    except Exception as exc:
        report = {"diagnostic_error_type": type(exc).__name__}
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
