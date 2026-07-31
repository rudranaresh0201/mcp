"""Discovers and selects verifiers via the `verimcp.verifiers` entry-point
group -- the same mechanism pytest/Black use for third-party plugins -- so a
new verifier can be added (in this package or a third-party one) without
forking verimcp's source. See docs/ROADMAP.md Phase 1."""
from importlib.metadata import entry_points

from verimcp.verifiers.base import Verifier


def _discover() -> dict[str, Verifier]:
    return {ep.name: ep.load()() for ep in entry_points(group="verimcp.verifiers")}


def load_verifiers(names: list[str] | None = None) -> list[Verifier]:
    """`names=None` (the default) loads every verifier installed under the
    entry-point group -- this is what preserves old callers' behavior.
    An explicit list restricts to just those verifiers, which matters once
    more than one backend is in play: two backends can expose a same-named
    tool with different response shapes, so a verifier built for one must
    not be loaded unconditionally against the other. An unknown name is a
    typo, not a soft failure -- KeyError on purpose."""
    discovered = _discover()
    if names is None:
        return list(discovered.values())
    return [discovered[name] for name in names]


def verifiers_for(tool_name: str, verifiers: list[Verifier]) -> list[Verifier]:
    return [v for v in verifiers if v.applies_to(tool_name)]
