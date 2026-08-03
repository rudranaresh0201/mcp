"""The core proxy loop.

Role split (topic 1, host/client/server): this process is a SERVER to the
real Host (talks over our own stdin/stdout) and simultaneously a CLIENT to
the real backend MCP server (talks over a subprocess's stdin/stdout).

    Host  <--stdio-->  [ verimcp: server-side | client-side ]  <--stdio-->  backend

Every message from the Host is forwarded to the backend unchanged. Every
response from the backend is checked: if it was a tools/call or a
resources/read this proxy has a verifier for, verify() decides what actually
gets forwarded back to the Host.
"""
import asyncio
import itertools
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from verimcp import audit_resource, jsonrpc, telemetry
from verimcp.audit import AuditStore
from verimcp.gates.base import RequestGate
from verimcp.gates.sampling_rate_limit import SamplingRateLimitGate
from verimcp.gates.tool_call_policy import ToolCallPolicyGate
from verimcp.verifiers import registry

FILE_URI_PREFIX = "file://"
# Reserved id namespace for requests *verimcp itself* originates (currently
# just elicitation/create). Must be a shape no real backend would ever
# generate on its own -- backends we've seen (devmcp, mcp-server-git) use
# plain integer ids from their own counters, so a prefixed string can never
# collide, without needing coordination with the backend at all.
_ELICITATION_ID_PREFIX = "verimcp-elicit-"


def _is_elicitation_id(id_) -> bool:
    return isinstance(id_, str) and id_.startswith(_ELICITATION_ID_PREFIX)


def _extract_root(roots_list_response: dict) -> Path | None:
    """Same file:// parsing devmcp.roots.RootsClient does on the other end --
    duplicated rather than imported because verimcp has no dependency on
    devmcp (it has to work in front of any backend, not just this one)."""
    for root in roots_list_response.get("result", {}).get("roots", []):
        uri = root.get("uri", "")
        if uri.startswith(FILE_URI_PREFIX):
            # url2pathname handles platform quirks (e.g. Windows' extra
            # leading slash before the drive letter in file:///C:/foo).
            return Path(url2pathname(urlparse(uri).path)).resolve()
    return None


class _StdinReader:
    """Reads our own real stdin without asyncio's pipe transports. Windows'
    proactor event loop can't reliably register a subprocess-redirected
    stdin via connect_read_pipe (see devmcp.cli._StdinReader for the same
    fix applied there) -- the blocking read runs in a worker thread instead
    so it doesn't block the loop."""

    async def readline(self) -> bytes:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, sys.stdin.buffer.readline)


class Proxy:
    def __init__(
        self,
        backend_cmd: list[str],
        verifier_names: list[str] | None = None,
        root_override: Path | None = None,
        sampling_limit: int = 10,
        sampling_window: float = 60.0,
        policy_config: Path | None = None,
        approval_timeout: float = 60.0,
        audit_log: Path | None = None,
    ):
        self.backend_cmd = backend_cmd
        # None loads every verifier installed under the verimcp.verifiers
        # entry-point group (existing behavior, unchanged for old callers).
        # An explicit list restricts to just those -- needed once more than
        # one backend is in play, since two backends can expose a same-named
        # tool with differently-shaped responses.
        self._verifiers = registry.load_verifiers(verifier_names)
        # Pending requests we've forwarded to the backend, keyed by JSON-RPC id,
        # so that when the matching response comes back we still have the
        # original request (tool name + arguments, or resource uri) to verify
        # it against.
        self._pending: dict[str, dict] = {}
        # ids of roots/list requests the *backend* sent us, so that when the
        # matching response comes back from the Host (opposite direction to
        # the tools/call tracking above) we recognize it as "the root" and
        # not just some unrelated reply.
        self._pending_roots: set = set()
        # devmcp learns its root by asking the Host via roots/list, so
        # watching that exchange is enough for it. Not every backend
        # participates in that dance at all -- e.g. the reference
        # mcp-server-git never sends roots/list, it takes its repo path from
        # its own --repository flag instead. root_override is the fallback
        # for exactly that case: a verifier still needs to know the backend's
        # real working directory even when the backend never tells the Host.
        self._root: Path | None = root_override
        # Policy gates -- unlike verifiers, these check *requests* before
        # forwarding them at all, in either direction (sampling/createMessage
        # backend->Host, tools/call Host->backend), not responses. See
        # docs/adr/0001-verify-vs-dont-framework.md's "Policy gates" bucket.
        self._gates = [SamplingRateLimitGate(sampling_limit, sampling_window)]
        # Opt-in: no --policy-config means no tools/call gating at all, same
        # as today. Given at construction, not lazily, so a bad YAML file
        # fails loudly at startup instead of on the first tool call.
        # Held separately (not just via self._gates) because require_approval
        # needs to be intercepted before the generic gate-check loop ever
        # runs -- see _forward_host_to_backend.
        self._policy_gate: ToolCallPolicyGate | None = None
        if policy_config is not None:
            self._policy_gate = ToolCallPolicyGate.from_yaml(policy_config)
            self._gates.append(self._policy_gate)
        self._approval_timeout = approval_timeout
        # Whether the Host declared the elicitation capability during
        # initialize -- learned by sniffing that message as it passes
        # through unchanged, same technique _extract_root uses on roots/list
        # replies. Defaults closed: no initialize seen yet means no
        # elicitation support assumed.
        self._host_supports_elicitation = False
        # verimcp's own outstanding elicitation/create requests, keyed by
        # the reserved id it picked -- resolved when the Host's reply comes
        # back through _forward_host_to_backend (the same direction the
        # Host's replies to devmcp's roots/list flow through).
        self._pending_elicitations: dict[str, asyncio.Future] = {}
        self._next_elicitation_id = itertools.count(1)
        # Approval handling runs as a background task (see _handle_approval)
        # so the single _forward_host_to_backend read loop stays free to
        # read the elicitation reply that task is waiting on -- the same
        # deadlock class fixed in devmcp/server.py's tools/call dispatch.
        # Tracked here so run() can cancel any still-pending ones on exit.
        self._background_tasks: set[asyncio.Task] = set()
        # Phase 3: audit log. None means no --audit-log given -- every hook
        # below is a no-op in that case (see _record_audit), so behavior for
        # existing callers is unchanged.
        self._audit = AuditStore(audit_log) if audit_log is not None else None
        # URIs (verimcp://audit or verimcp://audit/current) the Host has
        # asked to be notified about via resources/subscribe -- checked after
        # every _record_audit() call to decide whether to fire
        # notifications/resources/updated.
        self._audit_subscribers: set[str] = set()
        # ids of the Host's initialize/resources/list requests, tracked the
        # same way _pending_roots tracks roots/list -- so the matching
        # backend->Host response can be patched (capabilities.resources) or
        # extended (the audit resource entries) in _forward_backend_to_host.
        self._pending_initialize: set = set()
        self._pending_resource_list: set = set()
        # gate/approval info for a require_approval call that was accepted,
        # keyed by request id -- kept out of the request dict itself (which
        # is the exact object forwarded to the backend over the wire) so it
        # never leaks into a real JSON-RPC message. Consumed by
        # _record_completed_audit once the backend's response arrives.
        self._pending_approval_meta: dict[str, dict] = {}
        # Phase 4: one OTel span per in-flight tools/call/resources/read,
        # keyed by JSON-RPC id -- same request/response correlation problem
        # self._pending already solves (started in one read loop, finished in
        # the other, or from _handle_approval's background task), solved the
        # same way. Unlike self._audit, telemetry has no opt-in flag here:
        # start_span/finish_span are always called (see telemetry.py's
        # docstring for why that's safe) -- whether anything actually gets
        # exported depends only on whether cli.py's configure_sdk ran.
        self._active_spans: dict = {}

    async def run(self) -> None:
        backend = await asyncio.create_subprocess_exec(
            *self.backend_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )

        try:
            await asyncio.gather(
                self._forward_host_to_backend(backend),
                self._forward_backend_to_host(backend),
            )
        finally:
            # A Host that disconnects mid-approval leaves _handle_approval
            # tasks awaiting a Future that can now never resolve -- cancel
            # them rather than leaking the process open past exit.
            for task in self._background_tasks:
                task.cancel()
            # Same idea for any tools/call or resources/read whose span was
            # started but whose outcome will now never be recorded.
            for span, _ in self._active_spans.values():
                telemetry.abandon_span(span)
            self._active_spans.clear()

    async def _forward_host_to_backend(self, backend) -> None:
        reader = _StdinReader()

        while True:
            message = await jsonrpc.read_message(reader)
            if message is None:
                backend.stdin.close()
                return

            if message.get("method") == "initialize":
                capabilities = message.get("params", {}).get("capabilities", {})
                self._host_supports_elicitation = "elicitation" in capabilities
                if self._audit is not None and "id" in message:
                    self._pending_initialize.add(message["id"])

            if message.get("method") == "resources/list" and "id" in message and self._audit is not None:
                self._pending_resource_list.add(message["id"])

            if (
                self._audit is not None
                and message.get("method") in ("resources/read", "resources/subscribe", "resources/unsubscribe")
                and "id" in message
                and audit_resource.is_audit_uri(message.get("params", {}).get("uri", ""))
            ):
                # Ours -- handled locally, never forwarded to the backend and
                # never tracked in self._pending, same as the synchronous
                # gate-denial branch below.
                jsonrpc.write_message_sync(self._handle_audit_resource_request(message))
                continue

            if _is_elicitation_id(message.get("id")) and ("result" in message or "error" in message):
                # A reply to a request verimcp itself sent -- never the
                # backend's business, and swallowed unconditionally (not
                # just when a Future is still parked) so a late reply after
                # an already-resolved timeout can't leak through to the
                # backend as an ordinary message.
                future = self._pending_elicitations.pop(message["id"], None)
                if future is not None and not future.done():
                    future.set_result(message)
                continue

            if message.get("method") in ("tools/call", "resources/read") and "id" in message:
                # Started before gating/approval/pending-tracking touches this
                # id at all, so the span covers the full lifecycle regardless
                # of which path the call takes below (denied, held for
                # approval, or forwarded) -- finished in _record_audit, the
                # single choke point every one of those paths already funnels
                # through. verimcp://audit resource requests are handled
                # locally a few lines above (and `continue`d before reaching
                # here) -- deliberately not spanned, since they're answered
                # from memory, not a real backend round trip.
                params = message.get("params", {})
                target = params.get("uri", "") if message["method"] == "resources/read" else params.get("name", "")
                span = telemetry.start_span(message["method"], target, message["id"])
                self._active_spans[message["id"]] = (span, time.monotonic())

            if message.get("method") == "tools/call" and "id" in message and self._policy_gate is not None:
                name = message.get("params", {}).get("name", "")
                arguments = message.get("params", {}).get("arguments")
                if self._policy_gate.action_for(name, arguments) == "require_approval":
                    task = asyncio.create_task(self._handle_approval(message, backend))
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)
                    continue

            if message.get("method") is not None and "id" in message:
                denial = self._check_gates(message)
                if denial is not None:
                    if message.get("method") in ("tools/call", "resources/read"):
                        self._record_audit(
                            message,
                            outcome="denied",
                            gate={"action": "deny", "reason": denial["error"]["message"]},
                        )
                    jsonrpc.write_message_sync(denial)
                    continue

            if message.get("method") in ("tools/call", "resources/read") and "id" in message:
                self._pending[message["id"]] = message

            if message.get("id") in self._pending_roots and ("result" in message or "error" in message):
                self._pending_roots.discard(message["id"])
                if "result" in message:
                    self._root = _extract_root(message)

            jsonrpc.write_message(backend.stdin, message)
            await backend.stdin.drain()

    async def _forward_backend_to_host(self, backend) -> None:
        while True:
            message = await jsonrpc.read_message(backend.stdout)
            if message is None:
                return

            if message.get("method") == "roots/list" and "id" in message:
                self._pending_roots.add(message["id"])

            # "method" not in message" is the JSON-RPC-correct way to tell a
            # response from a request: only requests/notifications carry
            # "method", so this guards against an id collision between the
            # Host's own request-id sequence and the backend's *own*,
            # completely independent self-originated-request counter (e.g.
            # devmcp's roots/list). Without it, a same-numbered roots/list
            # *request* arriving while an initialize/resources/list response
            # is still pending would be misread as that response -- a real,
            # reproducible bug found via MCP Inspector (a real client that,
            # unlike this project's own tests, doesn't proactively answer
            # roots/list before sending its next request) -- see
            # docs/adr/0006-mcp-inspector-compatibility.md. The same
            # id-collision class ADR 0003 already solved for elicitation ids
            # via a reserved string prefix; this is its counterpart for the
            # Phase 3 tracking sets added afterward.
            if "method" not in message and message.get("id") in self._pending_initialize:
                self._pending_initialize.discard(message["id"])
                if "result" in message:
                    # verimcp always has an audit resource to offer once
                    # --audit-log is set, regardless of whether *this*
                    # backend declares resources support -- merge, don't
                    # override, whatever it already declared.
                    message["result"].setdefault("capabilities", {}).setdefault("resources", {}).setdefault(
                        "subscribe", True
                    )

            if "method" not in message and message.get("id") in self._pending_resource_list:
                self._pending_resource_list.discard(message["id"])
                message = self._inject_audit_resources(message)

            if message.get("method") is not None and "id" in message:
                denial = self._check_gates(message)
                if denial is not None:
                    jsonrpc.write_message(backend.stdin, denial)
                    await backend.stdin.drain()
                    continue

            request = self._pending.pop(message.get("id"), None)
            if request is not None:
                params = request.get("params", {})
                identifier = params.get("uri", "") if request.get("method") == "resources/read" else params.get("name", "")
                verifiers_run = registry.verifiers_for(identifier, self._verifiers)
                for verifier in verifiers_run:
                    message = verifier.verify(request, message, root=self._root)
                self._record_completed_audit(request, message, verifiers_run)

            jsonrpc.write_message_sync(message)

    def _check_gates(self, request: dict) -> dict | None:
        for gate in self._gates:
            if gate.applies_to(request["method"]):
                denial = gate.check(request)
                if denial is not None:
                    return denial
        return None

    def _handle_audit_resource_request(self, message: dict) -> dict:
        """Answers a resources/read, resources/subscribe, or
        resources/unsubscribe for a verimcp:// uri locally -- the caller has
        already confirmed audit_resource.is_audit_uri() and that self._audit
        is not None."""
        uri = message.get("params", {}).get("uri", "")
        method = message["method"]
        if method == "resources/read":
            return {"jsonrpc": "2.0", "id": message["id"], "result": audit_resource.read(uri, self._audit)}
        if method == "resources/subscribe":
            self._audit_subscribers.add(uri)
        else:
            self._audit_subscribers.discard(uri)
        return {"jsonrpc": "2.0", "id": message["id"], "result": {}}

    def _inject_audit_resources(self, message: dict) -> dict:
        """Appends verimcp's own audit resources to a resources/list
        response. If the backend errored (or doesn't support resources/list
        at all), synthesize a bare success instead of forwarding the error --
        the initialize capability patch already promised the Host that
        resources/list works."""
        entries = audit_resource.list_entries()
        if "result" in message:
            message["result"].setdefault("resources", []).extend(entries)
            return message
        return {"jsonrpc": message.get("jsonrpc", "2.0"), "id": message.get("id"), "result": {"resources": entries}}

    def _record_audit(
        self,
        request: dict,
        *,
        outcome: str,
        gate: dict | None = None,
        verification: dict | None = None,
        approval: dict | None = None,
    ) -> None:
        params = request.get("params", {})
        method = request.get("method")
        target = params.get("uri", "") if method == "resources/read" else params.get("name", "")

        # Telemetry and audit logging are separate opt-ins -- finishing the
        # span happens unconditionally (see self._active_spans's comment),
        # only the AuditStore write below is gated on --audit-log.
        span_entry = self._active_spans.pop(request.get("id"), None)
        if span_entry is not None:
            span, start_time = span_entry
            telemetry.finish_span(
                span, start_time, tool_name=target, outcome=outcome, gate=gate, verification=verification, approval=approval
            )

        if self._audit is None:
            return
        arguments = params.get("arguments") if method == "tools/call" else None
        self._audit.record(
            method=method,
            target=target,
            arguments=arguments,
            outcome=outcome,
            gate=gate,
            verification=verification,
            approval=approval,
        )
        self._notify_audit_subscribers()

    def _record_completed_audit(self, request: dict, response: dict, verifiers_run: list) -> None:
        """Records a tools/call or resources/read that actually reached the
        backend and got a response -- as opposed to one denied before ever
        being forwarded, which _record_audit's other call sites handle
        directly. Pulls the require_approval gate/approval info back off the
        request dict if _handle_approval's accept path stashed it there.
        Runs unconditionally (audit and telemetry are separate opt-ins,
        both handled inside _record_audit) -- the _pending_approval_meta
        pop below also needs to happen regardless of --audit-log, or a
        require_approval-accepted call's side-table entry would never be
        cleaned up when audit logging is off."""
        meta = self._pending_approval_meta.pop(request.get("id"), {})
        gate = meta.get("gate")
        approval = meta.get("approval")

        if not verifiers_run:
            outcome = "forwarded"
            verification = None
        else:
            is_error = "error" in response or bool(response.get("result", {}).get("isError"))
            outcome = "verified_failed" if is_error else "verified_ok"
            verification = {"verifiers": [type(v).__name__ for v in verifiers_run], "passed": not is_error}

        self._record_audit(request, outcome=outcome, gate=gate, verification=verification, approval=approval)

    def _notify_audit_subscribers(self) -> None:
        for uri in (audit_resource.AUDIT_URI, audit_resource.CURRENT_SESSION_URI):
            if uri in self._audit_subscribers:
                jsonrpc.write_message_sync(
                    {"jsonrpc": "2.0", "method": "notifications/resources/updated", "params": {"uri": uri}}
                )

    async def _handle_approval(self, request: dict, backend) -> None:
        """Runs as a background task (see the require_approval dispatch in
        _forward_host_to_backend) so the read loop stays free to read the
        elicitation reply this coroutine is waiting on. Fails closed on
        every non-approval outcome -- missing capability, decline, cancel,
        timeout -- deliberately: a require_approval rule that silently
        degrades to "allow" on any failure mode is inert without anyone
        noticing. See docs/adr/0003-require-approval-via-elicitation.md."""
        tool_name = request.get("params", {}).get("name", "")

        if not self._host_supports_elicitation:
            self._record_audit(
                request,
                outcome="denied",
                gate={"action": "require_approval", "reason": "Host does not support elicitation"},
                approval={"status": "unsupported"},
            )
            jsonrpc.write_message_sync(
                RequestGate._deny(request, f"tool {tool_name!r} requires approval, but the Host does not support elicitation")
            )
            return

        elicit_id = f"{_ELICITATION_ID_PREFIX}{next(self._next_elicitation_id)}"
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_elicitations[elicit_id] = future
        jsonrpc.write_message_sync({
            "jsonrpc": "2.0",
            "id": elicit_id,
            "method": "elicitation/create",
            "params": {
                "message": f"verimcp policy requires approval before running tool {tool_name!r}.",
                # Intentionally empty: the accept/decline/cancel action
                # itself is the yes/no signal (per the elicitation spec's
                # three-action model) -- no extra field needed to express it.
                "requestedSchema": {"type": "object", "properties": {}},
            },
        })

        try:
            reply = await asyncio.wait_for(future, timeout=self._approval_timeout)
        except TimeoutError:
            self._pending_elicitations.pop(elicit_id, None)
            self._record_audit(
                request,
                outcome="denied",
                gate={"action": "require_approval", "reason": "approval timed out"},
                approval={"status": "timeout"},
            )
            jsonrpc.write_message_sync(RequestGate._deny(request, f"approval request for tool {tool_name!r} timed out"))
            return

        action = reply.get("result", {}).get("action")
        if action == "accept":
            # Recorded once the backend's response actually arrives (see
            # _record_completed_audit), not here -- that's the point where
            # verification result is known too, so one combined entry covers
            # gate + verification + approval instead of two partial ones.
            # Stashed unconditionally, not just when --audit-log is set --
            # _record_completed_audit's telemetry span needs this approval
            # status too, and audit/telemetry are meant to be independent
            # opt-ins (see _record_audit).
            self._pending_approval_meta[request["id"]] = {
                "gate": {"action": "require_approval", "reason": "approved by Host"},
                "approval": {"status": "accept"},
            }
            self._pending[request["id"]] = request
            jsonrpc.write_message(backend.stdin, request)
            await backend.stdin.drain()
        else:
            self._record_audit(
                request,
                outcome="denied",
                gate={"action": "require_approval", "reason": f"approval was {action or 'not granted'}"},
                approval={"status": action or "not_granted"},
            )
            jsonrpc.write_message_sync(
                RequestGate._deny(request, f"approval for tool {tool_name!r} was {action or 'not granted'}")
            )
