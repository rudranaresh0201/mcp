"""Verifier for GitHub's official MCP server (github/github-mcp-server).

Every other built-in verifier re-derives ground truth from something on
this machine -- git history, the filesystem, docker inspect. A tool that
acts on GitHub has no local fact to re-check: the PR, issue or branch it
claims to have created lives on GitHub's servers. So the ground truth here
is GitHub's own REST API, which the tool response cannot manufacture.

Response shapes were read off the server's source, not guessed:
  - create_pull_request -> MinimalResponse {"id": ..., "url": "https://github.com/O/R/pull/N"}
  - issue_write (method=create) -> MinimalIssue {"number": N, "title": ..., "html_url": ...}
  - create_branch -> checked from the request's own owner/repo/branch arguments

Two decisions worth knowing:

1. Title matching. A backend can return the URL of a PR that really exists
   but was not created by this call -- the GitHub analogue of the reused
   commit hash that GitCommitVerifier cannot catch. Checking that the PR or
   issue carries the title that was actually requested closes most of that
   gap for free.

2. Private repos. Without a token GitHub answers 404 for a private repo and
   for a missing one alike, so a bare 404 is not evidence of a lie. A 404 on
   the PR/issue/branch only counts once the repo itself is confirmed visible;
   otherwise the response passes through unchanged. Absence of evidence is
   not evidence of a lie -- the same rule every verifier here follows.

Auth is optional and read-only: GITHUB_PERSONAL_ACCESS_TOKEN (what the
GitHub MCP server itself already requires) or GITHUB_TOKEN. Stdlib urllib
only, so the core install gains no HTTP dependency.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from verimcp.verifiers.base import Verifier

API = "https://api.github.com"
PR_TOOLS = {"create_pull_request"}
ISSUE_TOOLS = {"issue_write"}
BRANCH_TOOLS = {"create_branch"}
_PR_URL_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)/?$")

# (status, parsed JSON body). status None means the network itself failed.
Fetch = Callable[[str], tuple[int | None, Any]]


def _default_fetch(path: str) -> tuple[int | None, Any]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "verimcp"}
    token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{API}{path}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as reply:
            return reply.status, json.loads(reply.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        return err.code, None
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None, None


def _response_json(response: dict[str, Any]) -> Any:
    for block in response.get("result", {}).get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            try:
                return json.loads(block.get("text", ""))
            except ValueError:
                return None
    return None


def _same(a: str | None, b: str | None) -> bool:
    return (a or "").strip().lower() == (b or "").strip().lower()


class GitHubVerifier(Verifier):
    def __init__(self, fetch: Fetch | None = None):
        self._fetch = fetch or _default_fetch

    def applies_to(self, tool_name: str) -> bool:
        return tool_name in PR_TOOLS | ISSUE_TOOLS | BRANCH_TOOLS

    def verify(self, request: dict[str, Any], response: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
        if response.get("result", {}).get("isError"):
            return response  # backend already reported failure, nothing to add

        params = request.get("params", {})
        tool = params.get("name")
        args = params.get("arguments") or {}

        if tool in PR_TOOLS:
            return self._verify_pull_request(args, response)
        if tool in ISSUE_TOOLS:
            return self._verify_issue(args, response)
        if tool in BRANCH_TOOLS:
            return self._verify_branch(args, response)
        return response

    def _repo_visible(self, owner: str, repo: str) -> bool:
        status, _ = self._fetch(f"/repos/{owner}/{repo}")
        return status == 200

    def _caught(self, response: dict[str, Any], message: str, summary: str, source: str, evidence: dict) -> dict:
        return self._receipt(
            self._override(response, message), verdict="contradicted", summary=summary, source=source, evidence=evidence
        )

    def _verify_pull_request(self, args: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
        claimed = _response_json(response)
        url = claimed.get("url") if isinstance(claimed, dict) else None
        match = _PR_URL_RE.match(url or "")
        if match is None:
            return response  # not a shape we know how to check

        owner, repo, number = match.group(1), match.group(2), int(match.group(3))
        if args.get("owner") and args.get("repo") and not (_same(owner, args["owner"]) and _same(repo, args["repo"])):
            return self._caught(
                response, f"claimed PR {url} is in {owner}/{repo}, not the requested {args['owner']}/{args['repo']}",
                summary=f"the returned PR link points at {owner}/{repo}, not {args['owner']}/{args['repo']}",
                source="the tool's own response", evidence={"claimed_url": url, "requested_repo": f"{args['owner']}/{args['repo']}"},
            )

        source = f"GitHub API GET /repos/{owner}/{repo}/pulls/{number}"
        status, pr = self._fetch(f"/repos/{owner}/{repo}/pulls/{number}")
        if status == 404 and self._repo_visible(owner, repo):
            return self._caught(
                response, f"claimed PR #{number} does not exist in {owner}/{repo}",
                summary=f"GitHub has no PR #{number} in {owner}/{repo}", source=source,
                evidence={"repo": f"{owner}/{repo}", "number": number, "http_status": 404, "repo_visible": True},
            )
        if status != 200 or not isinstance(pr, dict):
            return response  # private repo without a token, rate limit, or network: can't check

        found = {
            "repo": f"{owner}/{repo}", "number": number, "title": pr.get("title"), "state": pr.get("state"),
            "url": pr.get("html_url"), "author": (pr.get("user") or {}).get("login"), "created_at": pr.get("created_at"),
        }
        if args.get("title") and not _same(pr.get("title"), args["title"]):
            return self._caught(
                response,
                f"PR #{number} exists in {owner}/{repo} but is titled {pr.get('title')!r}, not the requested "
                f"{args['title']!r} -- the response points at a different, pre-existing PR",
                summary=f"PR #{number} is titled {pr.get('title')!r}, not {args['title']!r}", source=source,
                evidence={**found, "requested_title": args["title"]},
            )
        return self._receipt(
            response, verdict="verified",
            summary=f"PR #{number} exists in {owner}/{repo}, titled {pr.get('title')!r}, {pr.get('state')}",
            source=source, evidence=found,
        )

    def _verify_issue(self, args: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
        if args.get("method") != "create":
            return response  # only creation has a clean postcondition to check
        owner, repo = args.get("owner"), args.get("repo")
        claimed = _response_json(response)
        number = claimed.get("number") if isinstance(claimed, dict) else None
        if not (owner and repo and isinstance(number, int)):
            return response

        source = f"GitHub API GET /repos/{owner}/{repo}/issues/{number}"
        status, issue = self._fetch(f"/repos/{owner}/{repo}/issues/{number}")
        if status == 404 and self._repo_visible(owner, repo):
            return self._caught(
                response, f"claimed issue #{number} does not exist in {owner}/{repo}",
                summary=f"GitHub has no issue #{number} in {owner}/{repo}", source=source,
                evidence={"repo": f"{owner}/{repo}", "number": number, "http_status": 404, "repo_visible": True},
            )
        if status != 200 or not isinstance(issue, dict):
            return response

        found = {
            "repo": f"{owner}/{repo}", "number": number, "title": issue.get("title"), "state": issue.get("state"),
            "url": issue.get("html_url"), "author": (issue.get("user") or {}).get("login"),
            "created_at": issue.get("created_at"),
        }
        if "pull_request" in issue:
            return self._caught(
                response, f"#{number} in {owner}/{repo} is a pull request, not the issue that was claimed",
                summary=f"#{number} in {owner}/{repo} is a pull request, not an issue", source=source, evidence=found,
            )
        if args.get("title") and not _same(issue.get("title"), args["title"]):
            return self._caught(
                response,
                f"issue #{number} exists in {owner}/{repo} but is titled {issue.get('title')!r}, not the requested "
                f"{args['title']!r} -- the response points at a different, pre-existing issue",
                summary=f"issue #{number} is titled {issue.get('title')!r}, not {args['title']!r}", source=source,
                evidence={**found, "requested_title": args["title"]},
            )
        return self._receipt(
            response, verdict="verified",
            summary=f"issue #{number} exists in {owner}/{repo}, titled {issue.get('title')!r}, {issue.get('state')}",
            source=source, evidence=found,
        )

    def _verify_branch(self, args: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
        owner, repo, branch = args.get("owner"), args.get("repo"), args.get("branch")
        if not (owner and repo and branch):
            return response

        source = f"GitHub API GET /repos/{owner}/{repo}/branches/{branch}"
        status, found = self._fetch(f"/repos/{owner}/{repo}/branches/{urllib.parse.quote(branch, safe='')}")
        if status == 404 and self._repo_visible(owner, repo):
            return self._caught(
                response, f"claimed branch {branch!r} does not exist in {owner}/{repo}",
                summary=f"GitHub has no branch {branch} in {owner}/{repo}", source=source,
                evidence={"repo": f"{owner}/{repo}", "branch": branch, "http_status": 404, "repo_visible": True},
            )
        if status != 200 or not isinstance(found, dict):
            return response
        head = (found.get("commit") or {}).get("sha")
        return self._receipt(
            response, verdict="verified",
            summary=f"branch {branch} exists in {owner}/{repo}" + (f" at {head[:12]}" if head else ""),
            source=source, evidence={"repo": f"{owner}/{repo}", "branch": branch, "head": head},
        )
