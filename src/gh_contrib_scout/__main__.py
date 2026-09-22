"""Allow `python -m gh_contrib_scout`."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
