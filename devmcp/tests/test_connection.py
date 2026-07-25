import asyncio

from devmcp.connection import Connection


class _FakeWriter:
    """Feeds written bytes straight into the peer's StreamReader — lets two
    Connections talk to each other in-process, with no real socket or pipe."""

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


async def test_request_response_round_trip():
    server, client = _wire_pair()

    async def pump_server_responses():
        # request() only resolves once *something* reads the reply and calls
        # resolve_pending() with it — in production this is Server.run()'s job.
        while True:
            message = await server.recv()
            if message is None:
                return
            server.resolve_pending(message)

    async def client_answers_roots_list():
        message = await client.recv()
        assert message == {"jsonrpc": "2.0", "id": 1, "method": "roots/list", "params": {}}
        await client.send_response(message["id"], result={"roots": [{"uri": "file:///workspace"}]})

    pump_task = asyncio.create_task(pump_server_responses())
    asyncio.create_task(client_answers_roots_list())
    try:
        result = await server.request("roots/list", params={})
    finally:
        pump_task.cancel()

    assert result == {"roots": [{"uri": "file:///workspace"}]}


async def test_resolve_pending_returns_false_for_unrelated_message():
    conn, _ = _wire_pair()

    assert conn.resolve_pending({"jsonrpc": "2.0", "id": 99, "method": "tools/call"}) is False
