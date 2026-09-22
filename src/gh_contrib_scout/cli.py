"""Command line interface for gh-contrib-scout."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .github import GitHub, GitHubError
from .scout import DEFAULT_LABELS, Scout, ScoutError

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_AUTH = 2
EXIT_NOTHING_FOUND = 4


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gh-contrib-scout",
        description=(
            "Find open source issues worth contributing to: unclaimed work in "
            "alive, responsive, mid-sized repositories."
        ),
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = p.add_subparsers(dest="command", required=True)

    find = sub.add_parser("find", help="search and rank issues")
    find.add_argument("language", nargs="?", help="language filter, e.g. python, go, rust")
    find.add_argument(
        "-l",
        "--label",
        default=None,
        help=f"issue label (default: '{DEFAULT_LABELS[0]}')",
    )
    find.add_argument(
        "-n", "--limit", type=int, default=10, help="how many results to show"
    )
    find.add_argument(
        "--scan",
        type=int,
        default=40,
        help="how many search hits to inspect before ranking",
    )
    find.add_argument(
        "--min-score", type=float, default=0.0, help="hide candidates below this score"
    )
    find.add_argument(
        "--include-claimed",
        action="store_true",
        help="do not filter out assigned or referenced issues",
    )
    find.add_argument("--json", action="store_true", help="emit JSON")
    find.add_argument(
        "--explain", action="store_true", help="show the score breakdown per candidate"
    )

    insp = sub.add_parser("inspect", help="score one issue by repo and number")
    insp.add_argument("target", help="'owner/repo#123' or a full issue URL")
    insp.add_argument("--json", action="store_true")

    labs = sub.add_parser("labels", help="show the labels this tool searches by default")
    labs.add_argument("--json", action="store_true")

    sub.add_parser("whoami", help="check gh authentication")

    return p


def _parse_target(target: str) -> tuple[str, int] | None:
    """Accept 'owner/repo#123' or 'https://github.com/owner/repo/issues/123'."""
    text = target.strip().rstrip("/")
    if "/issues/" in text:
        head, _, tail = text.partition("/issues/")
        parts = head.rstrip("/").split("/")
        if len(parts) >= 2 and tail.isdigit():
            return f"{parts[-2]}/{parts[-1]}", int(tail)
        return None
    if "#" in text:
        repo, _, num = text.partition("#")
        if "/" in repo and num.isdigit():
            return repo, int(num)
    return None


def _print_candidates(cands, as_json: bool, explain: bool) -> None:
    if as_json:
        print(json.dumps([c.as_dict() for c in cands], indent=2))
        return
    for c in cands:
        health = c.health
        stars = f"{health.stars}★" if health else "?"
        merged = (
            f"{health.merged_last_30d} merged/30d"
            if health and health.merged_last_30d
            else "no recent merges"
        )
        print(f"{c.score:5.1f}  {c.repo}#{c.number}  ({stars}, {merged})")
        print(f"       {c.title}")
        if c.labels:
            print(f"       labels: {', '.join(c.labels)}")
        print(f"       {c.url}")
        if c.warnings:
            print(f"       watch out: {'; '.join(c.warnings)}")
        if explain:
            bits = ", ".join(f"{k}={v}" for k, v in sorted(c.breakdown.items()))
            print(f"       score: {bits}")
        print()


def cmd_find(args: argparse.Namespace) -> int:
    scout = Scout(GitHub())
    try:
        cands = scout.search(
            language=args.language,
            label=args.label,
            limit=args.limit,
            scan=args.scan,
            min_score=args.min_score,
            include_claimed=args.include_claimed,
        )
    except (ScoutError, GitHubError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NO_AUTH if "auth" in str(exc).lower() else EXIT_ERROR

    if not cands:
        if args.json:
            print("[]")
        else:
            print("no candidates matched. try a different language or --min-score 0")
        return EXIT_NOTHING_FOUND

    _print_candidates(cands, args.json, args.explain)
    return EXIT_OK


def cmd_inspect(args: argparse.Namespace) -> int:
    parsed = _parse_target(args.target)
    if not parsed:
        print(
            "could not parse target, use owner/repo#123 or an issue URL",
            file=sys.stderr,
        )
        return EXIT_ERROR
    repo, number = parsed

    gh = GitHub()
    item: dict = {}
    try:
        item = gh._api(f"repos/{repo}/issues/{number}") or {}
    except GitHubError as exc:
        print(f"could not fetch issue: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if not item:
        print(f"{repo}#{number} not found", file=sys.stderr)
        return EXIT_ERROR

    item.setdefault("repository_url", f"https://api.github.com/repos/{repo}")
    item.setdefault("number", number)
    scout = Scout(gh)
    cand = scout.enrich(item)
    if cand is None:
        print(f"{repo}#{number} was filtered out (archived, stale, or too small)")
        return EXIT_NOTHING_FOUND

    _print_candidates([cand], args.json, explain=True)
    return EXIT_OK


def cmd_labels(args: argparse.Namespace) -> int:
    if args.json:
        print(json.dumps(list(DEFAULT_LABELS), indent=2))
    else:
        for label in DEFAULT_LABELS:
            print(label)
    return EXIT_OK


def cmd_whoami(args: argparse.Namespace) -> int:
    ok, message = GitHub().check_auth()
    if not ok:
        print(message, file=sys.stderr)
        return EXIT_NO_AUTH
    print(message)
    return EXIT_OK


COMMANDS = {
    "find": cmd_find,
    "inspect": cmd_inspect,
    "labels": cmd_labels,
    "whoami": cmd_whoami,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = COMMANDS.get(args.command)
    if handler is None:
        parser.print_help()
        return EXIT_ERROR
    try:
        return handler(args)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
