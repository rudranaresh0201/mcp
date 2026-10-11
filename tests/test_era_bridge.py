"""A stateless (2026-07-28) Host served through verimcp by a LEGACY backend --
the combination the spec's compatibility matrix says fails on its own.

Unit tests for the pure rules in era_bridge.py, then real `verimcp`
processes in front of real backends over real stdio: devmcp (legacy, answers
server/discover with an error), the lying server (legacy, never answers it at
all), and the stateless fixture (modern -- must NOT be bridged).
"""
import asyncio
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from verimcp import era_bridge, stateless

FIXTURES = Path(__file__).parent / "fixtures"
MODERN_META = {stateless.VERSION_KEY: "2026-07-28", stateless.CAPABILITIES_KEY: {}}


# -- the rules --------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "era"),
    [
        ({"result": {"supportedVersions": ["2026-07-28"]}}, "modern"),
        ({"error": {"code": -32022, "message": "Unsupported protocol version"}}, "modern"),
        ({"error": {"code": -32601, "message": "method not found"}}, "legacy"),
        ({"error": {"code": -32602, "message": "invalid params"}}, "legacy"),
        (None, "legacy"),  # silence
    ],
)
def test_era_of_discover_reply_follows_the_spec(reply, era):
    assert era_bridge.era_of_discover_reply(reply) == era


def test_discover_result_describes_the_legacy_backend_honestly():
    init = {
        "protocolVersion": "2025-06-18",
        "capabilities": {"tools": {"listChanged": True}, "resources": {"subscribe": True}, "logging": {}},
        "serverInfo": {"name": "devmcp", "version": "0.1.0"},
        "instructions": "use write_file for files",
    }
    result = era_bridge.discover_result(init)
    assert result["resultType"] == "complete"
    assert result["supportedVersions"] == ["2026-07-28"]
    # listChanged/subscribe promise notifications the bridge doesn't deliver;
    # logging is deprecated in 2026-07-28.
    assert result["capabilities"] == {"tools": {}, "resources": {}}
    assert result["_meta"][era_bridge.SERVER_INFO_KEY] == {"name": "devmcp", "version": "0.1.0"}
    assert result["instructions"] == "use write_file for files"


def test_backend_requests_are_answered_for_the_host(tmp_path):
    roots = era_bridge.answer_backend_request({"id": 1, "method": "roots/list"}, tmp_path)
    assert roots["result"]["roots"][0]["uri"] == tmp_path.as_uri()
    assert "error" in era_bridge.answer_backend_request({"id": 2, "method": "roots/list"}, None)
    assert era_bridge.answer_backend_request({"id": 3, "method": "ping"}, None)["result"] == {}
    sampling = era_bridge.answer_backend_request({"id": 4, "method": "sampling/createMessage"}, tmp_path)
    assert sampling["error"]["code"] == -32601


def test_stamping_adds_complete_but_never_overwrites():
    assert era_bridge.stamp_result_type({"id": 1, "result": {}})["result"]["resultType"] == "complete"
    kept = era_bridge.stamp_result_type({"id": 1, "result": {"resultType": "input_required"}})
    assert kept["result"]["resultType"] == "input_required"
    assert "result" not in era_bridge.stamp_result_type({"id": 1, "error": {"code": 1}})


# -- real processes -----------------------------------------------------------


class Host:
    """A stateless Host: never sends initialize, describes itself on every
    request, and records every message verimcp writes to it."""

    def __init__(self, proc):
        self.proc = proc
        self.received: list[dict] = []
        self._next = 0

    async def request(self, method: str, params: dict | None = None, meta: dict | None = None) -> dict:
        self._next += 1
        message = {
            "jsonrpc": "2.0", "id": self._next, "method": method,
            "params": {**(params or {}), "_meta": meta or MODERN_META},
        }
        self.proc.stdin.write((json.dumps(message) + "\n").encode())
        await self.proc.stdin.drain()
        while True:
            line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=30)
            reply = json.loads(line)
            self.received.append(reply)
            if reply.get("id") == self._next and "method" not in reply:
                return reply

    def requests_received(self) -> list[dict]:
        """Requests verimcp sent the Host. A modern server must never send one."""
        return [m for m in self.received if "method" in m and "id" in m]


async def _verimcp(*args: str):
    return await asyncio.create_subprocess_exec(
        sys.executable, "-m", "verimcp.cli", *args,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
    )


async def _stop(proc):
    proc.stdin.close()
    try:
        await asyncio.wait_for(proc.wait(), timeout=10)
    except TimeoutError:
        proc.kill()
        await proc.wait()


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


async def test_a_stateless_host_uses_legacy_devmcp_through_verimcp_and_writes_are_verified(tmp_path):
    import shutil

    devmcp = shutil.which("devmcp")
    if devmcp is None:
        pytest.skip("devmcp is not installed")
    repo = _repo(tmp_path)
    proc = await _verimcp("--root", str(repo), "--receipts", "--", devmcp, "--repo-path", str(repo))
    host = Host(proc)
    try:
        discover = await host.request("server/discover")
        assert discover["result"]["resultType"] == "complete"
        assert discover["result"]["supportedVersions"] == ["2026-07-28"]
        assert discover["result"]["_meta"][era_bridge.SERVER_INFO_KEY]["name"] == "devmcp"

        listed = await host.request("tools/list")
        assert listed["result"]["resultType"] == "complete"
        assert "write_file" in [t["name"] for t in listed["result"]["tools"]]

        wrote = await host.request("tools/call", {"name": "write_file", "arguments": {"path": "hello.txt", "content": "hi"}})
        assert wrote["result"]["resultType"] == "complete"
        assert wrote["result"]["isError"] is False
        assert (repo / "hello.txt").read_text() == "hi"
        assert "receipt" in json.dumps(wrote["result"]).lower()  # verified, not just forwarded

        # devmcp asks for roots/list after initialize; the stateless Host never sees it.
        assert host.requests_received() == []
    finally:
        await _stop(proc)


async def test_a_lie_from_a_legacy_server_is_still_caught_for_a_stateless_host(tmp_path):
    repo = _repo(tmp_path)
    proc = await _verimcp(
        "--root", str(repo), "--",
        sys.executable, str(FIXTURES / "lying_server.py"), "--lie", "write_file_wrong_content", "--repo-path", str(repo),
    )
    host = Host(proc)
    try:
        # The liar answers server/discover with -32601: legacy, so bridged.
        claimed = await host.request("tools/call", {"name": "write_file", "arguments": {"path": "x.txt", "content": "hi"}})
        assert claimed["result"]["resultType"] == "complete"
        assert claimed["result"]["isError"] is True  # the server said success; the file isn't there
        assert not (repo / "x.txt").exists()
    finally:
        await _stop(proc)


async def test_a_modern_backend_is_passed_through_not_bridged(tmp_path):
    proc = await _verimcp("--", sys.executable, str(FIXTURES / "stateless_server.py"))
    host = Host(proc)
    try:
        discover = await host.request("server/discover")
        # The backend's own DiscoverResult, not one verimcp built.
        assert discover["result"]["_meta"][era_bridge.SERVER_INFO_KEY]["name"] == "stateless-fixture"
        touched = await host.request("tools/call", {"name": "touch", "arguments": {"path": str(tmp_path / "t")}})
        assert touched["result"]["isError"] is False and (tmp_path / "t").exists()
    finally:
        await _stop(proc)


async def test_an_unknown_protocol_version_is_refused_the_spec_way(tmp_path):
    proc = await _verimcp("--", sys.executable, str(FIXTURES / "stateless_server.py"))
    host = Host(proc)
    try:
        reply = await host.request("tools/list", meta={stateless.VERSION_KEY: "1999-01-01"})
        assert reply["error"]["code"] == -32022
        assert reply["error"]["data"] == {"supported": ["2026-07-28"], "requested": "1999-01-01"}
    finally:
        await _stop(proc)


async def test_a_backend_that_stays_silent_on_discover_is_bridged_after_the_timeout(tmp_path):
    """Spec: no answer within a reasonable timeout means legacy."""
    silent = tmp_path / "silent_legacy.py"
    silent.write_text(textwrap.dedent("""
        import json, sys
        for line in sys.stdin:
            m = json.loads(line)
            if m.get("method") == "initialize":
                r = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "quiet"}}
                print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
            elif m.get("method") == "tools/list":
                print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": {"tools": []}}), flush=True)
            # server/discover: silence
    """))
    proc = await _verimcp("--discover-timeout", "1", "--", sys.executable, str(silent))
    host = Host(proc)
    try:
        start = asyncio.get_running_loop().time()
        discover = await host.request("server/discover")
        assert asyncio.get_running_loop().time() - start >= 1  # it really waited
        assert discover["result"]["_meta"][era_bridge.SERVER_INFO_KEY]["name"] == "quiet"
        listed = await host.request("tools/list")
        assert listed["result"] == {"tools": [], "resultType": "complete"}
    finally:
        await _stop(proc)
