"""Proves the two backend-initiated flows: devmcp asking the Client for its
roots, and devmcp asking the Client's model to sample a completion. Both
directions are the reverse of tools/resources/prompts, so they're tested at
the Connection level (real request/response, real async correlation) rather
than through a real subprocess, to keep the timing fully deterministic."""
import asyncio

from devmcp.connection import Connection
from devmcp.context import ServerContext
from devmcp.sampling import SamplingClient
from devmcp.server import Server
from devmcp.tools.summarize_diff import SummarizeDiffTool
from devmcp.tools.write_file import WriteFileTool


class _FakeWriter:
    def __init__(self, peer_reader: asyncio.StreamReader) -> None:
        self._peer_reader = peer_reader

    def write(self, data: bytes) -> None:
        self._peer_reader.feed_data(data)

    async def drain(self) -> None:
        return None


def _wire_pair() -> tuple[Connection, Connection]:
    reader_a, reader_b = asyncio.StreamReader(), asyncio.StreamReader()
    conn_a = Connection(reader_a, _FakeWriter(reader_b))
    conn_b = Connection(reader_b, _FakeWriter(reader_a))
    return conn_a, conn_b


async def _pump(conn: Connection) -> None:
    """Stands in for Server.run()'s read loop for a bare Connection under test."""
    while True:
        message = await conn.recv()
        if message is None:
            return
        conn.resolve_pending(message)


async def test_roots_negotiation_relocates_repo_root(tmp_git_repo, tmp_path):
    server_conn, client_conn = _wire_pair()
    other_repo = tmp_path / "other_repo"
    other_repo.mkdir()

    server = Server(server_conn, repo_root=tmp_git_repo)
    pump_task = asyncio.create_task(_pump(server_conn))

    async def client_answers_roots_list():
        message = await client_conn.recv()
        assert message["method"] == "roots/list"
        await client_conn.send_response(message["id"], result={"roots": [{"uri": f"file://{other_repo.as_posix()}"}]})

    asyncio.create_task(client_answers_roots_list())
    await server._handle_notification({"jsonrpc": "2.0", "method": "notifications/initialized"})
    await server._roots_task
    pump_task.cancel()

    assert server.ctx.repo_root == other_repo.resolve()

    # Prove it's not just a stored value -- a real tool call now operates on it.
    await WriteFileTool().call({"path": "a.txt", "content": "hi"}, server.ctx)
    assert (other_repo / "a.txt").read_text() == "hi"


async def test_roots_negotiation_falls_back_when_client_declines(tmp_git_repo):
    server_conn, client_conn = _wire_pair()
    server = Server(server_conn, repo_root=tmp_git_repo)
    pump_task = asyncio.create_task(_pump(server_conn))

    async def client_declines():
        message = await client_conn.recv()
        await client_conn.send_response(message["id"], error={"code": -32601, "message": "roots not supported"})

    asyncio.create_task(client_declines())
    await server._handle_notification({"jsonrpc": "2.0", "method": "notifications/initialized"})
    await server._roots_task
    pump_task.cancel()

    assert server.ctx.repo_root == tmp_git_repo


async def test_summarize_diff_tool_uses_real_sampling_round_trip(tmp_git_repo):
    server_conn, client_conn = _wire_pair()
    ctx = ServerContext(repo_root=tmp_git_repo, connection=server_conn)
    pump_task = asyncio.create_task(_pump(server_conn))

    async def client_answers_sampling():
        message = await client_conn.recv()
        assert message["method"] == "sampling/createMessage"
        assert "some diff text" in message["params"]["messages"][0]["content"]["text"]
        await client_conn.send_response(
            message["id"], result={"content": {"type": "text", "text": "Refactors the widget loader."}}
        )

    asyncio.create_task(client_answers_sampling())
    result = await SummarizeDiffTool().call({"diff": "some diff text"}, ctx)
    pump_task.cancel()

    assert result["isError"] is False
    assert result["content"][0]["text"] == "Refactors the widget loader."


async def test_sampling_client_returns_empty_string_if_no_text_content(tmp_git_repo):
    server_conn, client_conn = _wire_pair()
    pump_task = asyncio.create_task(_pump(server_conn))

    async def client_answers_with_no_content():
        message = await client_conn.recv()
        await client_conn.send_response(message["id"], result={})

    asyncio.create_task(client_answers_with_no_content())
    text = await SamplingClient().create_message(server_conn, messages=[])
    pump_task.cancel()

    assert text == ""
