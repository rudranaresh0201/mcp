"""Phase 7's actual proof, not just a documentation promise: a real,
independently pip-installable package outside this repo (examples/
third_party_verifier/) is discovered by verimcp's plugin registry with zero
verimcp source changes. See docs/writing-a-verifier.md.

Installing the example package is left as a prerequisite (`pip install -e
examples/third_party_verifier`, same as CI's own `pip install -e ".[dev]"`
step) rather than done inside the test itself -- entry_points discovery
reads from already-installed package metadata, which a subprocess-level
`pip install` during a test run wouldn't make visible to the *current*
Python process without a restart. If the example package isn't installed,
this test is skipped with a clear reason rather than failing confusingly.
"""
import importlib.metadata

import pytest

from verimcp.verifiers import registry


def _example_package_installed() -> bool:
    try:
        importlib.metadata.distribution("verimcp-example-reverse-verifier")
        return True
    except importlib.metadata.PackageNotFoundError:
        return False


@pytest.mark.skipif(
    not _example_package_installed(),
    reason="examples/third_party_verifier not installed -- run `pip install -e examples/third_party_verifier` first",
)
def test_third_party_verifier_is_discovered_with_zero_verimcp_changes():
    verifiers = registry.load_verifiers()
    names = [type(v).__name__ for v in verifiers]

    assert "ReverseStringVerifier" in names, (
        "the example package's verifier wasn't discovered -- entry_points "
        "plugin registration is broken, or the package needs reinstalling"
    )


@pytest.mark.skipif(not _example_package_installed(), reason="examples/third_party_verifier not installed")
def test_third_party_verifier_actually_catches_a_lie():
    """Not just "is it in the list" -- does calling it for real work."""
    verifier = next(v for v in registry.load_verifiers() if type(v).__name__ == "ReverseStringVerifier")

    request = {"params": {"arguments": {"text": "hello"}}}
    lying_response = {"result": {"isError": False, "structuredContent": {"reversed": "not-actually-reversed"}}}

    result = verifier.verify(request, lying_response, root=None)

    assert result["result"]["isError"] is True
    assert "does not match the real reverse of" in result["result"]["content"][0]["text"]
