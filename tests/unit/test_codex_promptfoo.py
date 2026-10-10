"""Eval boundary checks; a parser pass is not an agent behavior pass."""

import asyncio
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    path = ROOT / "skills/codex/halo-loop/scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


entry = load("evaluate")
provider = load("promptfoo_provider")


@pytest.fixture
def setup(tmp_path):
    runtime = tmp_path / "runtime/node_modules/promptfoo"
    (runtime / "dist/src").mkdir(parents=True)
    (runtime / "dist/src/entrypoint.js").write_text("fixture")
    (runtime / "package.json").write_text(json.dumps({"version": entry.VERSION}))
    suite = tmp_path / "suite.json"
    suite.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "C1",
                        "task": "Report state",
                        "assertions": [{"type": "equals", "value": "observed"}],
                    }
                ]
            }
        )
    )
    records = tmp_path / "records.json"
    records.write_text(
        json.dumps(
            [
                {
                    "id": variant,
                    "case_id": "C1",
                    "variant": variant,
                    "sample": "1",
                    "model": "fixed-model",
                    "input_id": "same-task-material",
                    "status": "COMPLETED",
                    "output": "observed" if variant == "after" else "unobserved",
                    "evidence": [],
                    "seconds": 2 if variant == "after" else 1,
                }
                for variant in ("before", "after")
            ]
        )
    )
    return SimpleNamespace(
        suite=suite,
        records=records,
        output_dir=tmp_path / "eval",
        promptfoo_home=str(tmp_path / "runtime"),
        halo_repo=None,
        judge_model=None,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize(
    "failure",
    [
        "incomplete",
        "unpaired",
        "different-model",
        "duplicate-run",
        "no-criteria",
        "existing-output",
        "bad-version",
    ],
)
def test_invalid_inputs_do_not_execute(setup, failure):
    args = setup
    records = entry.read(args.records)
    if failure == "incomplete":
        records[0]["status"] = "NOT_RUN"
    elif failure == "unpaired":
        records.pop()
    elif failure == "different-model":
        records[1]["model"] = "changed-model"
    elif failure == "duplicate-run":
        records[1]["id"] = records[0]["id"]
    elif failure == "no-criteria":
        args.suite.write_text(json.dumps({"cases": [{"id": "C1", "task": "Report state"}]}))
    elif failure == "existing-output":
        args.output_dir.mkdir()
        (args.output_dir / "old-error.txt").write_text("first failure")
    else:
        (Path(args.promptfoo_home) / "node_modules/promptfoo/package.json").write_text(
            '{"version":"wrong"}'
        )
    args.records.write_text(json.dumps(records))
    calls = []
    result = entry.execute(args, executor=lambda *a, **k: calls.append(a))
    assert result["Result"] == "TOOL_ERROR"
    assert calls == []
    if failure == "existing-output":
        assert (args.output_dir / "old-error.txt").read_text() == "first failure"
    else:
        assert not args.output_dir.exists()


def test_check_only_and_explicit_chatgpt_grader(setup, tmp_path):
    args = setup
    args.halo_repo = str(tmp_path / "HALO")
    python = Path(args.halo_repo) / (
        ".venv/Scripts/python.exe" if entry.os.name == "nt" else ".venv/bin/python"
    )
    python.parent.mkdir(parents=True)
    python.write_text("fixture")
    args.judge_model = "configured-judge"
    suite = entry.read(args.suite)
    suite["cases"][0]["rubric"] = "All conclusions need event citations."
    args.suite.write_text(json.dumps(suite))
    args.check = True
    result = entry.execute(args, executor=lambda *a, **k: pytest.fail("check cannot execute"))
    assert result["Result"] == "PREFLIGHT_PASS"
    assert not args.output_dir.exists()
    config = entry.prepare(args)["config"]
    for test in config["tests"]:
        grader = test["assert"][-1]["provider"]
        assert grader["config"]["mode"] == "chatgpt"
        assert grader["config"]["model"] == "configured-judge"
        assert "openai:" not in grader["id"]


def test_assertion_failure_is_completed_eval_and_preserves_costs(setup, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-should-not-inherit")
    hashes = entry.digest(setup.records), entry.digest(setup.suite)

    def executor(argv, **kwargs):
        assert "--no-cache" in argv and argv[-2:] == ["--max-concurrency", "1"]
        assert "OPENAI_API_KEY" not in kwargs["env"]
        tests = entry.read(setup.output_dir / "config.json")["tests"]
        rows = [
            {
                "testCase": t,
                "success": t["metadata"]["variant"] == "after",
                "score": int(t["metadata"]["variant"] == "after"),
                "gradingResult": {"reason": "fixture assertion"},
            }
            for t in tests
        ]
        (setup.output_dir / "results.json").write_text(json.dumps({"results": {"results": rows}}))
        return SimpleNamespace(returncode=100)

    result = entry.execute(setup, executor=executor)
    assert result["Result"] == "EVAL_COMPLETE"
    assert result["variants"]["before"]["passed"] == 0
    assert result["variants"]["after"]["passed"] == 1
    assert result["variants"]["after"]["agent_seconds"] == 2
    assert result["variants"]["after"]["agent_usage"]["input_tokens"] is None
    assert hashes == (entry.digest(setup.records), entry.digest(setup.suite))


@pytest.mark.parametrize("failure", ["exit", "timeout", "judge"])
def test_execution_errors_preserve_first_log_no_retry(setup, failure):
    calls = []

    def executor(argv, **kwargs):
        calls.append(argv)
        kwargs["stdout"].write("first error")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 10)
        if failure == "judge":
            (setup.output_dir / "judge-error.json").write_text('{"error_type":"quota"}')
            (setup.output_dir / "results.json").write_text("{}")
        return SimpleNamespace(returncode=1 if failure == "exit" else 100)

    assert entry.execute(setup, executor=executor)["Result"] == "TOOL_ERROR"
    assert len(calls) == 1
    assert (setup.output_dir / "execution.log").read_text() == "first error"


def test_recorded_provider_returns_actual_answer_not_a_judgment(setup):
    result = asyncio.run(
        provider.call_api(
            "ignored",
            {"config": {"mode": "recorded", "records": str(setup.records)}},
            {"vars": {"record_id": "before"}},
        )
    )
    assert result["output"] == "unobserved"
    assert result["metadata"]["agent_seconds"] == 1
    assert result["metadata"]["mode"] == "RECORDED_OUTPUT_REPLAY"


def test_prior_judge_error_prevents_any_model_call(tmp_path):
    (tmp_path / "judge-error.json").write_text('{"error_type":"quota"}')
    result = asyncio.run(
        provider.call_api(
            "ignored", {"config": {"mode": "chatgpt", "report_dir": str(tmp_path)}}, {}
        )
    )
    assert "error" in result
    assert list(tmp_path.iterdir()) == [tmp_path / "judge-error.json"]
