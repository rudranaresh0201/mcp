"""Unit-level: exercise SamplingRateLimitGate directly, no proxy involved."""
from verimcp.gates.sampling_rate_limit import SamplingRateLimitGate


def _sampling_request(id_: int) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "method": "sampling/createMessage", "params": {}}


def test_applies_only_to_sampling_create_message():
    gate = SamplingRateLimitGate()

    assert gate.applies_to("sampling/createMessage") is True
    assert gate.applies_to("roots/list") is False
    assert gate.applies_to("tools/call") is False


def test_allows_requests_under_the_limit():
    gate = SamplingRateLimitGate(limit=3, window_seconds=60.0)

    for i in range(3):
        assert gate.check(_sampling_request(i)) is None


def test_denies_once_the_limit_is_exceeded():
    gate = SamplingRateLimitGate(limit=2, window_seconds=60.0)
    gate.check(_sampling_request(1))
    gate.check(_sampling_request(2))

    denial = gate.check(_sampling_request(3))

    assert denial is not None
    assert denial["error"]["code"] == -32002
    assert "rate limit exceeded" in denial["error"]["message"]
    assert denial["id"] == 3


def test_denial_does_not_consume_a_slot(monkeypatch):
    """A denied request must not itself count toward the window -- otherwise
    a burst of denials past the limit would never recover once the window
    rolls forward, since each retry would immediately refill the slot it
    just freed."""
    gate = SamplingRateLimitGate(limit=1, window_seconds=60.0)
    gate.check(_sampling_request(1))

    gate.check(_sampling_request(2))  # denied
    gate.check(_sampling_request(3))  # also denied

    assert len(gate._timestamps) == 1


def test_recovers_once_the_window_slides_past(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("verimcp.gates.sampling_rate_limit.time.monotonic", lambda: now[0])
    gate = SamplingRateLimitGate(limit=1, window_seconds=60.0)

    gate.check(_sampling_request(1))
    assert gate.check(_sampling_request(2)) is not None  # still within the window, denied

    now[0] += 61.0
    assert gate.check(_sampling_request(3)) is None  # window has rolled past, allowed again
