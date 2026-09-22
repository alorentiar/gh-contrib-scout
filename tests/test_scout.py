"""Tests for the scoring logic.

Scoring is the part of this tool that people will disagree with, so it is the
part with the most tests: each signal is pinned down individually, then the
combinations that matter (claimed work, dead repos, huge projects).
"""

from __future__ import annotations

import json

from gh_contrib_scout.cli import _parse_target, main
from gh_contrib_scout.github import GitHub
from gh_contrib_scout.scout import (
    Candidate,
    RepoHealth,
    ScoreWeights,
    Scout,
    _days_since,
    _median,
)


class FakeGitHub(GitHub):
    """GitHub stand-in. Only the methods the scout actually uses."""

    def __init__(self, repo_data=None, merged=None, contents=None, linked=None,
                 search_items=None):
        super().__init__()
        self._repo_data = repo_data or {}
        self._merged = merged or {}
        self._contents = contents or {}
        self._linked = linked or {}
        self._search = search_items or []

    def viewer(self):
        return "tester"

    def repo(self, full_name):
        return self._repo_data.get(full_name, {})

    def recent_merged_prs(self, full_name, limit=20):
        return self._merged.get(full_name, [])

    def search_prs_for_issue(self, repo, number):
        return self._linked.get((repo, number), [])

    def search_issues(self, query, limit=50, sort="created"):
        return self._search[:limit]

    def _api_soft(self, path, fields=None):
        if path.endswith("/contents/CONTRIBUTING.md"):
            repo = path.split("repos/", 1)[1].rsplit("/contents/", 1)[0]
            return {"name": "CONTRIBUTING.md"} if self._contents.get(repo) else None
        return None


def healthy_repo(stars=200, pushed_days=2, archived=False, language="Python"):
    return {
        "stargazers_count": stars,
        "forks_count": 20,
        "open_issues_count": 15,
        "pushed_at": _iso_days_ago(pushed_days),
        "archived": archived,
        "language": language,
    }


def _iso_days_ago(days: float) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def issue_item(repo="acme/lib", number=10, title="Crash on empty input", **over):
    base = {
        "number": number,
        "title": title,
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "repository_url": f"https://api.github.com/repos/{repo}",
        "labels": [{"name": "good first issue"}],
        "comments": 0,
        "created_at": _iso_days_ago(5),
        "updated_at": _iso_days_ago(1),
        "body": "x" * 300,
        "assignees": [],
        "state": "open",
    }
    base.update(over)
    return base


def make_scout(gh, **kw):
    return Scout(github=gh, **kw)


# -- helpers --------------------------------------------------------------


def test_days_since_handles_bad_input():
    assert _days_since(None) is None
    assert _days_since("not a date") is None
    assert _days_since("2020-01-01T00:00:00Z") > 1000


def test_median():
    assert _median([]) is None
    assert _median([5]) == 5
    assert _median([1, 3]) == 2
    assert _median([3, 1, 2]) == 2


# -- repo health ----------------------------------------------------------


def test_repo_health_reads_merge_speed(fake=None):
    merged = [
        {
            "created_at": _iso_days_ago(10),
            "merged_at": _iso_days_ago(8),  # 2 days to merge
        },
        {
            "created_at": _iso_days_ago(20),
            "merged_at": _iso_days_ago(16),  # 4 days
        },
    ]
    gh = FakeGitHub(repo_data={"acme/lib": healthy_repo()}, merged={"acme/lib": merged})
    health = make_scout(gh).repo_health("acme/lib")
    assert health.stars == 200
    assert health.alive is True
    assert health.median_merge_days is not None
    assert abs(health.median_merge_days - 3.0) < 0.01
    assert health.merged_last_30d == 2


def test_repo_health_marks_stale_repo():
    gh = FakeGitHub(repo_data={"acme/lib": healthy_repo(pushed_days=200)})
    health = make_scout(gh).repo_health("acme/lib")
    assert health.alive is False


# -- scoring signals ------------------------------------------------------


def make_candidate(**kw) -> Candidate:
    base: dict = {
        "repo": "acme/lib",
        "number": 10,
        "title": "Crash on empty input",
        "url": "https://github.com/acme/lib/issues/10",
        "body_length": 300,
        "health": RepoHealth(
            full_name="acme/lib",
            stars=200,
            pushed_days_ago=2,
            merged_last_30d=5,
            median_merge_days=3.0,
            has_contributing=True,
        ),
    }
    base.update(kw)
    return Candidate(**base)


def test_perfect_candidate_scores_100():
    scout = make_scout(FakeGitHub())
    cand = scout.score(make_candidate())
    assert cand.score == 100.0
    assert cand.warnings == []


def test_assigned_issue_loses_unclaimed_points():
    scout = make_scout(FakeGitHub())
    cand = scout.score(make_candidate(assignees=["someone"]))
    assert cand.breakdown["unclaimed"] == 0
    assert any("already assigned" in w for w in cand.warnings)
    assert cand.claimed is True


def test_linked_pr_marks_claimed():
    scout = make_scout(FakeGitHub())
    cand = scout.score(make_candidate(linked_prs=1))
    assert cand.claimed is True
    assert cand.breakdown["unclaimed"] == 0


def test_slow_merges_reduce_score():
    scout = make_scout(FakeGitHub())
    health = RepoHealth(
        full_name="acme/lib", stars=200, pushed_days_ago=1, median_merge_days=60.0
    )
    cand = scout.score(make_candidate(health=health))
    assert cand.breakdown["merge_speed"] == 0
    assert any("slow merges" in w for w in cand.warnings)


def test_medium_merge_speed_is_proportional():
    scout = make_scout(FakeGitHub())
    health = RepoHealth(
        full_name="acme/lib", stars=200, pushed_days_ago=1, median_merge_days=26.0
    )
    cand = scout.score(make_candidate(health=health))
    value = cand.breakdown["merge_speed"]
    assert 0 < value < scout.weights.merge_speed


def test_huge_repo_is_penalised_but_not_excluded():
    scout = make_scout(FakeGitHub())
    health = RepoHealth(
        full_name="big/lib",
        stars=90_000,
        pushed_days_ago=1,
        median_merge_days=2.0,
        has_contributing=True,
    )
    cand = scout.score(make_candidate(health=health))
    assert 0 < cand.breakdown["stars_sweet_spot"] < scout.weights.stars_sweet_spot
    assert any("competition" in w for w in cand.warnings)


def test_busy_thread_loses_competition_points():
    scout = make_scout(FakeGitHub())
    cand = scout.score(make_candidate(comments=25))
    assert cand.breakdown["low_competition"] == 0
    assert any("busy thread" in w for w in cand.warnings)


def test_thin_description_loses_clarity_points():
    scout = make_scout(FakeGitHub())
    cand = scout.score(make_candidate(body_length=10))
    assert cand.breakdown["issue_clarity"] == 0
    assert any("thin issue" in w for w in cand.warnings)


def test_breakdown_sums_to_score():
    scout = make_scout(FakeGitHub())
    cand = scout.score(make_candidate(comments=4, body_length=100))
    assert abs(sum(cand.breakdown.values()) - cand.score) < 0.01


def test_weights_are_overridable():
    weights = ScoreWeights(unclaimed=50.0, repo_alive=0.0, merge_speed=0.0,
                           stars_sweet_spot=0.0, has_contributing=0.0,
                           low_competition=0.0, issue_clarity=0.0)
    scout = make_scout(FakeGitHub(), weights=weights)
    cand = scout.score(make_candidate())
    assert cand.score == 50.0


# -- enrichment filters ---------------------------------------------------


def test_enrich_rejects_archived_repo():
    gh = FakeGitHub(repo_data={"acme/lib": healthy_repo(archived=True)})
    assert make_scout(gh).enrich(issue_item()) is None


def test_enrich_rejects_tiny_repo():
    gh = FakeGitHub(repo_data={"acme/lib": healthy_repo(stars=3)})
    assert make_scout(gh).enrich(issue_item()) is None


def test_enrich_rejects_stale_repo():
    gh = FakeGitHub(repo_data={"acme/lib": healthy_repo(pushed_days=300)})
    assert make_scout(gh).enrich(issue_item()) is None


def test_enrich_accepts_healthy_repo():
    gh = FakeGitHub(repo_data={"acme/lib": healthy_repo()})
    cand = make_scout(gh).enrich(issue_item())
    assert cand is not None
    assert cand.repo == "acme/lib"
    assert cand.number == 10
    assert "good first issue" in cand.labels


# -- search ---------------------------------------------------------------


def test_search_sorts_by_score_and_filters_claimed():
    items = [
        issue_item(number=1),
        issue_item(number=2, assignees=[{"login": "taken"}]),
        issue_item(number=3, comments=40),
    ]
    gh = FakeGitHub(repo_data={"acme/lib": healthy_repo()}, search_items=items)
    results = make_scout(gh).search(limit=10)
    numbers = [c.number for c in results]
    assert 2 not in numbers, "assigned issue should be filtered out"
    scores = [c.score for c in results]
    assert scores == sorted(scores, reverse=True), "results must be best-first"
    # The quiet, well-described issue should outrank the busy thread.
    assert numbers.index(1) < numbers.index(3)


def test_search_can_include_claimed():
    items = [issue_item(number=2, assignees=[{"login": "taken"}])]
    gh = FakeGitHub(repo_data={"acme/lib": healthy_repo()}, search_items=items)
    results = make_scout(gh).search(limit=10, include_claimed=True)
    assert len(results) == 1
    assert results[0].claimed is True


def test_search_respects_min_score():
    items = [issue_item(number=1, comments=50, body="")]
    gh = FakeGitHub(repo_data={"acme/lib": healthy_repo()}, search_items=items)
    assert make_scout(gh).search(limit=10, min_score=90) == []


def test_search_respects_limit():
    items = [issue_item(number=i) for i in range(1, 11)]
    gh = FakeGitHub(repo_data={"acme/lib": healthy_repo()}, search_items=items)
    assert len(make_scout(gh).search(limit=3)) == 3


def test_build_query_includes_filters():
    scout = make_scout(FakeGitHub())
    q = scout.build_query(language="python", label="help wanted")
    assert "language:python" in q
    assert "is:issue" in q
    assert "no:assignee" in q
    assert 'label:"help wanted"' in q


# -- serialisation & cli --------------------------------------------------


def test_candidate_as_dict_is_json_safe():
    scout = make_scout(FakeGitHub())
    cand = scout.score(make_candidate())
    payload = json.loads(json.dumps(cand.as_dict()))
    assert payload["score"] == 100.0
    assert payload["repo_health"]["stars"] == 200


def test_parse_target_variants():
    assert _parse_target("owner/repo#12") == ("owner/repo", 12)
    assert _parse_target("https://github.com/owner/repo/issues/34") == ("owner/repo", 34)
    assert _parse_target("garbage") is None


def test_cli_labels(capsys):
    assert main(["labels"]) == 0
    out = capsys.readouterr().out
    assert "good first issue" in out


def test_cli_labels_json(capsys):
    assert main(["labels", "--json"]) == 0
    assert "help wanted" in json.loads(capsys.readouterr().out)


def test_cli_inspect_rejects_bad_target(capsys):
    assert main(["inspect", "not-a-target"]) == 1
    assert "could not parse" in capsys.readouterr().err
