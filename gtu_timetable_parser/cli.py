"""Command line entry point: python -m gtu_timetable_parser"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import db, fet, normalize, sources


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gtu_timetable_parser",
        description="Build one SQLite timetable file from the GTU teachers/groups/rooms pages.",
    )
    ap.add_argument("-o", "--out-dir", type=Path, default=Path("output"),
                    help="directory for the .sqlite file (default: ./output)")
    ap.add_argument("--url", default=sources.START_URL,
                    help=f"start page that redirects to the current period (default: {sources.START_URL})")
    ap.add_argument("--save-sources", type=Path, metavar="DIR",
                    help="also store the downloaded HTML pages in DIR")
    ap.add_argument("--from-sources", type=Path, metavar="DIR",
                    help="parse pages stored with --save-sources instead of downloading")
    args = ap.parse_args(argv)

    if args.from_sources:
        src = sources.load(args.from_sources)
    else:
        print(f"Downloading from {args.url} ...", file=sys.stderr)
        src = sources.download(args.url)
    if args.save_sources:
        sources.save(src, args.save_sources)

    print(f"Period {src.period}, week {src.week}", file=sys.stderr)
    views = {}
    for view in sources.VIEWS:
        page = src.views[view]
        views[view] = fet.parse(page.html)
        print(f"  {view}: {len(views[view][1])} tables  <- {page.url}", file=sys.stderr)

    tt = normalize.build(views)
    path, counts = db.write(tt, src, args.out_dir)

    print(f"Wrote {path}")
    print("  " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    for msg, n in sorted(tt.warnings.items()):
        print(f"  warning: {msg}: {n}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
