# gh-contrib-scout

Find open source issues that are **actually worth your time**.

Searching `label:"good first issue"` on GitHub returns a lot of noise: tickets
someone already claimed, repositories where the last commit was two years ago,
or projects so large that a newcomer's patch never gets reviewed. This tool
filters those out and scores what is left, so you spend your evening on
something that has a realistic chance of being merged.

```
$ gh-contrib-scout find python

 92.0  acme/parser#418  (312★, 7 merged/30d)
       Crash when parsing empty YAML documents
       labels: good first issue, bug
       https://github.com/acme/parser/issues/418

 74.5  otherlib/core#91  (1250★, 3 merged/30d)
       Add typing stubs for the client class
       labels: good first issue
       https://github.com/otherlib/core/issues/91
       watch out: very popular repo (1250 stars), competition likely
```

## What makes an issue "good"

Each signal is something an experienced contributor checks before starting, and
each one contributes a fixed number of points out of 100:

| Signal | Points | Meaning |
| --- | --- | --- |
| **Unclaimed** | 30 | No assignee, and no open PR referencing it |
| **Repo alive** | 20 | Pushed within the last 45 days, not archived |
| **Merge speed** | 15 | Median time from PR open to merge, over recent PRs |
| **Stars sweet spot** | 15 | Between 30 and 3000 stars: noticed, but not swamped |
| **Contributing guide** | 8 | The project tells you how to contribute |
| **Low competition** | 7 | Few comments, so nobody has grabbed it yet |
| **Issue clarity** | 5 | A description with enough detail to act on |

Everything is a weighted sum, and the weights are a plain dataclass you can
override. Candidates are dropped entirely when the repository is archived, has
fewer than 30 stars, or has not been pushed in 45 days.

## Requirements

- Python 3.9 or newer
- [`gh`](https://cli.github.com) installed and authenticated (`gh auth login`)

## Install

```bash
git clone https://github.com/alorentiar/gh-contrib-scout
cd gh-contrib-scout
python -m venv .venv && . .venv/bin/activate
pip install -e .
```

Without installing:

```bash
python -m gh_contrib_scout find go
```

## Usage

```bash
# default label is "good first issue"
gh-contrib-scout find python

# other labels work too
gh-contrib-scout find rust --label "help wanted"
gh-contrib-scout find typescript --label documentation

# see why things scored the way they did
gh-contrib-scout find go --explain

# keep only strong candidates
gh-contrib-scout find python --min-score 70

# machine readable, for piping into something else
gh-contrib-scout find python --json | jq '.[0].url'

# score a specific issue
gh-contrib-scout inspect acme/parser#418
gh-contrib-scout inspect https://github.com/acme/parser/issues/418
```

Useful flags for `find`:

| Flag | Purpose |
| --- | --- |
| `-n, --limit` | how many results to print (default 10) |
| `--scan` | how many search hits to inspect before ranking (default 40) |
| `--min-score` | hide anything below this score |
| `--include-claimed` | do not filter out assigned or referenced issues |
| `--explain` | print the per-signal breakdown |
| `--json` | emit JSON |

Exit codes: `0` success, `1` error, `2` not authenticated, `4` nothing found.
The last one is useful in scripts that should distinguish "no results" from
"the run failed".

## Why the star range is capped

Popularity is not the goal. Above roughly 3000 stars, issue threads fill with
comments within hours, several people open overlapping pull requests, and
maintainers triage by familiarity. The tool still shows those repositories, just
at a reduced score, and adds a "competition likely" warning.

## Library use

The scoring is importable if you want to build on it:

```python
from gh_contrib_scout import GitHub, Scout, ScoreWeights

scout = Scout(GitHub())
candidates = scout.search(language="python", limit=5, min_score=60)
for c in candidates:
    print(c.score, c.key, c.title)
    print("   ", c.breakdown)      # per-signal points
    print("   ", c.warnings)       # why it might not be worth it
```

Tune the priorities by passing your own weights:

```python
weights = ScoreWeights(
    unclaimed=40.0,
    merge_speed=25.0,
    stars_sweet_spot=5.0,
)
scout = Scout(GitHub(), weights=weights)
```

## Development

```bash
pip install -e ".[dev]"
pytest          # ~33 tests, no network access needed
ruff check .
```

The tests fake the GitHub client entirely, so the suite runs offline in well
under a second.

## Limitations

- Scoring is heuristic. It predicts likely experience, not guaranteed outcome.
- The "is this claimed" check looks at assignees and open PRs that mention the
  issue. Someone could have started work without either signal.
- Repository metadata is fetched per repository, so a large `--scan` costs more
  API calls. Unauthenticated `gh` has a lower rate limit.

## License

MIT. See [LICENSE](LICENSE).
