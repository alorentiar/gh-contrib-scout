"""Scoring: decide which issues are actually worth a contributor's time.

The hard part is not searching GitHub, it is filtering out the candidates that
waste a weekend: claimed tickets, dead repositories, projects where nobody
reviews outside pull requests. Each signal below is one thing an experienced
contributor checks before starting work, turned into a number.

Signals are weighted and summed into a 0-100 score. Nothing here is magic, the
weights are constants you can override through ScoreWeights.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .github import GitHub, GitHubError

DEFAULT_LABELS = (
    "good first issue",
    "help wanted",
    "documentation",
    "bug",
    "beginner friendly",
    "first-timers-only",
)


class ScoutError(RuntimeError):
    """Raised when the search itself cannot proceed."""


@dataclass
class ScoreWeights:
    """How much each signal contributes to the final score.

    The defaults encode a preference for unclaimed work in small, alive,
    responsive projects. Tune these if you are optimising for something else,
    for example reach over comfort.
    """

    unclaimed: float = 30.0
    repo_alive: float = 20.0
    merge_speed: float = 15.0
    stars_sweet_spot: float = 15.0
    has_contributing: float = 8.0
    low_competition: float = 7.0
    issue_clarity: float = 5.0
    total: float = 100.0

    # Thresholds used by the individual checks.
    stale_days: int = 45
    merge_window_days: int = 30
    merge_fast_days: float = 7.0
    merge_slow_days: float = 45.0
    stars_min: int = 30
    stars_max: int = 3000


@dataclass
class RepoHealth:
    """Facts about a repository, gathered once per repo per run."""

    full_name: str
    stars: int = 0
    forks: int = 0
    open_issues: int = 0
    pushed_days_ago: float | None = None
    merged_last_30d: int = 0
    median_merge_days: float | None = None
    has_contributing: bool = False
    archived: bool = False
    language: str = ""

    @property
    def alive(self) -> bool:
        return not self.archived and (
            self.pushed_days_ago is not None and self.pushed_days_ago <= 45
        )


@dataclass
class Candidate:
    """An issue plus everything we learned about it."""

    repo: str
    number: int
    title: str
    url: str
    labels: list[str] = field(default_factory=list)
    comments: int = 0
    created_days_ago: float | None = None
    updated_days_ago: float | None = None
    body_length: int = 0
    assignees: list[str] = field(default_factory=list)
    linked_prs: int = 0
    health: RepoHealth | None = None
    score: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}"

    @property
    def claimed(self) -> bool:
        return bool(self.assignees) or self.linked_prs > 0

    def describe(self) -> str:
        return f"{self.key} [{self.score:.0f}/100] {self.title}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "number": self.number,
            "title": self.title,
            "url": self.url,
            "score": round(self.score, 1),
            "labels": self.labels,
            "comments": self.comments,
            "claimed": self.claimed,
            "created_days_ago": self.created_days_ago,
            "updated_days_ago": self.updated_days_ago,
            "breakdown": {k: round(v, 1) for k, v in self.breakdown.items()},
            "warnings": self.warnings,
            "repo_health": (
                {
                    "stars": self.health.stars,
                    "forks": self.health.forks,
                    "open_issues": self.health.open_issues,
                    "pushed_days_ago": self.health.pushed_days_ago,
                    "merged_last_30d": self.health.merged_last_30d,
                    "median_merge_days": self.health.median_merge_days,
                    "has_contributing": self.health.has_contributing,
                    "archived": self.health.archived,
                    "language": self.health.language,
                }
                if self.health
                else None
            ),
        }


def _days_since(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        cleaned = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return max(delta.total_seconds() / 86400.0, 0.0)
    except (ValueError, TypeError):
        return None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


class Scout:
    """Search for issues and rank them by contribution friendliness."""

    def __init__(
        self,
        github: GitHub | None = None,
        weights: ScoreWeights | None = None,
        labels: tuple[str, ...] = DEFAULT_LABELS,
    ) -> None:
        self.github = github or GitHub()
        self.weights = weights or ScoreWeights()
        self.labels = labels

    # -- search ------------------------------------------------------------

    def build_query(
        self,
        language: str | None = None,
        label: str | None = None,
        min_stars: int | None = None,
        extra: str = "",
    ) -> str:
        """Compose the GitHub search string."""
        parts = ["is:issue", "is:open", "no:assignee"]
        if language:
            parts.append(f"language:{language}")
        if label:
            parts.append(f'label:"{label}"')
        if min_stars:
            parts.append(f"stars:>={min_stars}")
        if extra:
            parts.append(extra.strip())
        return "+".join(parts)

    def find_issues(
        self,
        language: str | None = None,
        label: str | None = None,
        limit: int = 40,
        extra: str = "",
    ) -> list[dict[str, Any]]:
        label = label or self.labels[0]
        query = self.build_query(language=language, label=label, extra=extra)
        try:
            return self.github.search_issues(query, limit=limit)
        except GitHubError as exc:
            raise ScoutError(f"search failed: {exc}") from exc

    # -- enrichment --------------------------------------------------------

    def repo_health(self, full_name: str) -> RepoHealth:
        """Gather repository facts, tolerating partial failures."""
        data = self.github.repo(full_name) or {}
        health = RepoHealth(
            full_name=full_name,
            stars=int(data.get("stargazers_count") or 0),
            forks=int(data.get("forks_count") or 0),
            open_issues=int(data.get("open_issues_count") or 0),
            pushed_days_ago=_days_since(data.get("pushed_at")),
            archived=bool(data.get("archived")),
            language=str(data.get("language") or ""),
        )

        # A CONTRIBUTING file is a decent proxy for "outsiders are welcome".
        if data.get("has_contributing_guide") is not None:
            health.has_contributing = bool(data.get("has_contributing_guide"))
        else:
            contrib = self.github._api_soft(
                f"repos/{full_name}/contents/CONTRIBUTING.md"
            )
            health.has_contributing = bool(contrib)

        # Merge behaviour over the last month, from closed pull requests.
        merges: list[float] = []
        merged_recent = 0
        cutoff = self.weights.merge_window_days
        for pr in self.github.recent_merged_prs(full_name, limit=30):
            if not pr.get("merged_at"):
                continue
            opened = _days_since(pr.get("created_at"))
            merged = _days_since(pr.get("merged_at"))
            if merged is not None and merged <= cutoff:
                merged_recent += 1
            if opened is not None and merged is not None:
                merges.append(max(opened - merged, 0.0))
        health.merged_last_30d = merged_recent
        health.median_merge_days = _median(merges)

        return health

    def enrich(self, item: dict[str, Any]) -> Candidate | None:
        """Turn a search result into a Candidate, or None if unusable."""
        try:
            repo = self.github.split_repo(item)
        except GitHubError:
            return None
        number = int(item.get("number") or 0)
        if not number:
            return None

        health = self.repo_health(repo)
        if health.archived:
            return None
        # Repositories so small they are effectively personal scratchpads add
        # noise, and repositories with a dead default branch almost never merge.
        if health.stars < self.weights.stars_min:
            return None
        # A stale default branch means pull requests sit around unmerged.
        if (
            health.pushed_days_ago is not None
            and health.pushed_days_ago > self.weights.stale_days
        ):
            return None

        labels = [
            str((lbl or {}).get("name") or "").lower()
            for lbl in (item.get("labels") or [])
        ]
        assignees = [
            str((a or {}).get("login") or "") for a in (item.get("assignees") or [])
        ]
        body = item.get("body") or ""

        linked = self.github.search_prs_for_issue(repo, number)

        candidate = Candidate(
            repo=repo,
            number=number,
            title=(item.get("title") or "").strip(),
            url=item.get("html_url") or "",
            labels=labels,
            comments=int(item.get("comments") or 0),
            created_days_ago=_days_since(item.get("created_at")),
            updated_days_ago=_days_since(item.get("updated_at")),
            body_length=len(body),
            assignees=[a for a in assignees if a],
            linked_prs=len(linked),
            health=health,
        )
        self.score(candidate)
        return candidate

    # -- scoring -----------------------------------------------------------

    def score(self, candidate: Candidate) -> Candidate:
        """Fill in score, breakdown and warnings. Mutates and returns."""
        w = self.weights
        health = candidate.health
        breakdown: dict[str, float] = {}
        warnings: list[str] = []

        # Unclaimed: the single strongest predictor of a good first experience.
        if not candidate.assignees and candidate.linked_prs == 0:
            breakdown["unclaimed"] = w.unclaimed
        else:
            breakdown["unclaimed"] = 0.0
            if candidate.assignees:
                warnings.append(f"already assigned to {', '.join(candidate.assignees)}")
            if candidate.linked_prs:
                warnings.append(f"{candidate.linked_prs} open PR(s) reference it")

        # Repository liveness.
        if health and health.alive:
            breakdown["repo_alive"] = w.repo_alive
        else:
            breakdown["repo_alive"] = 0.0
            warnings.append("repository looks inactive")

        # Merge responsiveness: how long maintainers take to land PRs.
        if health and health.median_merge_days is not None:
            days = health.median_merge_days
            if days <= w.merge_fast_days:
                breakdown["merge_speed"] = w.merge_speed
            elif days >= w.merge_slow_days:
                breakdown["merge_speed"] = 0.0
                warnings.append(f"slow merges (median {days:.0f}d)")
            else:
                span = w.merge_slow_days - w.merge_fast_days
                ratio = 1.0 - ((days - w.merge_fast_days) / span)
                breakdown["merge_speed"] = round(w.merge_speed * ratio, 2)
        else:
            breakdown["merge_speed"] = 0.0

        # Stars: a sweet spot, not a maximisation. Very large projects ignore
        # newcomers, very small ones are abandoned.
        if health:
            stars = health.stars
            if w.stars_min <= stars <= w.stars_max:
                breakdown["stars_sweet_spot"] = w.stars_sweet_spot
            elif stars > w.stars_max:
                breakdown["stars_sweet_spot"] = round(w.stars_sweet_spot * 0.4, 2)
                warnings.append(f"very popular repo ({stars} stars), competition likely")
            else:
                breakdown["stars_sweet_spot"] = 0.0
        else:
            breakdown["stars_sweet_spot"] = 0.0

        # Contributing guide.
        if health and health.has_contributing:
            breakdown["has_contributing"] = w.has_contributing
        else:
            breakdown["has_contributing"] = 0.0

        # Low competition: few comments usually means nobody has grabbed it.
        if candidate.comments <= 2:
            breakdown["low_competition"] = w.low_competition
        elif candidate.comments <= 6:
            breakdown["low_competition"] = round(w.low_competition * 0.5, 2)
        else:
            breakdown["low_competition"] = 0.0
            warnings.append(f"busy thread ({candidate.comments} comments)")

        # Issue clarity: a description with substance is easier to act on.
        if candidate.body_length >= 200:
            breakdown["issue_clarity"] = w.issue_clarity
        elif candidate.body_length >= 60:
            breakdown["issue_clarity"] = round(w.issue_clarity * 0.5, 2)
        else:
            breakdown["issue_clarity"] = 0.0
            warnings.append("thin issue description")

        candidate.breakdown = breakdown
        candidate.warnings = warnings
        candidate.score = round(sum(breakdown.values()), 2)
        return candidate

    # -- top level ---------------------------------------------------------

    def search(
        self,
        language: str | None = None,
        label: str | None = None,
        limit: int = 15,
        scan: int = 40,
        min_score: float = 0.0,
        include_claimed: bool = False,
    ) -> list[Candidate]:
        """Search, enrich, score, and return candidates best first."""
        items = self.find_issues(language=language, label=label, limit=scan)
        results: list[Candidate] = []
        for item in items:
            try:
                cand = self.enrich(item)
            except GitHubError:
                continue
            if cand is None:
                continue
            if not include_claimed and cand.claimed:
                continue
            if cand.score < min_score:
                continue
            results.append(cand)
        results.sort(key=lambda c: (-c.score, c.repo, c.number))
        return results[:limit]
