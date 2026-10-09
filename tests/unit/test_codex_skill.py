"""Guarded Codex skill entry: failures must not trigger model execution."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "skills/codex/halo-loop/scripts/diagnose.py"
spec = importlib.util.spec_from_file_location("halo_codex_skill", SCRIPT)
assert spec and spec.loader
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    runtime = root / "runtime"
    runtime.mkdir()
    (runtime / ".venv").mkdir()
    for name in ["pyproject.toml", "uv.lock", "engine/chatgpt_auth.py"]:
        p = runtime / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("fixture")
    trace = root / "traces.jsonl"
    row = {
        "trace_id": "run1",
        "span_id": "tool1",
        "name": "actual_command",
        "kind": "SPAN_KIND_INTERNAL",
        "start_time": "2026-10-09T00:00:00Z",
        "end_time": "2026-10-09T00:00:01Z",
        "status": {"code": "STATUS_CODE_ERROR"},
        "resource": {"attributes": {}},
        "scope": {"name": "fixture"},
        "attributes": {},
    }
    trace.write_text(json.dumps(row) + "\n")
    prompt = root / "question.txt"
    prompt.write_text("What happened after the tool error? Cite evidence.")
    monkeypatch.setattr(entry.shutil, "which", lambda _: "/fixture/uv")
    args = SimpleNamespace(
        halo_repo=str(runtime),
        model="fixture-model",
        traces=trace,
        prompt_file=prompt,
        output=root / "report.txt",
        repo=root,
        max_turns=3,
        timeout=10,
        check=False,
    )
    return args


@pytest.mark.parametrize(
    "failure",
    [
        "missing-trace",
        "empty-trace",
        "bad-json",
        "duplicate-span",
        "missing-prompt",
        "empty-prompt",
        "existing-output",
        "missing-runtime",
        "missing-repo",
    ],
)
def test_preparation_failure_never_calls_model(setup, failure):
    args = setup
    if failure == "missing-trace":
        args.traces.unlink()
    elif failure == "empty-trace":
        args.traces.write_text("")
    elif failure == "bad-json":
        args.traces.write_text("invalid JSON")
    elif failure == "duplicate-span":
        args.traces.write_text(args.traces.read_text() * 2)
    elif failure == "missing-prompt":
        args.prompt_file.unlink()
    elif failure == "empty-prompt":
        args.prompt_file.write_text(" \n")
    elif failure == "existing-output":
        args.output.write_text("original failure")
    elif failure == "missing-runtime":
        (Path(args.halo_repo) / "engine/chatgpt_auth.py").unlink()
    else:
        args.repo = args.repo / "missing"

    def forbidden(*_, **__):
        pytest.fail("model started after preparation failure")

    result = entry.execute(args, executor=forbidden)
    assert result["Result"] == "TOOL_ERROR"
    assert result["FailureStage"] == "preflight"
    if failure == "existing-output":
        assert args.output.read_text() == "original failure"
    else:
        assert not args.output.exists()


def test_preflight_only_never_calls_model(setup):
    setup.check = True

    def forbidden(*_, **__):
        pytest.fail("check-only must not start model")

    result = entry.execute(setup, executor=forbidden)
    assert result["Result"] == "PREFLIGHT_PASS"
    assert result["Scope"] == "INPUT_STRUCTURE_ONLY"
    assert not setup.output.exists()


def test_chatgpt_routing_and_input_preservation(setup):
    calls = []
    original = (setup.traces.read_bytes(), setup.prompt_file.read_bytes())

    def executor(argv, **kwargs):
        calls.append((argv, kwargs["cwd"]))
        assert argv[:5] == ["uv", "run", "--locked", "--no-sync", "halo"]
        assert argv[argv.index("--auth-mode") + 1] == "chatgpt"
        assert argv[argv.index("--max-depth") + 1] == "0"
        assert "--api-key" not in argv and "--base-url" not in argv
        assert kwargs["stderr"] == subprocess.STDOUT
        kwargs["stdout"].write("diagnostic response final=True\n")
        return SimpleNamespace(returncode=0)

    result = entry.execute(setup, executor=executor)
    assert len(calls) == 1
    assert calls[0][1] == str(Path(setup.halo_repo).resolve())
    assert result["Result"] == "PASS" and result["Scope"] == "HALO_DIAGNOSTIC_ONLY"
    assert (setup.traces.read_bytes(), setup.prompt_file.read_bytes()) == original


@pytest.mark.parametrize("failure", ["nonzero", "unfinished", "timeout"])
def test_analysis_failure_preserves_output_without_retry(setup, failure):
    calls = []

    def executor(argv, **kwargs):
        calls.append(argv)
        kwargs["stdout"].write("first original failure\n")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 10)
        return SimpleNamespace(returncode=2 if failure == "nonzero" else 0)

    result = entry.execute(setup, executor=executor)
    assert result["Result"] == "TOOL_ERROR" and result["FailureStage"] == "analysis"
    assert len(calls) == 1
    assert setup.output.read_text() == "first original failure\n"
