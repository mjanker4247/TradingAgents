"""Post-run finalize helpers shared by the CLI and the web UI.

Keeps report assembly, review detection, and save behaviour in one place so the
browser path cannot drift from ``tradingagents`` after a run.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cli.display import ANALYST_ORDER
from tradingagents.agents.rating import is_review, run_rating

REVIEW_WARNING = (
    "No rating could be read from the final decision, so this run is recorded "
    "for review rather than as a position. Re-run, or read the decision text "
    "below and judge it yourself."
)


def analyst_keys_from_selections(selections: dict) -> list[str]:
    """Analyst keys in canonical graph order (same as the CLI)."""
    selected = {analyst.value for analyst in selections["analysts"]}
    return [key for key in ANALYST_ORDER if key in selected]


def needs_review(final_state: dict | None) -> bool:
    """True when the portfolio manager left no usable rating."""
    return is_review(run_rating(final_state or {}))


def complete_report_markdown(final_state: dict | None) -> str:
    """Full multi-section report as Markdown (web / headless equivalent of the CLI panels)."""
    state = final_state or {}
    parts: list[str] = ["# Complete Analysis Report", ""]

    analysts: list[tuple[str, str]] = []
    if state.get("market_report"):
        analysts.append(("Market Analyst", state["market_report"]))
    if state.get("sentiment_report"):
        analysts.append(("Sentiment Analyst", state["sentiment_report"]))
    if state.get("news_report"):
        analysts.append(("News Analyst", state["news_report"]))
    if state.get("fundamentals_report"):
        analysts.append(("Fundamentals Analyst", state["fundamentals_report"]))
    if analysts:
        parts.append("## I. Analyst Team Reports")
        parts.append("")
        for title, content in analysts:
            parts.append(f"### {title}")
            parts.append("")
            parts.append(str(content).strip())
            parts.append("")

    if state.get("investment_debate_state"):
        debate = state["investment_debate_state"]
        research: list[tuple[str, str]] = []
        if debate.get("bull_history"):
            research.append(("Bull Researcher", debate["bull_history"]))
        if debate.get("bear_history"):
            research.append(("Bear Researcher", debate["bear_history"]))
        if state.get("investment_plan"):
            research.append(("Research Manager", state["investment_plan"]))
        if research:
            parts.append("## II. Research Team Decision")
            parts.append("")
            for title, content in research:
                parts.append(f"### {title}")
                parts.append("")
                parts.append(str(content).strip())
                parts.append("")

    if state.get("trader_investment_plan"):
        parts.append("## III. Trading Team Plan")
        parts.append("")
        parts.append("### Trader")
        parts.append("")
        parts.append(str(state["trader_investment_plan"]).strip())
        parts.append("")

    if state.get("risk_debate_state"):
        risk = state["risk_debate_state"]
        risk_reports: list[tuple[str, str]] = []
        if risk.get("aggressive_history"):
            risk_reports.append(("Aggressive Analyst", risk["aggressive_history"]))
        if risk.get("conservative_history"):
            risk_reports.append(("Conservative Analyst", risk["conservative_history"]))
        if risk.get("neutral_history"):
            risk_reports.append(("Neutral Analyst", risk["neutral_history"]))
        if risk_reports:
            parts.append("## IV. Risk Management Team Decision")
            parts.append("")
            for title, content in risk_reports:
                parts.append(f"### {title}")
                parts.append("")
                parts.append(str(content).strip())
                parts.append("")

    if state.get("final_trade_decision"):
        parts.append("## V. Portfolio Manager Decision")
        parts.append("")
        parts.append(str(state["final_trade_decision"]).strip())
        parts.append("")

    if len(parts) <= 2:
        parts.append("_No report sections were produced for this run._")
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


@dataclass(frozen=True)
class SaveResult:
    """Outcome of writing the on-disk report tree."""

    save_path: Path
    report_file: Path | None
    html_page: Path | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.report_file is not None


def save_analysis_reports(
    graph: Any,
    final_state: dict,
    ticker: str,
    *,
    save_path: Path | None = None,
    html: bool = True,
) -> SaveResult:
    """Write markdown (+ optional HTML) under ``results_dir``, like the CLI."""
    path = Path(save_path) if save_path is not None else graph.default_report_path(ticker)
    try:
        report_file = graph.save_reports(final_state, ticker, path, html=html)
    except Exception as exc:  # noqa: BLE001 - surface to whichever UI called us
        return SaveResult(save_path=path, report_file=None, html_page=None, error=str(exc))
    html_page = (path / "complete_report.html").resolve() if html else None
    if html_page is not None and not html_page.is_file():
        html_page = None
    return SaveResult(
        save_path=path.resolve(),
        report_file=Path(report_file).resolve(),
        html_page=html_page,
        error=None,
    )
