"""`verimcp wrap` / `verimcp unwrap`: put verimcp in front of every MCP server
a developer already has configured, in one command, and take it back out.

A proxy only helps if tool calls actually pass through it, and hand-editing
each app's MCP JSON is exactly the setup cost that stops anyone trying it.
wrap reads the config files of the MCP apps on this machine and rewrites
each server entry so the app launches verimcp, which launches (or connects
to) the original server:

    stdio:  {"command": "npx", "args": ["srv"]}
        ->  {"command": "verimcp", "args": ["--audit-log", LOG, "--", "npx", "srv"]}
    http:   {"type": "http", "url": U, "headers": {"Authorization": "Bearer t"}}
        ->  {"type": "stdio", "command": "verimcp",
             "args": ["--audit-log", LOG, "--backend-url", U,
                      "--header-from-env", "Authorization=VERIMCP_HEADER_1"],
             "env": {"VERIMCP_HEADER_1": "Bearer t"}}

Rules, each one there because the alternative breaks or leaks something:

  - Already wrapped -> skipped, so running wrap twice changes nothing.
  - Header values move into the entry's env block, never into args: command
    lines are visible to other processes on the machine, env blocks are not.
  - A remote server with no headers is skipped unless --all-http. It most
    likely authenticates through the app's own OAuth flow, which verimcp
    cannot perform; wrapping it would silently break a working server.
  - Legacy SSE-transport servers are skipped: verimcp speaks Streamable HTTP.
  - Every file is backed up before it is written, and the original entry is
    recorded in ~/.verimcp/wrap-state.json so unwrap restores it exactly --
    but only if the entry is still what wrap wrote. An entry the user has
    edited since is left alone rather than clobbered.
  - Writes are atomic (temp file + os.replace), so a crash mid-write can't
    leave a half-written config behind.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MARKER_ARG = "--audit-log"


@dataclass
class ServerMap:
    """One `mcpServers` object inside one config file."""

    client: str
    path: Path
    location: list[str]  # JSON keys from the file root down to the mcpServers object
    label: str


@dataclass
class Change:
    server_map: ServerMap
    name: str
    action: str  # "wrapped" | "skipped" | "restored"
    reason: str = ""
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


@dataclass
class Plan:
    changes: list[Change] = field(default_factory=list)
    backups: list[Path] = field(default_factory=list)


def _appdata(home: Path, home_overridden: bool) -> Path:
    if sys.platform == "win32":
        if not home_overridden and os.environ.get("APPDATA"):
            return Path(os.environ["APPDATA"])
        return home / "AppData" / "Roaming"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support"
    return home / ".config"


def discover(home: Path, home_overridden: bool = False) -> list[ServerMap]:
    """Every mcpServers object in the MCP apps' config files that exist."""
    found: list[ServerMap] = []

    claude_code = home / ".claude.json"
    if claude_code.exists():
        data = _load(claude_code)
        if isinstance(data.get("mcpServers"), dict):
            found.append(ServerMap("Claude Code", claude_code, ["mcpServers"], "user scope"))
        for project, pdata in (data.get("projects") or {}).items():
            if isinstance(pdata, dict) and isinstance(pdata.get("mcpServers"), dict) and pdata["mcpServers"]:
                found.append(
                    ServerMap("Claude Code", claude_code, ["projects", project, "mcpServers"], f"project {project}")
                )

    for client, path in (
        ("Claude Desktop", _appdata(home, home_overridden) / "Claude" / "claude_desktop_config.json"),
        ("Cursor", home / ".cursor" / "mcp.json"),
    ):
        if path.exists() and isinstance(_load(path).get("mcpServers"), dict):
            found.append(ServerMap(client, path, ["mcpServers"], "global"))
    return found


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _servers(data: dict[str, Any], location: list[str]) -> dict[str, Any]:
    node = data
    for key in location:
        node = node[key]
    return node


def is_wrapped(entry: dict[str, Any]) -> bool:
    command = Path(str(entry.get("command", ""))).name.lower()
    args = [str(a) for a in entry.get("args") or []]
    if command in ("verimcp", "verimcp.exe"):
        return True
    return command in ("uvx", "uvx.exe") and bool(args) and args[0] == "verimcp"


def _running_verimcp() -> str | None:
    """The absolute path of the verimcp command this process was installed
    with -- its virtualenv's, if it runs from one -- so apps that launch MCP
    servers without the user's shell PATH (Claude Desktop) still find it."""
    scripts = Path(sys.executable).parent
    for candidate in (scripts / "verimcp.exe", scripts / "verimcp", scripts / "Scripts" / "verimcp.exe"):
        if candidate.exists():
            return str(candidate)
    return shutil.which("verimcp")


def launcher_prefix(launcher: str) -> tuple[str, list[str]]:
    if launcher == "auto":
        installed = _running_verimcp()
        return (installed, []) if installed else ("uvx", ["verimcp"])
    if launcher == "uvx":
        return "uvx", ["verimcp"]
    return "verimcp", []


def wrap_entry(
    entry: dict[str, Any], audit_log: Path, launcher: str, all_http: bool
) -> tuple[dict[str, Any] | None, str]:
    """Return (wrapped entry, "") or (None, reason it was skipped)."""
    if is_wrapped(entry):
        return None, "already wrapped"

    kind = entry.get("type") or ("http" if "url" in entry else "stdio")
    command, prefix = launcher_prefix(launcher)
    base_args = [*prefix, "--receipts", MARKER_ARG, str(audit_log)]

    if kind == "stdio":
        if not entry.get("command"):
            return None, "stdio entry has no command"
        wrapped = copy.deepcopy(entry)
        wrapped["command"] = command
        wrapped["args"] = [*base_args, "--", entry["command"], *(entry.get("args") or [])]
        return wrapped, ""

    if kind in ("http", "streamable-http"):
        headers = entry.get("headers") or {}
        if not headers and not all_http:
            return None, "remote server with no headers: probably signs in through the app (OAuth), which verimcp can't do yet -- use --all-http if it needs no auth"
        wrapped = {k: copy.deepcopy(v) for k, v in entry.items() if k not in ("type", "url", "headers")}
        if "type" in entry:
            wrapped["type"] = "stdio"
        env = dict(wrapped.get("env") or {})
        args = [*base_args, "--backend-url", entry["url"]]
        for index, (name, value) in enumerate(headers.items(), start=1):
            env_var = f"VERIMCP_HEADER_{index}"
            env[env_var] = value
            args += ["--header-from-env", f"{name}={env_var}"]
        wrapped["command"] = command
        wrapped["args"] = args
        wrapped["env"] = env
        return wrapped, ""

    return None, f"{kind!r} transport isn't supported (verimcp speaks stdio and Streamable HTTP)"


def _write_atomic(path: Path, data: Any) -> None:
    tmp = path.with_name(f"{path.name}.verimcp-tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _state_path(home: Path) -> Path:
    return home / ".verimcp" / "wrap-state.json"


def _load_state(home: Path) -> list[dict[str, Any]]:
    path = _state_path(home)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _save_state(home: Path, records: list[dict[str, Any]]) -> None:
    path = _state_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, records)


def wrap(
    home: Path, audit_log: Path, launcher: str = "auto", all_http: bool = False,
    dry_run: bool = False, home_overridden: bool = False,
) -> Plan:
    plan = Plan()
    state = _load_state(home)
    by_file: dict[Path, list[ServerMap]] = {}
    for server_map in discover(home, home_overridden):
        by_file.setdefault(server_map.path, []).append(server_map)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    for path, maps in by_file.items():
        data = _load(path)
        file_changed = False
        for server_map in maps:
            servers = _servers(data, server_map.location)
            for name, entry in list(servers.items()):
                wrapped, reason = wrap_entry(entry, audit_log, launcher, all_http)
                if wrapped is None:
                    plan.changes.append(Change(server_map, name, "skipped", reason, before=entry))
                    continue
                plan.changes.append(Change(server_map, name, "wrapped", before=entry, after=wrapped))
                servers[name] = wrapped
                file_changed = True
                state.append({"file": str(path), "location": server_map.location, "name": name,
                              "original": entry, "wrapped": wrapped})
        if file_changed and not dry_run:
            backup = path.with_name(f"{path.name}.verimcp-backup-{stamp}")
            shutil.copy2(path, backup)
            plan.backups.append(backup)
            _write_atomic(path, data)

    if not dry_run and any(c.action == "wrapped" for c in plan.changes):
        _save_state(home, state)
    return plan


def unwrap(home: Path, dry_run: bool = False) -> Plan:
    plan = Plan()
    remaining: list[dict[str, Any]] = []
    records = _load_state(home)
    by_file: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_file.setdefault(record["file"], []).append(record)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    for file, file_records in by_file.items():
        path = Path(file)
        if not path.exists():
            remaining.extend(file_records)
            continue
        data = _load(path)
        file_changed = False
        for record in file_records:
            server_map = ServerMap("", path, record["location"], " / ".join(record["location"][:-1]) or "global")
            try:
                servers = _servers(data, record["location"])
            except (KeyError, TypeError):
                plan.changes.append(Change(server_map, record["name"], "skipped", "its config section no longer exists"))
                continue
            current = servers.get(record["name"])
            if current != record["wrapped"]:
                plan.changes.append(Change(server_map, record["name"], "skipped",
                                           "edited since wrap -- left as is", before=current))
                remaining.append(record)
                continue
            servers[record["name"]] = record["original"]
            plan.changes.append(Change(server_map, record["name"], "restored",
                                       before=current, after=record["original"]))
            file_changed = True
        if file_changed and not dry_run:
            backup = path.with_name(f"{path.name}.verimcp-backup-{stamp}")
            shutil.copy2(path, backup)
            plan.backups.append(backup)
            _write_atomic(path, data)

    if not dry_run:
        _save_state(home, remaining)
    return plan


def _describe(entry: dict[str, Any] | None) -> str:
    if not entry:
        return ""
    if "url" in entry:
        return f"{entry['url']}"
    return " ".join([str(entry.get("command", "")), *[str(a) for a in entry.get("args") or []]])


def format_plan(plan: Plan, verb: str, dry_run: bool, audit_log: Path | None) -> str:
    lines: list[str] = []
    if not plan.changes:
        return "No MCP server configs found (looked for Claude Code, Claude Desktop and Cursor)."
    for change in plan.changes:
        where = f"{change.server_map.client} ({change.server_map.label})".replace("() ", "").strip()
        if change.action in ("wrapped", "restored"):
            label = {"wrapped": "would wrap", "restored": "would restore"}[change.action] if dry_run else change.action
            lines.append(f"  {label:>15}  {change.name:<24} {where}")
            lines.append(f"{'':>19}{'':<24} {_describe(change.before)}")
            lines.append(f"{'':>19}{'':<24} -> {_describe(change.after)}")
        else:
            lines.append(f"  {'skipped':>15}  {change.name:<24} {where}")
            lines.append(f"{'':>19}{'':<24} {change.reason}")
    done = sum(c.action in ("wrapped", "restored") for c in plan.changes)
    lines.append("")
    if dry_run:
        lines.append(f"Dry run: {done} server(s) would be {verb}. Nothing was written.")
    else:
        lines.append(f"{done} server(s) {verb}.")
        for backup in plan.backups:
            lines.append(f"Backup: {backup}")
        if done and verb == "wrapped":
            lines.append("Restart your MCP apps so they relaunch the servers through verimcp.")
            lines.append(f"Watch every verdict live:  verimcp-dashboard --audit-log {audit_log}")
            lines.append("Undo at any time:          verimcp unwrap")
        elif done:
            lines.append("Restart your MCP apps to pick up the original servers.")
    return "\n".join(lines)


def main(argv: list[str], command: str) -> int:
    parser = argparse.ArgumentParser(
        prog=f"verimcp {command}",
        description="Put verimcp in front of every MCP server configured in Claude Code, Claude Desktop and "
        "Cursor." if command == "wrap" else "Restore the MCP server entries `verimcp wrap` changed.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing anything.")
    parser.add_argument("--home", default=None, help=argparse.SUPPRESS)  # tests point this at a temp dir
    if command == "wrap":
        parser.add_argument("--audit-log", default=None,
                            help="Where every wrapped server records its verdicts (default: ~/.verimcp/audit.jsonl).")
        parser.add_argument("--launcher", choices=["auto", "verimcp", "uvx"], default="auto",
                            help="How apps should start verimcp: the installed `verimcp` command, or `uvx verimcp` "
                            "(auto: installed command if on PATH, else uvx).")
        parser.add_argument("--all-http", action="store_true",
                            help="Also wrap remote servers that have no headers (only for ones that need no sign-in).")
    args = parser.parse_args(argv)

    home = Path(args.home).expanduser().resolve() if args.home else Path.home()
    if command == "wrap":
        audit_log = Path(args.audit_log).expanduser().resolve() if args.audit_log else home / ".verimcp" / "audit.jsonl"
        if not args.dry_run:
            audit_log.parent.mkdir(parents=True, exist_ok=True)
        plan = wrap(home, audit_log, args.launcher, args.all_http, args.dry_run, home_overridden=args.home is not None)
        print(format_plan(plan, "wrapped", args.dry_run, audit_log))
    else:
        plan = unwrap(home, args.dry_run)
        if not plan.changes:
            print("Nothing to unwrap: no servers recorded by `verimcp wrap`.")
        else:
            print(format_plan(plan, "restored", args.dry_run, None))
    return 0
