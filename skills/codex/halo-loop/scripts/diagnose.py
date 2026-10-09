"""Checked HALO diagnostic entry; ChatGPT auth only, no automatic retries."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent


def positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("positive number required")
    return number


def plan(args: argparse.Namespace) -> dict[str, Any]:
    config_path = SKILL_DIR / "local.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    if not isinstance(config, dict):
        raise ValueError("local.json must be an object with runtime settings")
    runtime = args.halo_repo or os.environ.get("HALO_REPO") or config.get("halo_repo")
    model = args.model or config.get("diagnostic_model")
    if not runtime or not isinstance(model, str) or not model.strip():
        raise ValueError("set HALO checkout and supported diagnostic model")
    runtime = Path(runtime).expanduser().resolve()
    for name in ["pyproject.toml", "uv.lock", "engine/chatgpt_auth.py"]:
        if not (runtime / name).is_file():
            raise ValueError("ChatGPT-enabled HALO checkout missing: " + name)
    if not (runtime / ".venv").is_dir():
        raise ValueError("HALO environment missing; set it up before diagnostic execution")
    if not shutil.which("uv"):
        raise ValueError("uv not found; reuse or set up HALO environment first")
    trace = args.traces.expanduser().resolve()
    prompt = args.prompt_file.expanduser().resolve()
    output = args.output.expanduser().absolute()
    if output.exists() or any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("fresh nonsymlink report path required; preserve old evidence")
    if not output.parent.is_dir():
        raise ValueError("report parent must already exist")
    body = prompt.read_text()
    if not body.strip():
        raise ValueError("nonempty diagnostic prompt required")
    raw = trace.read_bytes()
    spans = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    if not spans:
        raise ValueError("nonempty plain JSONL trace required")
    ids = set()
    for row in spans:
        if not isinstance(row, dict):
            raise ValueError("trace row must be a span object")
        for key in ["trace_id", "span_id", "name", "kind", "start_time", "end_time"]:
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError("span string field missing: " + key)
        for key in ["status", "resource", "scope", "attributes"]:
            if not isinstance(row.get(key), dict):
                raise ValueError("span object field missing: " + key)
        if not isinstance(row["status"].get("code"), str):
            raise ValueError("span status.code missing")
        if not isinstance(row["resource"].get("attributes"), dict):
            raise ValueError("span resource.attributes missing")
        if not isinstance(row["scope"].get("name"), str):
            raise ValueError("span scope.name missing")
        identity = (row["trace_id"], row["span_id"])
        if identity in ids:
            raise ValueError("duplicate trace/span identity")
        ids.add(identity)
    argv = [
        "uv",
        "run",
        "--locked",
        "--no-sync",
        "halo",
        str(trace),
        "--auth-mode",
        "chatgpt",
        "--model",
        model,
        "--max-depth",
        "0",
        "--max-turns",
        str(args.max_turns),
        "--refusal-retries",
        "0",
        "-p",
        body,
    ]
    if args.repo:
        repo = args.repo.expanduser().resolve()
        if not repo.is_dir():
            raise ValueError("harness repo must exist")
        argv.extend(["--repo-path", str(repo)])
    return {
        "argv": argv,
        "cwd": str(runtime),
        "output": str(output),
        "TraceSha256": hashlib.sha256(raw).hexdigest(),
        "PromptSha256": hashlib.sha256(body.encode()).hexdigest(),
        "Spans": len(spans),
        "AuthMode": "chatgpt",
        "Model": model,
    }


def execute(args: argparse.Namespace, *, executor=subprocess.run) -> dict[str, Any]:
    stage = "preflight"
    prepared = None
    try:
        prepared = plan(args)
        receipt = {k: v for k, v in prepared.items() if k not in ["argv", "cwd"]}
        if args.check:
            return {"Result": "PREFLIGHT_PASS", "Scope": "INPUT_STRUCTURE_ONLY", **receipt}
        stage = "analysis"
        output = Path(prepared["output"])
        # Exclusive open closes the preflight/execution race; no overwrites.
        with output.open("x") as stream:
            proc = executor(
                prepared["argv"],
                cwd=prepared["cwd"],
                text=True,
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=args.timeout,
            )
        if proc.returncode or "final=True" not in output.read_text():
            raise ValueError("HALO incomplete; preserve report, no automatic retry")
        return {"Result": "PASS", "Scope": "HALO_DIAGNOSTIC_ONLY", **receipt}
    except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired) as error:
        return {
            "Result": "TOOL_ERROR",
            "FailureStage": stage,
            "FirstIssue": str(error),
            "Output": prepared["output"] if stage == "analysis" and prepared else None,
            "Boundary": "Not an AI behavior or DUT result; stop downstream stages.",
        }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--traces", type=Path, required=True)
    p.add_argument("--prompt-file", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--halo-repo")
    p.add_argument("--model")
    p.add_argument("--repo", type=Path)
    p.add_argument("--max-turns", type=positive, default=8)
    p.add_argument("--timeout", type=positive, default=300)
    p.add_argument("--check", action="store_true")
    return p


if __name__ == "__main__":
    result = execute(parser().parse_args())
    print(json.dumps(result, ensure_ascii=False), flush=True)
    raise SystemExit(0 if result["Result"] in ["PASS", "PREFLIGHT_PASS"] else 2)
