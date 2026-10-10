"""Run pinned Promptfoo on paired recorded agent outputs; never rerun an agent implicitly."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
VERSION = "0.121.9"


def read(path: Path):
    return json.loads(path.read_text())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(args) -> dict:
    local = SKILL_DIR / "local.json"
    settings = read(local) if local.exists() else {}
    runtime = Path(args.promptfoo_home or settings.get("promptfoo_home", "")).resolve()
    package = runtime / "node_modules/promptfoo"
    if read(package / "package.json")["version"] != VERSION:
        raise ValueError("install the pinned Promptfoo version before evaluation")
    binary = package / "dist/src/entrypoint.js"
    if not binary.is_file():
        raise ValueError("Promptfoo runtime entry missing")
    suite_path, records_path = args.suite.resolve(), args.records.resolve()
    suite, records = read(suite_path), read(records_path)
    cases = suite["cases"]
    if not isinstance(cases, list) or not cases or not isinstance(records, list) or not records:
        raise ValueError("nonempty cases and recorded outputs required")
    ids = [c["id"] for c in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate case identity")
    if len({r["id"] for r in records}) != len(records):
        raise ValueError("duplicate run identity")
    known = set(ids)
    for r in records:
        if r["case_id"] not in known or r["variant"] not in ("before", "after"):
            raise ValueError("unknown case or variant")
        if (
            r["status"] != "COMPLETED"
            or not isinstance(r["output"], str)
            or not r["output"].strip()
        ):
            raise ValueError("only completed, nonempty agent outputs may be graded")
        if not isinstance(r["evidence"], list):
            raise ValueError("public tool evidence must be a list; use [] if none observed")
        for field in ("model", "input_id", "sample"):
            if not isinstance(r[field], str) or not r[field].strip():
                raise ValueError("nonempty recorded identity required: " + field)
        seconds = r.get("seconds")
        if seconds is not None and (
            type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0
        ):
            raise ValueError("invalid recorded agent latency")
    for c in cases:
        paired = [r for r in records if r["case_id"] == c["id"]]
        arms = {v: [r for r in paired if r["variant"] == v] for v in ("before", "after")}
        for arm in arms.values():
            if not arm or len({r["sample"] for r in arm}) != len(arm):
                raise ValueError("both arms need unique sample identities")

        def signature(rows):
            return sorted((r["sample"], r["model"], r["input_id"]) for r in rows)

        if signature(arms["before"]) != signature(arms["after"]):
            raise ValueError("paired samples, models and task input identities must match")
        if not isinstance(c["task"], str) or not c["task"].strip():
            raise ValueError("nonempty task required")
        assertions = c.get("assertions", [])
        for a in assertions:
            if a.get("type") not in ("contains", "icontains", "not-contains", "equals", "is-json"):
                raise ValueError("use supported deterministic assertions or a frozen rubric")
        if not assertions and not c.get("rubric"):
            raise ValueError("every case needs assertions or a rubric")
    output = args.output_dir.absolute()
    if output.exists() or any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("fresh nonsymlink output directory required")
    if not output.parent.is_dir():
        raise ValueError("output parent must exist")
    semantic = any(c.get("rubric") for c in cases)
    halo = args.halo_repo or settings.get("halo_repo")
    model = args.judge_model or settings.get("judge_model")
    python = (
        Path(halo).resolve()
        / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
        if halo
        else None
    )
    if semantic and (not halo or not python or not python.is_file() or not model):
        raise ValueError("configure HALO runtime and an explicit ChatGPT judge model")
    provider = "file://" + str(SKILL_DIR / "scripts/promptfoo_provider.py")
    tests = []
    for r in records:
        c = next(c for c in cases if c["id"] == r["case_id"])
        assertions = list(c.get("assertions", []))
        if c.get("rubric"):
            assert halo is not None
            assertions.append(
                {
                    "type": "llm-rubric",
                    "value": c["rubric"],
                    "threshold": 1,
                    "provider": {
                        "id": provider,
                        "config": {
                            "mode": "chatgpt",
                            "halo_repo": str(Path(halo).resolve()),
                            "model": model,
                            "report_dir": str(output),
                        },
                    },
                }
            )
        tests.append(
            {
                "description": r["id"],
                "vars": {
                    "record_id": r["id"],
                    "task": c["task"],
                    "evidence": json.dumps(r["evidence"], ensure_ascii=False),
                },
                "metadata": {"case_id": c["id"], "variant": r["variant"], "sample": r["sample"]},
                "assert": assertions,
            }
        )
    config = {
        "description": "HALO paired recorded-output evaluation",
        "prompts": ["{{task}}"],
        "providers": [
            {
                "id": provider,
                "config": {"mode": "recorded", "records": str(output / "records.json")},
            }
        ],
        "defaultTest": {
            "options": {
                "rubricPrompt": "Task: {{task}}\nPublic tool evidence: {{evidence}}\n"
                "Actual final answer: {{output}}\nFrozen criteria: {{rubric}}"
            }
        },
        "tests": tests,
    }
    return {
        "config": config,
        "records": records,
        "suite": suite,
        "binary": str(binary),
        "python": str(python) if python else None,
        "suite_sha256": digest(suite_path),
        "records_sha256": digest(records_path),
        "judge_model": model if semantic else None,
    }


def summarize(data: dict, records: list[dict]) -> dict:
    rows = data["results"]["results"]
    if len(rows) != len(records):
        raise ValueError("Promptfoo result count differs from expected recorded runs")
    if {r["testCase"]["description"] for r in rows} != {r["id"] for r in records}:
        raise ValueError("Promptfoo row identities do not match recorded runs")
    if any(r.get("response", {}).get("error") or r.get("failureReason") == 2 for r in rows):
        raise ValueError("provider or assertion execution error; not a behavior failure")
    variants = {}
    for v in ("before", "after"):
        arm = [r for r in records if r["variant"] == v]
        scored = [r for r in rows if r["testCase"]["metadata"]["variant"] == v]
        variants[v] = {
            "runs": len(arm),
            "passed": sum(r["success"] for r in scored),
            "agent_seconds": sum(r["seconds"] for r in arm)
            if all(r.get("seconds") is not None for r in arm)
            else None,
            "agent_usage": {
                k: sum(r["usage"][k] for r in arm)
                if all(
                    isinstance(r.get("usage"), dict) and r["usage"].get(k) is not None for r in arm
                )
                else None
                for k in ("input_tokens", "cached_input_tokens", "output_tokens")
            },
        }
    return {
        "variants": variants,
        "rows": [
            {
                "run": r["testCase"]["description"],
                "pass": r["success"],
                "score": r["score"],
                "grading": r.get("gradingResult"),
            }
            for r in rows
        ],
        "boundary": "Recorded-output grading; replay latency is not agent latency. "
        "No new agent execution or stable Harness benefit established; review semantic grades.",
    }


def execute(args, *, executor=subprocess.run) -> dict:
    output = args.output_dir.absolute()
    try:
        prepared = prepare(args)
        identity = {k: prepared[k] for k in ("suite_sha256", "records_sha256", "judge_model")}
        if args.check:
            return {"Result": "PREFLIGHT_PASS", "Scope": "INPUT_STRUCTURE_ONLY", **identity}
        output.mkdir()  # Exclusive creation; keep reports and any failures.
        for name, value in (
            ("config", prepared["config"]),
            ("records", prepared["records"]),
            ("suite", prepared["suite"]),
        ):
            (output / (name + ".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2))
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("OPENAI_API_KEY", "OPENAI_PROJECT", "OPENAI_ORGANIZATION")
        }
        env.update(
            PROMPTFOO_DISABLE_TELEMETRY="1",
            PROMPTFOO_DISABLE_UPDATE="1",
            PROMPTFOO_FAILED_TEST_EXIT_CODE="100",
            METADATA_SERVER_DETECTION="none",
            PROMPTFOO_CONFIG_DIR=str(output / ".promptfoo"),
            PROMPTFOO_DISABLE_REMOTE_GENERATION="true",
        )
        if prepared["python"]:
            env["PROMPTFOO_PYTHON"] = prepared["python"]
        argv = [
            "node",
            prepared["binary"],
            "eval",
            "-c",
            str(output / "config.json"),
            "-o",
            str(output / "results.json"),
            "--no-cache",
            "--no-table",
            "--max-concurrency",
            "1",
        ]
        with (output / "execution.log").open("x") as stream:
            proc = executor(
                argv,
                cwd=output,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=args.timeout,
            )
        if (
            proc.returncode not in (0, 100)
            or not (output / "results.json").is_file()
            or (output / "judge-error.json").exists()
        ):
            raise ValueError(
                "Promptfoo/grade execution failed; preserve first error, stop downstream"
            )
        summary = summarize(read(output / "results.json"), prepared["records"])
        if (
            digest(args.suite.resolve()) != identity["suite_sha256"]
            or digest(args.records.resolve()) != identity["records_sha256"]
        ):
            raise ValueError("evaluation inputs changed during execution")
        result = {"Result": "EVAL_COMPLETE", "PromptfooVersion": VERSION, **identity, **summary}
        (output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        StopIteration,
        subprocess.TimeoutExpired,
    ) as error:
        return {
            "Result": "TOOL_ERROR",
            "FirstIssue": str(error),
            "Output": str(output),
            "Boundary": "Not an AI behavior PASS; preserve evidence and stop downstream.",
        }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--suite", type=Path, required=True)
    p.add_argument("--records", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--promptfoo-home")
    p.add_argument("--halo-repo")
    p.add_argument("--judge-model")
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--check", action="store_true")
    result = execute(p.parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(1 if result["Result"] == "TOOL_ERROR" else 0)


if __name__ == "__main__":
    main()
