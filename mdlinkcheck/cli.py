"""Command-line interface for mdlinkcheck."""

import argparse
import json
import sys

from .core import check_paths


def build_parser():
    parser = argparse.ArgumentParser(
        prog="mdlinkcheck",
        description="Check links in Markdown files for broken URLs "
        "and missing local files.",
    )
    parser.add_argument(
        "paths", nargs="+",
        help="Markdown files or directories to scan",
    )
    parser.add_argument(
        "--timeout", type=float, default=10.0,
        help="seconds to wait for each HTTP request (default: 10)",
    )
    parser.add_argument(
        "--exclude", action="append", default=[],
        metavar="PATTERN",
        help="glob pattern or substring of URLs to skip "
        "(repeatable)",
    )
    parser.add_argument(
        "--ignore-file", default=None, metavar="FILE",
        help="file with one URL substring per line to skip",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="print the report as JSON instead of text",
    )
    parser.add_argument(
        "--only-broken", action="store_true",
        help="show only broken links in the text report",
    )
    return parser


def format_text(report, only_broken=False):
    lines = []
    for result in report.results:
        if only_broken and result.status != "broken":
            continue
        marker = {
            "ok": "OK    ",
            "broken": "BROKEN",
            "skipped": "SKIP  ",
        }[result.status]
        lines.append(
            f"{marker} {result.url}  ({result.source})"
            + (f" — {result.detail}" if result.detail else "")
        )
    lines.append(
        f"\n{report.files_scanned} file(s) scanned, "
        f"{len(report.results)} link(s): "
        f"{len(report.ok)} ok, {len(report.broken)} broken, "
        f"{len(report.skipped)} skipped."
    )
    if report.broken:
        lines.append(
            f"{len(report.broken)} broken link(s) found."
        )
    return "\n".join(lines)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be greater than 0")
    report = check_paths(
        args.paths,
        timeout=args.timeout,
        exclude=args.exclude,
        ignore_file=args.ignore_file,
        only_broken=args.only_broken,
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(format_text(report, only_broken=args.only_broken))
    for warning in report.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if report.broken:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
