from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.registry import get_local_tool_specs


def test_status_tool_spec() -> None:
    spec = get_local_tool_specs().get("get_usage_status")
    assert spec is not None
    assert spec.provider == "local"
    assert spec.kind == "status"
    assert spec.risk == "read_only"
    assert spec.side_effect is False
    assert spec.uses_network is False

def main() -> None:
    test_status_tool_spec()
    print("smoke_status_tool_completion ok")


if __name__ == "__main__":
    main()
