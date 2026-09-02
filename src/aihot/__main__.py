from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from aihot.ai_notes import build_default_ai_notes_pipeline, build_default_backtester
from aihot.release_sources import load_release_sources
from aihot.source_evaluation import write_trial_evaluation


def _default_date() -> str:
    return datetime.now(UTC).date().isoformat()


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect and verify high-quality AI release information with Ai Notes.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    daily = subcommands.add_parser("daily", help="run Collect → Codex Review → Finalize for one day")
    daily.add_argument("--date", default=_default_date(), type=date.fromisoformat, help="UTC run date in YYYY-MM-DD form")
    daily.add_argument("--root", default=Path.cwd(), type=Path, help="repository root containing config/")
    daily.add_argument("--decisions", type=Path, help="Codex-produced decisions JSON for the prepared queue")
    backtest = subcommands.add_parser("backtest", help="run a complete historical source review")
    backtest.add_argument("--date", default=_default_date(), type=date.fromisoformat, help="UTC end date")
    backtest.add_argument("--days", default=90, type=_positive_int)
    backtest.add_argument("--root", default=Path.cwd(), type=Path)
    backtest.add_argument("--decisions", type=Path, help="Codex-produced decisions JSON for eligible releases")
    evaluate = subcommands.add_parser("evaluate", help="evaluate a completed online source trial")
    evaluate.add_argument("--start-date", required=True, type=date.fromisoformat)
    evaluate.add_argument("--days", default=14, type=_positive_int)
    evaluate.add_argument("--root", default=Path.cwd(), type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "daily":
            result = build_default_ai_notes_pipeline(
                root, decisions_path=args.decisions.resolve() if args.decisions else None
            ).run(args.date.isoformat())
            print(f"Wrote {result.accepted_information_path}")
            return 0 if result.status == "success" else (2 if result.status == "partial" else 1)
        if args.command == "backtest":
            report = build_default_backtester(
                root, decisions_path=args.decisions.resolve() if args.decisions else None
            ).run(as_of=args.date.isoformat(), days=args.days)
            print(f"Reviewed {report['eligible_count']} eligible historical releases")
            return 0 if all(item["coverage_complete"] for item in report["sources"]) else 2
        if args.command == "evaluate":
            _, json_path, _ = write_trial_evaluation(
                root=root,
                sources=load_release_sources(root / "config" / "ai_notes_sources.yaml"),
                start_date=args.start_date.isoformat(),
                days=args.days,
            )
            print(f"Wrote {json_path}")
            return 0
    except Exception as error:
        print(f"Ai Notes {args.command} failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
