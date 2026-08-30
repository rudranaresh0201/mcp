"""The dashboard's HTTP/WebSocket layer.

Reads the audit log verimcp already writes and streams it to a browser. It
deliberately does not talk to the proxy directly: verimcp speaks MCP over
stdio to exactly one Host, and a second reader on that pipe would corrupt
the session. The audit log is the supported out-of-band surface (ADR 0004),
and reading a file cannot perturb what it is observing -- which matters for
a tool whose whole claim is that it does not interfere.

`--replay` exists for recording a demo without a live agent attached: it
walks an existing log at a fixed cadence so the same UI can be shown
end-to-end reproducibly.

Optional extra -- `pip install verimcp[dashboard]`. Nothing in verimcp's
proxy path imports this module, so the core install stays free of a web
framework.
"""
# No `from __future__ import annotations` here, deliberately. It stringifies
# every annotation, and FastAPI resolves a route handler's annotations against
# this module's *globals* -- but `WebSocket` is imported inside create_app to
# keep the dependency lazy. With the future import present, FastAPI cannot
# resolve "WebSocket", falls back to treating `socket` as a query parameter,
# and every /ws connection is rejected with close code 1008 "Field required".
# Nothing fails at import or in the REST routes, so this only shows up as a
# websocket that will not open. Python 3.11+ (this project's floor) evaluates
# `X | None` natively, so the future import buys nothing here anyway.

import argparse
import asyncio
from pathlib import Path

from verimcp.dashboard.events import AuditTailer, read_events, summarize

# Polling cadence for the live tail. The audit log is appended once per tool
# call -- human-paced, not high-frequency -- so a quarter second is well
# inside "feels instant" while costing one stat() per tick on an idle system.
POLL_SECONDS = 0.25

_STATIC = Path(__file__).parent / "static"


def create_app(audit_log: Path, replay_delay: float | None = None):
    """Build the ASGI app. Imports FastAPI lazily so that importing this
    module for its constants (or collecting it during a test run without
    the extra installed) doesn't hard-fail on a missing dependency."""
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import FileResponse, JSONResponse

    app = FastAPI(title="verimcp console", docs_url=None, redoc_url=None)

    @app.get("/")
    async def index():
        return FileResponse(_STATIC / "index.html")

    @app.get("/api/events")
    async def api_events():
        """Whole-log snapshot. The WebSocket sends this too on connect; this
        endpoint exists so the log is inspectable with curl and so the UI has
        a fallback if the socket is blocked."""
        events = read_events(audit_log)
        return JSONResponse({"events": events, "summary": summarize(events), "log": str(audit_log)})

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        try:
            if replay_delay is not None:
                await _replay(socket, audit_log, replay_delay)
            else:
                await _follow(socket, audit_log)
        except WebSocketDisconnect:
            return

    return app


async def _send(socket, kind: str, events: list[dict], seen: list[dict], mode: str = "live") -> None:
    """Every frame carries the recomputed summary alongside the new events.

    Recomputing rather than incrementing counters client-side is a deliberate
    trade of a few microseconds for the guarantee that the numbers on screen
    are a pure function of the events on screen -- a drifting counter in a
    tool that exists to be trusted is worse than a slow one.
    """
    await socket.send_json({"type": kind, "mode": mode, "events": events, "summary": summarize(seen)})


async def _follow(socket, audit_log: Path) -> None:
    """Live mode: snapshot, then append as the proxy writes."""
    seen = read_events(audit_log)
    await _send(socket, "snapshot", seen, seen)

    tailer = AuditTailer(audit_log, offset=audit_log.stat().st_size if audit_log.exists() else 0)
    while True:
        new = tailer.poll()
        if new:
            seen.extend(new)
            await _send(socket, "append", new, seen)
        await asyncio.sleep(POLL_SECONDS)


async def _replay(socket, audit_log: Path, delay: float) -> None:
    """Demo mode: walk an existing log one event at a time.

    Starts from an empty snapshot rather than the full log, so a viewer sees
    the console fill from nothing -- the same shape a live session has, which
    is the point of recording it.
    """
    await _send(socket, "snapshot", [], [], mode="replay")
    seen: list[dict] = []
    for event in read_events(audit_log):
        await asyncio.sleep(delay)
        seen.append(event)
        await _send(socket, "append", [event], seen, mode="replay")

    # Hold the connection open on the final frame instead of returning.
    # Returning closes the socket, the client's reconnect fires, and the
    # console flashes "reconnecting" and restarts the replay from empty --
    # directly on top of the last event, which is the one worth looking at.
    while True:
        await asyncio.sleep(3600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verimcp-dashboard", description=__doc__)
    parser.add_argument("--audit-log", type=Path, default=Path("verimcp-audit.jsonl"),
                        help="the log verimcp was started with (--audit-log)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address; stays on loopback by default because an audit log "
                             "contains real tool arguments and should not be served to a network "
                             "without a deliberate choice")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--replay", type=float, metavar="SECONDS", default=None,
                        help="replay the existing log at this cadence instead of following it live")
    args = parser.parse_args(argv)

    try:
        import uvicorn
    except ImportError:
        parser.error("the dashboard extra is not installed -- run: pip install 'verimcp[dashboard]'")

    print(f"verimcp console  ->  http://{args.host}:{args.port}")
    print(f"reading          ->  {args.audit_log}")
    if args.replay is not None:
        print(f"replay mode      ->  {args.replay}s per event")
    uvicorn.run(create_app(args.audit_log, replay_delay=args.replay), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
