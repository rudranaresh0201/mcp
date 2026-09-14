"""Unit-level: GitHubVerifier against a fake GitHub API (a dict of path ->
(status, body)), so every branch of the decision is exercised without the
network. The real-API run lives in tests/test_github_verifier_live.py."""
import json

from verimcp.verifiers.base import FAILURE_PREFIX
from verimcp.verifiers.github import GitHubVerifier


def _fake_github(routes: dict[str, tuple[int | None, object]]):
    calls: list[str] = []

    def fetch(path: str):
        calls.append(path)
        return routes.get(path, (404, None))

    fetch.calls = calls
    return fetch


def _request(tool: str, **arguments) -> dict:
    return {"method": "tools/call", "params": {"name": tool, "arguments": arguments}}


def _text_response(payload: object) -> dict:
    return {"result": {"isError": False, "content": [{"type": "text", "text": json.dumps(payload)}]}}


def _caught(result: dict) -> str | None:
    if not result["result"].get("isError"):
        return None
    text = result["result"]["content"][0]["text"]
    assert text.startswith(FAILURE_PREFIX)
    return text[len(FAILURE_PREFIX):]


PR_ARGS = {"owner": "octo", "repo": "demo", "title": "Add login", "head": "feature", "base": "main"}
PR_URL = "https://github.com/octo/demo/pull/7"
REPO_OK = {"/repos/octo/demo": (200, {"full_name": "octo/demo"})}


def test_applies_only_to_github_write_tools():
    verifier = GitHubVerifier(fetch=_fake_github({}))
    assert verifier.applies_to("create_pull_request")
    assert verifier.applies_to("issue_write")
    assert verifier.applies_to("create_branch")
    assert not verifier.applies_to("git_commit")


def test_passes_a_real_pr_with_the_requested_title():
    fetch = _fake_github({**REPO_OK, "/repos/octo/demo/pulls/7": (200, {"number": 7, "title": "Add login"})})
    response = _text_response({"id": "123", "url": PR_URL})

    result = GitHubVerifier(fetch=fetch).verify(_request("create_pull_request", **PR_ARGS), response)

    assert result is response


def test_catches_a_pr_that_does_not_exist():
    fetch = _fake_github(REPO_OK)  # repo visible, PR 404
    result = GitHubVerifier(fetch=fetch).verify(
        _request("create_pull_request", **PR_ARGS), _text_response({"id": "123", "url": PR_URL})
    )

    assert _caught(result) == "claimed PR #7 does not exist in octo/demo"


def test_catches_a_reused_pr_that_really_exists_but_has_another_title():
    """The GitHub analogue of GitCommitVerifier's reused-hash gap: the URL is
    real, but it is an old PR, not the one this call was asked to create."""
    fetch = _fake_github({**REPO_OK, "/repos/octo/demo/pulls/7": (200, {"number": 7, "title": "Fix typo"})})
    result = GitHubVerifier(fetch=fetch).verify(
        _request("create_pull_request", **PR_ARGS), _text_response({"id": "123", "url": PR_URL})
    )

    assert "is titled 'Fix typo', not the requested 'Add login'" in _caught(result)


def test_catches_a_pr_in_a_different_repo_than_requested():
    fetch = _fake_github({})
    result = GitHubVerifier(fetch=fetch).verify(
        _request("create_pull_request", **PR_ARGS),
        _text_response({"id": "1", "url": "https://github.com/someone/else/pull/7"}),
    )

    assert "is in someone/else, not the requested octo/demo" in _caught(result)
    assert fetch.calls == []  # contradiction found from the response alone, no API call needed


def test_a_404_on_an_invisible_repo_is_not_treated_as_a_lie():
    """Without a token GitHub 404s private repos too, so this is absence of
    evidence, not evidence of a lie: pass through unchanged."""
    fetch = _fake_github({})  # both the PR and the repo 404
    response = _text_response({"id": "123", "url": PR_URL})

    result = GitHubVerifier(fetch=fetch).verify(_request("create_pull_request", **PR_ARGS), response)

    assert result is response


def test_network_failure_passes_through():
    fetch = _fake_github({"/repos/octo/demo/pulls/7": (None, None)})
    response = _text_response({"id": "123", "url": PR_URL})

    assert GitHubVerifier(fetch=fetch).verify(_request("create_pull_request", **PR_ARGS), response) is response


def test_backend_reported_error_is_left_alone():
    fetch = _fake_github({})
    response = {"result": {"isError": True, "content": [{"type": "text", "text": "validation failed"}]}}

    assert GitHubVerifier(fetch=fetch).verify(_request("create_pull_request", **PR_ARGS), response) is response
    assert fetch.calls == []


def test_unknown_response_shape_passes_through():
    fetch = _fake_github({})
    response = {"result": {"isError": False, "content": [{"type": "text", "text": "created!"}]}}

    assert GitHubVerifier(fetch=fetch).verify(_request("create_pull_request", **PR_ARGS), response) is response


ISSUE_ARGS = {"method": "create", "owner": "octo", "repo": "demo", "title": "Login broken"}


def test_passes_a_real_issue():
    fetch = _fake_github({**REPO_OK, "/repos/octo/demo/issues/5": (200, {"number": 5, "title": "Login broken"})})
    response = _text_response({"number": 5, "title": "Login broken"})

    assert GitHubVerifier(fetch=fetch).verify(_request("issue_write", **ISSUE_ARGS), response) is response


def test_catches_an_issue_that_does_not_exist():
    fetch = _fake_github(REPO_OK)
    result = GitHubVerifier(fetch=fetch).verify(
        _request("issue_write", **ISSUE_ARGS), _text_response({"number": 5, "title": "Login broken"})
    )

    assert _caught(result) == "claimed issue #5 does not exist in octo/demo"


def test_catches_an_issue_number_that_is_actually_a_pull_request():
    fetch = _fake_github(
        {**REPO_OK, "/repos/octo/demo/issues/5": (200, {"number": 5, "title": "Login broken", "pull_request": {}})}
    )
    result = GitHubVerifier(fetch=fetch).verify(
        _request("issue_write", **ISSUE_ARGS), _text_response({"number": 5, "title": "Login broken"})
    )

    assert "is a pull request" in _caught(result)


def test_issue_updates_are_not_checked():
    fetch = _fake_github({})
    response = _text_response({"number": 5})

    result = GitHubVerifier(fetch=fetch).verify(_request("issue_write", **{**ISSUE_ARGS, "method": "update"}), response)

    assert result is response
    assert fetch.calls == []


BRANCH_ARGS = {"owner": "octo", "repo": "demo", "branch": "feature/login"}


def test_passes_a_branch_that_exists():
    fetch = _fake_github({"/repos/octo/demo/branches/feature%2Flogin": (200, {"name": "feature/login"})})
    response = _text_response({"ref": "refs/heads/feature/login"})

    assert GitHubVerifier(fetch=fetch).verify(_request("create_branch", **BRANCH_ARGS), response) is response


def test_catches_a_branch_that_does_not_exist():
    fetch = _fake_github(REPO_OK)
    result = GitHubVerifier(fetch=fetch).verify(
        _request("create_branch", **BRANCH_ARGS), _text_response({"ref": "refs/heads/feature/login"})
    )

    assert _caught(result) == "claimed branch 'feature/login' does not exist in octo/demo"
