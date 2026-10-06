"""Shared post-run finalize helpers (CLI + web)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cli.finalize import (
    REVIEW_WARNING,
    analyst_keys_from_selections,
    complete_report_markdown,
    needs_review,
    save_analysis_reports,
)
from cli.models import AnalystType
from tradingagents.agents.rating import RATING_REVIEW


@pytest.mark.unit
def test_analyst_keys_follow_canonical_order_not_selection_order():
    selections = {
        "analysts": [
            AnalystType.NEWS,
            AnalystType.MARKET,
            AnalystType.FUNDAMENTALS,
            AnalystType.SOCIAL,
        ]
    }
    assert analyst_keys_from_selections(selections) == [
        "market",
        "social",
        "news",
        "fundamentals",
    ]


@pytest.mark.unit
def test_complete_report_markdown_covers_each_team_section():
    state = {
        "market_report": "M has risen.",
        "sentiment_report": "Sentiment mixed.",
        "news_report": "News quiet.",
        "fundamentals_report": "Books solid.",
        "investment_debate_state": {
            "bull_history": "Bull case",
            "bear_history": "Bear case",
        },
        "investment_plan": "Research plan",
        "trader_investment_plan": "Trader plan",
        "risk_debate_state": {
            "aggressive_history": "Agg",
            "conservative_history": "Con",
            "neutral_history": "Neu",
        },
        "final_trade_decision": "Rating: Buy\n\nBuy it.",
    }
    md = complete_report_markdown(state)
    assert "# Complete Analysis Report" in md
    assert "## I. Analyst Team Reports" in md
    assert "### Market Analyst" in md
    assert "## II. Research Team Decision" in md
    assert "## III. Trading Team Plan" in md
    assert "## IV. Risk Management Team Decision" in md
    assert "## V. Portfolio Manager Decision" in md
    assert "Buy it." in md


@pytest.mark.unit
def test_needs_review_matches_rating_helpers():
    assert needs_review({"final_rating": RATING_REVIEW})
    assert not needs_review({"final_rating": "Buy"})
    assert REVIEW_WARNING.startswith("No rating could be read")


@pytest.mark.unit
def test_save_analysis_reports_records_html_path(tmp_path, monkeypatch):
    written = tmp_path / "reports" / "NVDA_stamp"
    report_md = written / "complete_report.md"
    html_file = written / "complete_report.html"

    def _save(final_state, ticker, save_path=None, html=True):
        path = Path(save_path)
        path.mkdir(parents=True, exist_ok=True)
        report_md.write_text("# ok\n", encoding="utf-8")
        if html:
            html_file.write_text("<html></html>", encoding="utf-8")
        return report_md

    graph = SimpleNamespace(
        default_report_path=lambda ticker: written,
        save_reports=_save,
    )
    result = save_analysis_reports(graph, {"final_trade_decision": "x"}, "NVDA", html=True)
    assert result.ok
    assert result.report_file == report_md.resolve()
    assert result.html_page == html_file.resolve()
    assert result.error is None


@pytest.mark.unit
def test_save_analysis_reports_surfaces_errors(tmp_path):
    def _boom(*a, **k):
        raise OSError("disk full")

    graph = SimpleNamespace(
        default_report_path=lambda ticker: tmp_path / "out",
        save_reports=_boom,
    )
    result = save_analysis_reports(graph, {}, "NVDA")
    assert not result.ok
    assert "disk full" in (result.error or "")
