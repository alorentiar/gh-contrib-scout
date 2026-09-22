"""GitHub access for gh-contrib-scout, via the gh CLI.

Same approach as the rest of the toolkit: `gh auth login` is the only setup
step, no personal access token to mint or rotate. Every call is wrapped so the
scout can degrade gracefully when a single repository lookup fails.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

DEFAULT_TIMEOUT = 60


class GitHubError(RuntimeError):
    """gh missing, unauthenticated, or the API call failed."""


class GitHub:
    """Minimal GitHub client built on the gh CLI."""

    def __init__(self, gh_binary: str = "gh", timeout: int = DEFAULT_TIMEOUT) -> None:
        self.gh_binary = gh_binary
        self.timeout = timeout
        self._repo_cache: dict[str, dict[str, Any]] = {}

    # -- plumbing ----------------------------------------------------------

    def _run(self, args: list[str]) -> str:
        if shutil.which(self.gh_binary) is None:
            raise GitHubError(
                f"'{self.gh_binary}' not found on PATH. Install from "
                "https://cli.github.com and run 'gh auth login'."
            )
        try:
            proc = subprocess.run(
                [self.gh_binary, *args],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitHubError(f"gh timed out after {self.timeout}s") from exc
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or "").strip()
            raise GitHubError(msg or f"gh exited with {proc.returncode}")
        return proc.stdout

    def _api(self, path: str, fields: dict[str, Any] | None = None) -> Any:
        args = ["api", path]
        for key, value in (fields or {}).items():
            args += ["-f", f"{key}={value}"]
        out = self._run(args)
        try:
            return json.loads(out) if out.strip() else None
        except json.JSONDecodeError as exc:
            raise GitHubError(f"gh returned invalid JSON: {exc}") from exc

    def _search(self, query: str, limit: int, sort: str | None = None) -> list[dict]:
        """Run an issue/PR search.

        The query is sent as a query string rather than through `gh api -f`,
        because `gh` treats spaces inside a `-f` value as separate arguments
        and the call hangs instead of failing. Percent-encoding the spaces
        keeps the whole query in one argument.
        """
        encoded = query.replace(" ", "+")
        url = f"search/issues?q={encoded}&per_page={min(limit, 100)}"
        if sort:
            url += f"&sort={sort}&order=desc"
        data = self._api_soft(url)
        if not isinstance(data, dict):
            return []
        return data.get("items") or []

    def _api_soft(self, path: str, fields: dict[str, Any] | None = None) -> Any:
        """Like _api but returns None instead of raising. For optional data."""
        try:
            return self._api(path, fields)
        except GitHubError:
            return None

    # -- identity ----------------------------------------------------------

    def viewer(self) -> str:
        login = self._run(["api", "user", "--jq", ".login"]).strip()
        if not login:
            raise GitHubError("gh is not authenticated. Run 'gh auth login'.")
        return login

    def check_auth(self) -> tuple[bool, str]:
        try:
            return True, self.viewer()
        except GitHubError as exc:
            return False, str(exc)

    # -- search ------------------------------------------------------------

    def search_issues(
        self,
        query: str,
        limit: int = 50,
        sort: str = "created",
    ) -> list[dict[str, Any]]:
        """Search issues. `query` is the raw GitHub search string."""
        return self._search(query, limit=limit, sort=sort)

    def search_pulls(self, query: str, limit: int = 30) -> list[dict[str, Any]]:
        """Open pull requests matching a query, used for the claim check."""
        return self._search(query, limit=limit)

    def search_prs_for_issue(self, repo: str, number: int) -> list[dict[str, Any]]:
        """Open PRs that mention this issue, which usually means it is taken."""
        query = f"repo:{repo}+type:pr+is:open+{number}+in:body"
        return self.search_pulls(query, limit=10)

    # -- repository data ---------------------------------------------------

    def repo(self, full_name: str) -> dict[str, Any]:
        """Repository metadata, cached for the life of the client."""
        if full_name in self._repo_cache:
            return self._repo_cache[full_name]
        data = self._api_soft(f"repos/{full_name}") or {}
        self._repo_cache[full_name] = data
        return data

    def recent_merged_prs(self, full_name: str, limit: int = 20) -> list[dict[str, Any]]:
        return (
            self._api_soft(
                f"repos/{full_name}/pulls?state=closed&sort=updated"
                f"&direction=desc&per_page={limit}"
            )
            or []
        )

    def contributors_count(self, full_name: str) -> int:
        data = self._api_soft(f"repos/{full_name}/contributors?per_page=100")
        return len(data) if isinstance(data, list) else 0

    def issue_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        return self._api_soft(f"repos/{repo}/issues/{number}/comments") or []

    def split_repo(self, item: dict[str, Any]) -> str:
        url = item.get("repository_url") or ""
        if "/repos/" in url:
            return url.split("/repos/", 1)[1]
        full = (item.get("repository") or {}).get("full_name")
        if full:
            return str(full)
        raise GitHubError("cannot determine repository for this search result")

    def rate_limit(self) -> dict[str, Any]:
        return self._api_soft("rate_limit") or {}
