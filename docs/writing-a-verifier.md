# Writing a verifier

A verifier answers exactly one question: given the tool call a Client made
and the response the real backend produced, does independently-checkable
reality actually match the claim? Nothing in the MCP spec checks this --
`isError: false` only means the tool ran without raising, not that its
claimed effect is real. That gap is what a verifier closes.

This doc plus `examples/third_party_verifier/` (a real, independently
installable package) are the two things Phase 7 (`docs/ROADMAP.md`) asks
for: enough to write a new verifier without reading verimcp's internals
first.

## The contract (`src/verimcp/verifiers/base.py`)

```python
class Verifier(ABC):
    @abstractmethod
    def applies_to(self, tool_name: str) -> bool:
        """Should this verifier run for a given tool name?"""

    @abstractmethod
    def verify(self, request: dict, response: dict, root: Path | None = None) -> dict:
        """Return the response to actually forward to the client."""
```

Two methods, nothing else required. `applies_to` is a filter — the proxy
only calls `verify()` for tool names (or resource URIs) your verifier says
yes to. `verify()` gets the original request, the backend's raw response,
and `root` — the filesystem path the *backend* negotiated with the Host via
`roots/list` (or the `--root` CLI override), not verimcp's own working
directory. Return `response` unchanged to pass it through; return an
overridden failure to enforce a catch.

Two shared helpers do the actual enforcement, and picking the right one
matters — they produce different wire shapes because the MCP spec itself
treats tool failures and resource-read failures differently:

- **`self._override(response, message)`** — for `tools/call`. A tool
  failure is a *successful* JSON-RPC response with `isError: true` inside
  it (the agent is meant to see this and reason about it, same as any other
  tool output). Every verifier's catch looks identical to the Host
  regardless of which verifier caught it: `{"isError": true, "content":
  [{"type": "text", "text": "[verimcp] postcondition check failed: ..."}]}`.
- **`self._error(response, message)`** — for anything else (e.g.
  `resources/read`). These don't have an `isError`-flavored "successful"
  failure shape in the spec — a failed read is a real JSON-RPC `error`
  object (`code: -32001`), replacing `result` entirely.

Using the wrong one produces a response shape a real client won't
recognize as a failure for that method — check which one your target
method uses before writing `verify()`.

## Worked example: `FilesystemVerifier`, line by line

The simplest real verifier in this repo (`src/verimcp/verifiers/filesystem.py`)
checks that `write_file` actually wrote what it claimed:

```python
class FilesystemVerifier(Verifier):
    def applies_to(self, tool_name: str) -> bool:
        return tool_name in WRITE_TOOLS          # {"write_file"}

    def verify(self, request, response, root=None):
        result = response.get("result", {})
        if result.get("isError"):
            return response   # (1) backend already reported failure, nothing to add

        args = request.get("params", {}).get("arguments", {})
        path = args.get("path")
        expected_content = args.get("content")
        if path is None or expected_content is None:
            return response   # (2) not a shape we know how to check

        if root is None:
            return response   # (3) can't tell where the backend wrote this -- don't guess, don't block

        actual_path = Path(root) / path
        if not actual_path.exists():
            return self._override(response, f"claimed write to {path!r} succeeded, but the file does not exist")

        actual_content = actual_path.read_text()
        if _sha256(actual_content) != _sha256(expected_content):
            return self._override(response, "...content mismatch...")

        return response   # (4) verified: claim matches reality, pass through unchanged
```

Every verifier in this repo follows the same skeleton, in this order:

1. **If the backend already said it failed, don't pile on.** A verifier's
   job is to catch a *false claim of success* — an honest failure needs no
   second opinion.
2. **If the request/response isn't a shape you recognize, pass through.**
   Two backends can expose a tool with the same name and different response
   shapes (this repo's `GitCommitVerifier` vs. `GitServerCommitVerifier` are
   a real example) — silently returning unchanged, not raising, is what
   lets multiple verifiers coexist safely on the same tool name.
3. **If you can't determine `root`, pass through — never guess.** A verifier
   that guessed a path relative to its own process's cwd once produced a
   real false positive (blocked a legitimate write) during this project's
   own development. Fail open on missing information, always.
4. **Recompute ground truth independently, then compare.** Don't trust
   anything in the response except what you're about to verify against
   something you derived yourself (a file read, a `git cat-file`, a
   subprocess re-run — whatever "independently checkable" means for your
   domain).

## Registering it as a plugin

Verifiers are discovered via `importlib.metadata.entry_points`, the same
mechanism pytest and Black use for third-party plugins — **no verimcp
source changes required.** In *your own* package's `pyproject.toml`:

```toml
[project.entry-points."verimcp.verifiers"]
my_verifier = "my_package.my_module:MyVerifierClass"
```

`pip install`-ing your package makes `verimcp.verifiers.registry.load_verifiers()`
pick it up automatically. See `examples/third_party_verifier/` for a
complete, real, independently pip-installable package doing exactly this —
`tests/test_third_party_verifier_plugin.py` installs it and asserts
discovery actually works, not just that the docs describe it correctly.

## A checklist before you ship one

- Does `applies_to` only match what you can actually check? Don't claim a
  tool name you have no ground truth for.
- Does `verify()` fail open (pass through unchanged) on every ambiguous
  case — unknown shape, missing `root`, backend-already-failed — rather
  than guessing or blocking?
- Are you shelling out to check ground truth (git, filesystem, a re-run)?
  If so, pass `stdin=subprocess.DEVNULL` explicitly. Every verifier here
  that shells out does this deliberately — without it, the child process
  inherits verimcp's own real stdin, which is simultaneously being read on
  a background thread, and the two silently deadlock on Windows.
- Did you pick `_override` or `_error` correctly for the method you're
  verifying?
