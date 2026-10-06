"""Background job store and worker spawn."""

from __future__ import annotations

from pathlib import Path

import pytest

from cli.jobs import JobStore, serialize_selections, deserialize_selections
from cli.models import AnalystType
from cli.job_worker import run_job


@pytest.mark.unit
def test_selections_round_trip_preserves_analyst_types():
    raw = {
        "ticker": "NVDA",
        "analysis_date": "2026-01-10",
        "asset_type": "stock",
        "analysts": [AnalystType.NEWS, AnalystType.MARKET],
        "research_depth": 1,
        "llm_provider": "openai",
        "backend_url": "https://api.openai.com/v1",
        "quick_think_llm": "q",
        "deep_think_llm": "d",
        "output_language": "English",
    }
    restored = deserialize_selections(serialize_selections(raw))
    assert restored["analysts"] == [AnalystType.NEWS, AnalystType.MARKET]


@pytest.mark.unit
def test_job_store_create_list_and_snapshot(tmp_path, monkeypatch):
    cfg = {
        "data_cache_dir": str(tmp_path / "cache"),
        "results_dir": str(tmp_path / "results"),
    }
    store = JobStore(cfg)
    selections = {
        "ticker": "AAPL",
        "analysis_date": "2026-03-01",
        "asset_type": "stock",
        "analysts": [AnalystType.MARKET],
        "research_depth": 1,
        "llm_provider": "openai",
        "backend_url": "https://api.openai.com/v1",
        "quick_think_llm": "q",
        "deep_think_llm": "d",
        "output_language": "English",
    }
    job = store.create(selections, cfg)
    assert job.status == "queued"
    assert job.ticker == "AAPL"
    assert (store.job_dir(job.job_id) / "meta.json").is_file()

    store.write_snapshot(job.job_id, {"agent_status": {"Trader": "in_progress"}, "messages": []})
    snap = store.read_snapshot(job.job_id)
    assert snap["agent_status"]["Trader"] == "in_progress"

    store.write_final_state(job.job_id, {"final_trade_decision": "Rating: Buy"})
    store.update(
        job.job_id,
        status="completed",
        report_path=str(tmp_path / "out"),
        html_path=str(tmp_path / "out.html"),
    )
    listed = store.list_jobs()
    assert listed[0].job_id == job.job_id
    assert listed[0].status == "completed"
    assert store.read_final_state(job.job_id)["final_trade_decision"] == "Rating: Buy"


@pytest.mark.unit
def test_reconcile_marks_dead_pid_as_failed(tmp_path):
    cfg = {"data_cache_dir": str(tmp_path / "cache"), "results_dir": str(tmp_path / "r")}
    store = JobStore(cfg)
    job = store.create(
        {
            "ticker": "X",
            "analysis_date": "2026-01-01",
            "asset_type": "stock",
            "analysts": [AnalystType.MARKET],
            "research_depth": 1,
            "llm_provider": "openai",
            "backend_url": "u",
            "quick_think_llm": "q",
            "deep_think_llm": "d",
            "output_language": "English",
        },
        cfg,
    )
    store.update(job.job_id, status="running", pid=999_999_999)
    refreshed = store.get(job.job_id)
    assert refreshed is not None
    assert refreshed.status == "failed"


@pytest.mark.unit
def test_run_job_unknown_id_returns_2(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("TRADINGAGENTS_RESULTS_DIR", str(tmp_path / "results"))
    assert run_job("does-not-exist") == 2
