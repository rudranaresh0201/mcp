import asyncio

from verimcp import jsonrpc


async def test_read_message_parses_json_line():
    reader = asyncio.StreamReader()
    reader.feed_data(b'{"jsonrpc": "2.0", "id": 1, "method": "ping"}\n')
    reader.feed_eof()

    message = await jsonrpc.read_message(reader)

    assert message == {"jsonrpc": "2.0", "id": 1, "method": "ping"}


async def test_read_message_returns_none_on_eof():
    reader = asyncio.StreamReader()
    reader.feed_eof()

    assert await jsonrpc.read_message(reader) is None


def test_write_message_sync_flushes_json_line(capsys):
    jsonrpc.write_message_sync({"jsonrpc": "2.0", "id": 1, "result": {}})

    captured = capsys.readouterr()
    assert captured.out == '{"jsonrpc": "2.0", "id": 1, "result": {}}\n'
