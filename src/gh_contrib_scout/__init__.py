"""gh-contrib-scout: find open source issues that are actually worth picking up.

Most "good first issue" searches return tickets that are already claimed, sit in
abandoned repositories, or belong to projects so large that a newcomer's patch
never gets read. This tool scores each candidate on the things that predict a
good first contribution experience:

  * the issue is unclaimed (no assignee, no linked pull request)
  * the repository is alive (recent commits, recent merges)
  * maintainers are responsive (pull requests get merged reasonably fast)
  * the project is small enough that a new contributor is noticed

Public API::

    from gh_contrib_scout import GitHub, Scout, ScoreWeights

    scout = Scout(GitHub())
    for cand in scout.search("python", limit=10):
        print(cand.issue.title, cand.score)
"""

from .github import GitHub, GitHubError
from .scout import Candidate, RepoHealth, ScoreWeights, Scout, ScoutError

__all__ = [
    "GitHub",
    "GitHubError",
    "Scout",
    "ScoutError",
    "Candidate",
    "RepoHealth",
    "ScoreWeights",
]

__version__ = "0.1.0"
