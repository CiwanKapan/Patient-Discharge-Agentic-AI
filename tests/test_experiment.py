"""End to end, offline: scripts/run_experiment.py with every arm, then evaluation.evaluate on the result.

A fake client answers every arm from the answer keys (as OracleLLM does for the
pipeline), reports token usage, and can be told to fail in specific ways.
"""
import csv
import json
import re
import shutil
from pathlib import Path

import pytest

from evaluation import evaluate
from scripts import run_experiment
from src.models.answer_key import AnswerKey
from src.models.case import PatientCase
from src.models.messages import Stage
from src.utils.llm import LLMInfraError
from src.utils.run_config import ConfigMismatch
from src.utils.run_records import build_manifest, case_path, key_path, read_run, write_manifest

from oracle_llm import OracleLLM

ROOT = Path(__file__).resolve().parent.parent
CASES = ["CASE-151", "CASE-152", "CASE-154"]  # hard, easy, medium; 151 has gated tasks
USAGE = {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 500,
         "cache_creation_input_tokens": 0, "stop_reason": "end_turn", "request_id": "req_test"}


def _load(cid):
    case = PatientCase.model_validate_json(case_path(cid).read_text())
    return case, AnswerKey.model_validate_json(key_path(cid).read_text())


class FakeLLM:
    """Answers pipeline stages and the single prompt from the answer key."""

    model = "claude-sonnet-5-5"

    def __init__(self, taxonomy, infra_fail_once=(), bad_first=(), edit_single=None):
        self.taxonomy = taxonomy
        self.infra_fail_once = set(infra_fail_once)  # (step, case_id) that raise LLMInfraError once
        self.bad_first = set(bad_first)  # case_ids whose first single-prompt answer does not parse
        self.edit_single = edit_single
        self.calls = []
        self.batches = 0
        self.last_usage = None
        self._oracles = {}

    def _oracle(self, cid):
        if cid not in self._oracles:
            self._oracles[cid] = OracleLLM(*_load(cid), self.taxonomy)
        return self._oracles[cid]

    def _answer(self, step, user):
        cid = re.search(r'"case_id": "((?:CASE|DEV)-\d+)"', user).group(1)
        self.calls.append((step, cid))
        if (step, cid) in self.infra_fail_once:
            self.infra_fail_once.discard((step, cid))
            raise LLMInfraError(f"{step}: API error 529: overloaded")
        o = self._oracle(cid)
        if isinstance(step, Stage):
            return o.complete(step, "", user, None)
        if cid in self.bad_first and "previous response was rejected" not in user:
            return "{"
        recs = o._rec_generator()["recommendations"]
        for r in recs:
            r["requires_human_approval"] = r.get("proposed_action") is not None
        out = {"clinical_summary": "c", "social_summary": "s", **o._barrier_detector(), **o._task_detector(), "recommendations": recs}
        if self.edit_single:
            self.edit_single(step, out)
        return json.dumps(out)

    def complete(self, stage, system, user, schema):
        self.last_usage = None
        text = self._answer(stage, user)
        self.last_usage = {**USAGE, "batch": False}
        return text

    def params(self, system, user, schema):
        return {"system": system, "user": user}

    def run_batch(self, requests, **kw):
        self.batches += 1
        out = {}
        for cid, p in requests.items():
            step = "baseline_2_single_llm"
            try:
                out[cid] = {"text": self._answer(step, p["user"]), "usage": {**USAGE, "batch": True}, "error": None}
            except LLMInfraError as e:
                out[cid] = {"text": None, "usage": None, "error": e}
        return out


def _cases_file(tmp_path, ids=CASES):
    f = tmp_path / "cases.txt"
    f.write_text("# test\n" + "\n".join(ids) + "\n")
    return f


def _run(tmp_path, llm, *extra, name="exp", runs="1-2", ids=CASES):
    argv = ["--name", name, "--runs-dir", str(tmp_path), "--runs", runs, "--allow-dirty",
            "--cases-file", str(_cases_file(tmp_path, ids)), *extra]
    return run_experiment.main(argv, llm=llm)


def _eval(tmp_path, name="exp"):
    code = evaluate.main(["--experiment", str(tmp_path / name)])
    out = tmp_path / name / "eval"
    read = lambda f: list(csv.DictReader((out / f).open())) if (out / f).exists() else []  # noqa: E731
    return code, read("per_case.csv"), read("summary.csv"), read("significance.csv"), read("variability.csv")


@pytest.fixture
def experiment(tmp_path, taxonomy):
    llm = FakeLLM(taxonomy)
    assert _run(tmp_path, llm) == 0
    return tmp_path, llm


# ---------------------------------------------------------------- running
def test_layout_and_run_counts(experiment):
    root, llm = experiment
    exp = root / "exp"
    assert sorted(p.name for p in (exp / "b1").iterdir()) == ["r1"]  # B1 runs once
    for arm in ("b2", "agentic"):
        assert sorted(p.name for p in (exp / arm).iterdir()) == ["r1", "r2"]
    assert not (exp / "b3").exists()
    for arm in ("b1", "b2", "agentic"):
        m = json.loads((exp / arm / "r1" / "manifest.json").read_text())
        assert m["arm"] == arm and m["run"] == 1 and m["taxonomy_sha256"]
        assert (m["llm_settings"] is None) == (arm == "b1")
    # pipeline: 4 calls per case per run; B2 went through the Batch API, one batch per run
    assert sum(isinstance(s, Stage) for s, _ in llm.calls) == 4 * 3 * 2
    assert llm.batches == 2


def test_every_case_has_a_run_record_with_calls_and_settings(experiment):
    root, _ = experiment
    for arm, n_calls in (("b2", 1), ("agentic", 4)):
        r = read_run(root / "exp" / arm / "r1", "CASE-151")
        assert r["status"] == "succeeded" and len(r["llm_calls"]) == n_calls
        assert r["llm_settings"]["effort"] == "medium"
        assert all(c["outcome"] == "accepted" and c["input_tokens"] == 1000 for c in r["llm_calls"])
        assert all(c["batch"] == (arm != "agentic") for c in r["llm_calls"])
    agentic = read_run(root / "exp" / "agentic" / "r1", "CASE-151")
    assert agentic["audit_path"] and agentic["audit_errors"] == []
    assert (root / "exp" / "agentic" / "r1" / "CASE-151.signoff.json").exists()


def test_rerun_resumes_without_calling_again(experiment, taxonomy):
    root, _ = experiment
    again = FakeLLM(taxonomy)
    assert _run(root, again) == 0
    assert again.calls == [] and again.batches == 0


def test_infrastructure_failures_are_retried_on_resume(tmp_path, taxonomy):
    llm = FakeLLM(taxonomy, infra_fail_once={(Stage.CASE_ANALYZER, "CASE-152")})
    assert _run(tmp_path, llm, "--arms", "agentic", runs="1") == 1
    r = read_run(tmp_path / "exp" / "agentic" / "r1", "CASE-152")
    assert r["status"] == "failed" and r["error_kind"] == "infrastructure"
    assert [c["outcome"] for c in r["llm_calls"]] == ["llm_error"]
    again = FakeLLM(taxonomy)
    assert _run(tmp_path, again, "--arms", "agentic", runs="1") == 0
    assert {cid for _, cid in again.calls} == {"CASE-152"}  # only the failed case reran
    assert read_run(tmp_path / "exp" / "agentic" / "r1", "CASE-152")["status"] == "succeeded"


def test_format_retry_in_the_batch_path(tmp_path, taxonomy):
    llm = FakeLLM(taxonomy, bad_first={"CASE-154"})
    assert _run(tmp_path, llm, "--arms", "b2", runs="1") == 0
    r = read_run(tmp_path / "exp" / "b2" / "r1", "CASE-154")
    assert [c["outcome"] for c in r["llm_calls"]] == ["format_error", "accepted"]
    assert llm.batches == 2  # one first-attempt batch and one retry batch


def test_refuses_a_model_override(tmp_path, taxonomy, monkeypatch):
    monkeypatch.setenv("CLAUDE_MODEL", "claude-opus-5-5")
    with pytest.raises(ConfigMismatch):
        _run(tmp_path, FakeLLM(taxonomy), runs="1")


def test_refuses_to_mix_runs_made_differently(experiment):
    root, _ = experiment
    out = root / "exp" / "b2" / "r1"
    changed = build_manifest("b2", {"model": "claude-sonnet-5-5", "effort": "high", "max_tokens": 16000,
                                    "max_attempts": 2}, "v1")
    with pytest.raises(RuntimeError, match="different"):
        write_manifest(out, changed)


def test_dry_run_calls_and_writes_nothing(tmp_path, taxonomy, capsys):
    llm = FakeLLM(taxonomy)
    assert _run(tmp_path, llm, "--dry-run", runs="1-3") == 0
    assert llm.calls == [] and not (tmp_path / "exp").exists()
    assert "model calls: at least" in capsys.readouterr().out


# ---------------------------------------------------------------- evaluation
def test_evaluation_of_the_experiment(experiment):
    root, _ = experiment
    code, rows, summary, sig, var = _eval(root)
    assert code == 0
    assert len(rows) == 3 * (1 + 2 + 2)  # B1 once, the LLM arms twice
    assert {r["run"] for r in rows if r["system"] == "B1"} == {"r1"}
    allrows = {s["system"]: s for s in summary if s["slice"] == "all"}
    assert allrows["Agentic"]["llm_calls"] == "24" and allrows["B2"]["llm_calls"] == "6"
    assert allrows["Agentic"]["validation_retries"] == "0"
    # cost: batch calls (B2) at half price
    per_call = (1000 * 2.0 + 200 * 10.0 + 500 * 0.2) / 1e6
    assert float(allrows["Agentic"]["cost_usd"]) == pytest.approx(24 * per_call, abs=1e-6)
    assert float(allrows["B2"]["cost_usd"]) == pytest.approx(6 * per_call * 0.5, abs=1e-6)
    families = {(r["baseline"], r["hypothesis"], r["family"]) for r in sig}
    assert {(b, f) for b, h, f in families if h == "H4a"} == {("B1", "primary"), ("B2", "primary")}
    assert {(b, f) for b, h, f in families if h == "H4-composite"} == {("B1", "secondary"), ("B2", "secondary")}
    assert all("mean over its runs" in r["runs_used"] for r in sig)
    metrics = {(v["system"], v["metric"]) for v in var}
    assert ("Agentic", "barrier_recall") in metrics and ("B1", "barrier_recall") not in metrics
    same = next(v for v in var if v["system"] == "B2" and v["metric"] == "identical_barrier_sets_across_runs")
    assert same["mean"] == "1.0"


def test_reduced_design_scores_each_run_on_its_own_cases(tmp_path, taxonomy):
    llm = FakeLLM(taxonomy)
    assert _run(tmp_path, llm, "--arms", "b2,agentic", runs="1") == 0
    assert _run(tmp_path, llm, "--arms", "b2,agentic", runs="2-3", ids=CASES[:2]) == 0
    code, rows, _, sig, var = _eval(tmp_path)
    assert code == 0
    assert not any(r["status"] == "missing" for r in rows)  # r2/r3 never scored outside the subset
    assert sum(r["system"] == "Agentic" for r in rows) == 3 + 2 * 2
    assert all("run r1 only" in r["runs_used"] for r in sig)
    assert next(v for v in var if v["system"] == "Agentic" and v["metric"] == "barrier_recall")["cases"] == "2"


def test_evaluation_refuses_mixed_settings(experiment):
    root, _ = experiment
    rec_path = root / "exp" / "b2" / "r2" / "CASE-152.run.json"
    rec = json.loads(rec_path.read_text())
    rec["llm_settings"]["effort"] = "high"
    rec_path.write_text(json.dumps(rec))
    code, *_ = _eval(root)
    assert code == 2


# ---------------------------------------------------------------- development set
def test_dev_case_set_runs_and_scores(tmp_path, taxonomy):
    llm = FakeLLM(taxonomy)
    argv = ["--name", "pilot", "--runs-dir", str(tmp_path), "--runs", "1", "--allow-dirty", "--case-set", "dev",
            "--arms", "b1,b2,agentic"]
    assert run_experiment.main(argv, llm=llm) == 0
    assert {cid for _, cid in llm.calls} == {f"DEV-{i:03d}" for i in range(1, 16)}
    assert json.loads((tmp_path / "pilot" / "b2" / "r1" / "manifest.json").read_text())["case_set"] == "dev"
    code, rows, summary, *_ = _eval(tmp_path, name="pilot")
    assert code == 0 and len(rows) == 15 * 3
    assert all(r["case_id"].startswith("DEV-") and r["status"] == "ok" for r in rows)


def test_cases_file_must_match_the_case_set(tmp_path, taxonomy):
    with pytest.raises(SystemExit, match="outside --case-set dev"):
        _run(tmp_path, FakeLLM(taxonomy), "--case-set", "dev", runs="1")  # cases file lists CASE-### ids


def test_evaluation_combines_experiments_later_arms_win(tmp_path, taxonomy, capsys):
    llm = FakeLLM(taxonomy)
    assert _run(tmp_path, llm, "--arms", "b1,b2,agentic", runs="1", name="first") == 0
    assert _run(tmp_path, llm, "--arms", "b2", runs="1-2", name="rerun") == 0
    out = tmp_path / "combined"
    assert evaluate.main(["--experiment", str(tmp_path / "first"), "--experiment", str(tmp_path / "rerun"),
                          "--out", str(out)]) == 0
    rows = list(csv.DictReader((out / "per_case.csv").open()))
    assert {r["run"] for r in rows if r["system"] == "B2"} == {"r1", "r2"}   # from the re-run
    assert {r["system"] for r in rows} == {"B1", "B2", "Agentic"}
    assert "B2 from" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        evaluate.main(["--experiment", str(tmp_path / "first"), "--experiment", str(tmp_path / "rerun")])


def test_a_moved_run_keeps_its_audit_trail(experiment, tmp_path):
    """Run records hold absolute audit paths; scoring a run moved to another machine still finds its logs."""
    root, _ = experiment
    moved = tmp_path / "moved"
    shutil.copytree(root / "exp", moved)
    record = moved / "agentic" / "r1" / "CASE-151.run.json"
    data = json.loads(record.read_text())
    data["audit_path"] = "/elsewhere/runs/exp/agentic/r1/audit/CASE-151/gone.jsonl"
    record.write_text(json.dumps(data))
    rd = next(r for r in evaluate.discover(moved)["Agentic"] if r.run == "r1")
    *_, audit_present = evaluate.load_plan(rd, "CASE-151")
    assert audit_present
