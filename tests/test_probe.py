"""`verimcp probe` runs the 2026-07-28 spec's stdio era detection: server/discover
first, initialize as the fallback. One real server per era."""
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from verimcp.probe import probe

FIXTURES = Path(__file__).parent / "fixtures"
MODERN = [sys.executable, str(FIXTURES / "stateless_server.py")]


def test_a_modern_server_is_recognised_from_its_discover_result():
    result = probe(MODERN, timeout=10)
    assert result.era == "modern"
    assert result.supported_versions == ["2026-07-28"]
    assert result.server_info["name"] == "stateless-fixture"
    assert result.tools == ["touch"]
    assert result.result_type_on_tools_list == "complete"


def test_a_legacy_server_falls_back_to_initialize(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    devmcp = shutil.which("devmcp")
    if devmcp is None:
        pytest.skip("devmcp is not installed")
    result = probe([devmcp, "--repo-path", str(tmp_path)], timeout=20)
    assert result.era == "legacy"
    assert result.discover.startswith("error")
    assert result.protocol_version and result.protocol_version < "2026-07-28"
    assert "write_file" in result.tools


def test_a_server_that_never_answers_discover_is_legacy_not_stuck(tmp_path):
    # Spec: "does not respond within a reasonable timeout: the server is legacy".
    server = tmp_path / "silent_legacy.py"
    server.write_text(textwrap.dedent('''
        import json, sys
        for line in sys.stdin:
            m = json.loads(line)
            if m.get("method") == "initialize":
                r = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "quiet"}}
                print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
            elif m.get("method") == "tools/list":
                print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": {"tools": []}}), flush=True)
            # anything else, server/discover included: silence
    '''))
    result = probe([sys.executable, str(server)], timeout=2)
    assert result.era == "legacy" and result.discover == "no answer"
    assert result.server_info["name"] == "quiet"


def test_unsupported_version_means_modern_and_no_fallback(tmp_path):
    server = tmp_path / "future.py"
    server.write_text(textwrap.dedent('''
        import json, sys
        for line in sys.stdin:
            m = json.loads(line)
            if m.get("method") == "initialize":
                raise SystemExit("probe must not fall back to initialize")
            err = {"code": -32022, "message": "Unsupported protocol version",
                   "data": {"supported": ["2027-01-01"], "requested": "2026-07-28"}}
            print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "error": err}), flush=True)
    '''))
    result = probe([sys.executable, str(server)], timeout=5)
    assert result.era == "modern"
    assert result.supported_versions == ["2027-01-01"]
    assert result.protocol_version is None


def test_the_cli_prints_a_verdict_and_refuses_a_missing_command():
    ok = subprocess.run(
        [sys.executable, "-m", "verimcp.cli", "probe", "--", *MODERN],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert ok.returncode == 0, ok.stderr
    assert "era:            MODERN" in ok.stdout and "can use this server" in ok.stdout

    missing = subprocess.run([sys.executable, "-m", "verimcp.cli", "probe"], capture_output=True, text=True, timeout=60, check=False)
    assert missing.returncode == 2 and "after --" in missing.stderr


@pytest.mark.parametrize("bad", [["no-such-mcp-server-xyz"]])
def test_a_command_that_does_not_exist_is_a_clear_error(bad):
    run = subprocess.run([sys.executable, "-m", "verimcp.cli", "probe", "--", *bad], capture_output=True, text=True, timeout=60, check=False)
    assert run.returncode == 2 and "could not start" in run.stderr


def test_a_server_that_dies_is_reported_not_crashed_on():
    result = probe([sys.executable, "-c", "import sys; sys.exit(3)"], timeout=5)
    assert result.era == "unreachable" and "exited" in result.discover
