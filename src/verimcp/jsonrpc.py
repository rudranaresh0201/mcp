"""Newline-delimited JSON-RPC framing for the stdio transport."""
import json
import sys
from typing import Any


async def read_message(stream) -> dict[str, Any] | None:
    """Read one JSON-RPC message (one line) from an asyncio StreamReader.

    Returns None on EOF (the other side closed the pipe).
    """
    line = await stream.readline()
    if not line:
        return None
    return json.loads(line)


def write_message(stream, message: dict[str, Any]) -> None:
    """Write one JSON-RPC message as a single line to a writable stream."""
    stream.write((json.dumps(message) + "\n").encode())


def write_message_sync(message: dict[str, Any]) -> None:
    """Write one JSON-RPC message to our own real stdout (server side)."""
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()
