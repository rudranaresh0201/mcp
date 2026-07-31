"""Unit tests for the entry-point plugin discovery that replaced the old
hardcoded ALL_VERIFIERS list (docs/ROADMAP.md Phase 1)."""
import pytest

from verimcp.verifiers.ci_run import CIRunVerifier
from verimcp.verifiers.filesystem import FilesystemVerifier
from verimcp.verifiers.git_branch import GitBranchVerifier
from verimcp.verifiers.git_commit import GitCommitVerifier
from verimcp.verifiers.git_server_commit import GitServerCommitVerifier
from verimcp.verifiers.registry import load_verifiers
from verimcp.verifiers.resource_read import ResourceReadVerifier


def test_load_verifiers_with_no_names_loads_everything_discovered():
    verifiers = load_verifiers(None)

    types = {type(v) for v in verifiers}
    assert types == {
        FilesystemVerifier,
        GitCommitVerifier,
        GitBranchVerifier,
        CIRunVerifier,
        ResourceReadVerifier,
        GitServerCommitVerifier,
    }


def test_load_verifiers_with_explicit_names_restricts_to_just_those():
    verifiers = load_verifiers(["filesystem"])

    assert len(verifiers) == 1
    assert isinstance(verifiers[0], FilesystemVerifier)


def test_load_verifiers_with_unknown_name_raises():
    with pytest.raises(KeyError):
        load_verifiers(["not_a_real_verifier"])
