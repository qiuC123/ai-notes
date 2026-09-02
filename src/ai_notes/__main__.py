from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ai_notes.learning import prepare_learning
from ai_notes.review import (
    finalize_learning,
    list_pending_feedback,
    list_watched_projects,
    record_feedback,
    validate_learning_decisions,
)
from ai_notes.storage import sha256_file
from ai_notes.trial import evaluate_trial


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare and validate evidence-gated GitHub co-learning runs.")
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare-learning", help="pin a GitHub target and build a bounded evidence queue")
    prepare.add_argument("github_url")
    prepare.add_argument("--project", required=True, type=Path, help="authorized owned-project Git root")
    prepare.add_argument("--root", default=Path.cwd(), type=Path, help="Ai Notes repository root")
    prepare.add_argument("--include", action="append", default=[], help="pinned same-repository GitHub blob URL")
    prepare.add_argument("--entry-mode", choices=["nominated", "discovered"], default="nominated")
    prepare.add_argument("--discovery", type=Path, help="bounded active-discovery provenance JSON")
    prepare.add_argument("--run-id", help="extend an unfinalized run with additional evidence")

    finalize = commands.add_parser("finalize-learning", help="validate decisions or record explicit user feedback")
    finalize.add_argument("run_id")
    finalize.add_argument("--root", default=Path.cwd(), type=Path, help="Ai Notes repository root")
    action = finalize.add_mutually_exclusive_group(required=True)
    action.add_argument("--decisions", type=Path, help="Codex-produced learning-decisions.v1 JSON")
    action.add_argument("--feedback", choices=["continue", "ignore", "watch", "experiment"])

    validate = commands.add_parser("validate-learning", help="preflight decisions without changing run state")
    validate.add_argument("run_id")
    validate.add_argument("--root", default=Path.cwd(), type=Path, help="Ai Notes repository root")
    validate.add_argument("--decisions", required=True, type=Path, help="Codex-produced learning-decisions.v1 JSON")

    trial = commands.add_parser("trial-status", help="report whether manual validation permits automation")
    trial.add_argument("--root", default=Path.cwd(), type=Path, help="Ai Notes repository root")

    watches = commands.add_parser("watch-status", help="list explicitly watched external projects")
    watches.add_argument("--root", default=Path.cwd(), type=Path, help="Ai Notes repository root")

    feedback = commands.add_parser("feedback-status", help="list successful runs awaiting explicit feedback")
    feedback.add_argument("--root", default=Path.cwd(), type=Path, help="Ai Notes repository root")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "prepare-learning":
            result = prepare_learning(
                root=root,
                github_url=args.github_url,
                project_path=args.project,
                include_urls=tuple(args.include),
                entry_mode=args.entry_mode,
                discovery_input_path=args.discovery.resolve() if args.discovery else None,
                run_id=args.run_id,
            )
            print(
                json.dumps(
                    {
                        "run_id": result.run_id,
                        "status": result.status,
                        "queue_path": str(result.queue_path) if result.queue_path else None,
                        "queue_sha256": sha256_file(result.queue_path) if result.queue_path else None,
                        "manifest_path": str(result.manifest_path) if result.manifest_path else None,
                        "missing_scopes": list(result.missing_scopes),
                    },
                    ensure_ascii=False,
                )
            )
            return 0 if result.status == "success" else (2 if result.status == "partial" else 1)
        if args.command == "validate-learning":
            result = validate_learning_decisions(
                root=root,
                run_id=args.run_id,
                input_decisions_path=args.decisions.resolve(),
            )
            print(
                json.dumps(
                    {
                        "run_id": result.run_id,
                        "status": "valid",
                        "queue_sha256": result.queue_sha256,
                        "decisions_sha256": result.decisions_sha256,
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "trial-status":
            result = evaluate_trial(root)
            print(json.dumps(result.as_dict(), ensure_ascii=False))
            return 0 if result.eligible_for_automation else 2
        if args.command == "watch-status":
            print(json.dumps({"projects": list_watched_projects(root)}, ensure_ascii=False))
            return 0
        if args.command == "feedback-status":
            print(json.dumps({"runs": list_pending_feedback(root)}, ensure_ascii=False))
            return 0
        if args.decisions is not None:
            result = finalize_learning(
                root=root,
                run_id=args.run_id,
                input_decisions_path=args.decisions.resolve(),
            )
            print(
                json.dumps(
                    {
                        "run_id": result.run_id,
                        "status": result.status,
                        "healthy_no_connection": result.healthy_no_connection,
                        "manifest_path": str(result.manifest_path),
                        "decisions_path": str(result.decisions_path) if result.decisions_path else None,
                        "validation_errors": list(result.validation_errors),
                    },
                    ensure_ascii=False,
                )
            )
            return 0 if result.status == "success" else (2 if result.status == "partial" else 1)
        feedback = record_feedback(root=root, run_id=args.run_id, feedback=args.feedback)
        print(
            json.dumps(
                {
                    "run_id": feedback.run_id,
                    "feedback": feedback.feedback,
                    "recorded": feedback.recorded,
                    "relation_id": feedback.relation_id,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as error:
        print(f"Ai Notes {args.command} failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
