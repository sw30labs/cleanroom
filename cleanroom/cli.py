"""Cleanroom — Command Line

Run inspect / run / server from the terminal.

Single entry point so the pipeline is scriptable and CI-friendly.

Author: Nic Cravino
Email: spidernic@me.com
LinkedIn: https://www.linkedin.com/in/nic-cravino
Date: August 13, 2026
License: MIT
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _cmd_inspect(args: argparse.Namespace) -> int:
    from .files import classify, extract_text

    p = Path(args.input).expanduser()
    profile = classify(p)
    if not profile.exists:
        print(f"error: {p} not found", file=sys.stderr)
        return 1
    profile = extract_text(profile)
    payload = {
        "path": str(profile.path),
        "size_bytes": profile.size_bytes,
        "extension": profile.extension,
        "kind": profile.kind,
        "container_note": profile.container_note,
        "detected_charset": profile.detected_charset,
        "extracted_chars": len(profile.extracted_text),
        "marks": profile.mark_report,
        "metadata_hints": profile.metadata_hints,
        "extraction_error": profile.extraction_error,
    }
    print(json.dumps(payload, indent=2, default=str))
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from .pipeline import run_cleanroom

    try:
        result = run_cleanroom(
            args.input,
            strength=args.strength,
            apply=not args.preview,
            skip_llm=args.offline,
            output_dir=args.out,
        )
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result.summary(), indent=2, default=str))
    if result.errors:
        print("\nErrors:", file=sys.stderr)
        for e in result.errors:
            print(f"  - {e}", file=sys.stderr)
        return 0 if result.state.get("final") else 1
    return 0


def _cmd_server(args: argparse.Namespace) -> int:
    from .web.server import main as server_main

    return server_main([f"--host={args.host}", f"--port={args.port}"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cleanroom", description="Agentic file-hygiene pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser("inspect", help="classify and inspect a file")
    p_inspect.add_argument("input")
    p_inspect.set_defaults(func=_cmd_inspect)

    p_run = sub.add_parser("run", help="run the full Analyze→Recommend→Remediate→QA workflow")
    p_run.add_argument("input")
    p_run.add_argument("--strength", choices=["safe", "aggressive"], default=None,
                       help="remediation strength (default from cleanroom.yaml)")
    p_run.add_argument("--out", default=None, help="output directory")
    p_run.add_argument("--preview", action="store_true",
                       help="run without writing the cleaned file")
    p_run.add_argument("--offline", action="store_true",
                       help="force the deterministic path (skip LLM even if reachable)")
    p_run.set_defaults(func=_cmd_run)

    p_server = sub.add_parser("server", help="start the web dashboard")
    p_server.add_argument("--host", default="127.0.0.1")
    p_server.add_argument("--port", type=int, default=8080)
    p_server.set_defaults(func=_cmd_server)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())