"""Browser-served Textual app: setup form, then live analysis.

Launched by ``tradingagents serve`` via textual-serve. LLM provider/model settings
come from TRADINGAGENTS_* environment variables (same unattended contract as a
non-interactive CLI run).
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    Markdown,
    Select,
    Static,
)

from cli.models import AnalystType, AssetType
from cli.prompts import (
    detect_asset_type,
    filter_analysts_for_asset_type,
    parse_analysis_date,
    parse_ticker,
)
from cli.selections import depth_from_env, unattended_gaps
from cli.stats_handler import StatsCallbackHandler
from cli.theme import SHARED_CSS, apply_trading_theme
from cli.tui import AnalysisScreen
from tradingagents.default_config import DEFAULT_CONFIG


def _env_ready(flags: dict) -> list[str]:
    """Gaps that still block a web run (save/show are answered by the web UI)."""
    web_flags = {**flags, "save": True, "show": False}
    return [
        gap
        for gap in unattended_gaps(web_flags)
        if not gap.startswith("--save") and not gap.startswith("--show")
    ]


class SetupScreen(Screen[dict | None]):
    """Collect ticker / date / analysts / depth; require LLM settings from the env."""

    CSS = """
    #setup {
        width: 88;
        max-width: 100%;
        margin: 2 2;
        padding: 1 2;
        background: $surface;
        border: tall $primary;
    }

    #brand {
        color: $success;
        text-style: bold;
        margin-bottom: 0;
    }

    #subtitle {
        color: $text-muted;
        margin-bottom: 1;
    }

    .row {
        height: auto;
        margin: 1 0;
        align: left middle;
    }

    #actions {
        margin-top: 1;
        height: auto;
    }

    Checkbox {
        width: auto;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="setup"):
            yield Label("TradingAgents", id="brand")
            yield Static(
                "Multi-agent LLM research — configure a run, then watch it live.",
                id="subtitle",
            )
            yield Static("", id="gaps")
            yield Label("Ticker", classes="field-label")
            yield Static(
                "[dim]Exchange suffix when needed — SPY, 0700.HK, BTC-USD[/dim]"
            )
            yield Input(
                value=os.environ.get("TRADINGAGENTS_TICKER", "SPY"),
                id="ticker",
                placeholder="SPY",
            )
            yield Label("Analysis date", classes="field-label")
            yield Static("[dim]YYYY-MM-DD[/dim]")
            yield Input(
                value=datetime.datetime.now().strftime("%Y-%m-%d"),
                id="date",
                placeholder="YYYY-MM-DD",
            )
            yield Label("Analysts", classes="section-title")
            with Horizontal(classes="row"):
                yield Checkbox("Market", True, id="analyst-market")
                yield Checkbox("Sentiment", True, id="analyst-social")
                yield Checkbox("News", True, id="analyst-news")
                yield Checkbox("Fundamentals", True, id="analyst-fundamentals")
            yield Label("Research depth", classes="section-title")
            yield Static("[dim]Debate / risk rounds[/dim]")
            yield Select(
                (
                    ("Shallow (1)", 1),
                    ("Medium (3)", 3),
                    ("Deep (5)", 5),
                ),
                value=3,
                id="depth",
                allow_blank=False,
            )
            yield Label("Environment", classes="section-title")
            yield Static(id="env-summary")
            with Horizontal(id="actions", classes="row"):
                yield Button("Start analysis", variant="success", id="start")
                yield Button("Quit", variant="default", id="quit")
        yield Footer()

    def on_mount(self) -> None:
        gaps = _env_ready({"ticker": "x", "date": "x", "analysts": "x"})
        gap_widget = self.query_one("#gaps", Static)
        if gaps:
            gap_widget.update(
                "[b]Set these before starting[/b]\n" + "\n".join(f"• {g}" for g in gaps)
            )
            gap_widget.set_classes("status-bad")
        else:
            gap_widget.update("[b]Ready[/b] — LLM environment looks good.")
            gap_widget.set_classes("status-ok")

        provider = DEFAULT_CONFIG.get("llm_provider", "?")
        quick = DEFAULT_CONFIG.get("quick_think_llm", "?")
        deep = DEFAULT_CONFIG.get("deep_think_llm", "?")
        lang = DEFAULT_CONFIG.get("output_language", "English")
        self.query_one("#env-summary", Static).update(
            f"[b]Provider[/b]  {provider}\n"
            f"[b]Quick[/b]     {quick}\n"
            f"[b]Deep[/b]      {deep}\n"
            f"[b]Language[/b]  {lang}"
        )

        if depth_from_env():
            depth = DEFAULT_CONFIG["max_debate_rounds"]
            select = self.query_one("#depth", Select)
            options = {1, 3, 5}
            select.value = depth if depth in options else 3

    @on(Button.Pressed, "#quit")
    def quit_pressed(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#start")
    def start_pressed(self) -> None:
        gaps = _env_ready({"ticker": "x", "date": "x", "analysts": "x"})
        if gaps:
            gap_widget = self.query_one("#gaps", Static)
            gap_widget.update(
                "[b]Still missing[/b]\n" + "\n".join(f"• {g}" for g in gaps)
            )
            gap_widget.set_classes("status-bad")
            return

        try:
            ticker = parse_ticker(self.query_one("#ticker", Input).value.strip())
            analysis_date = parse_analysis_date(
                self.query_one("#date", Input).value.strip()
            )
        except ValueError as exc:
            gap_widget = self.query_one("#gaps", Static)
            gap_widget.update(f"[b]Invalid input[/b]\n{exc}")
            gap_widget.set_classes("status-bad")
            return

        asset_type = detect_asset_type(ticker)
        selected: list[AnalystType] = []
        mapping = {
            "analyst-market": AnalystType.MARKET,
            "analyst-social": AnalystType.SOCIAL,
            "analyst-news": AnalystType.NEWS,
            "analyst-fundamentals": AnalystType.FUNDAMENTALS,
        }
        for widget_id, analyst in mapping.items():
            if self.query_one(f"#{widget_id}", Checkbox).value:
                selected.append(analyst)

        allowed = filter_analysts_for_asset_type(selected, asset_type)
        if not allowed:
            gap_widget = self.query_one("#gaps", Static)
            gap_widget.update(
                "[b]No analysts selected[/b]\n"
                "Pick at least one analyst valid for this asset type."
            )
            gap_widget.set_classes("status-bad")
            return

        if depth_from_env():
            research_depth = DEFAULT_CONFIG["max_debate_rounds"]
        else:
            research_depth = int(self.query_one("#depth", Select).value)

        self.dismiss(
            {
                "ticker": ticker,
                "asset_type": asset_type.value
                if isinstance(asset_type, AssetType)
                else asset_type,
                "analysis_date": analysis_date,
                "analysts": allowed,
                "research_depth": research_depth,
                "llm_provider": DEFAULT_CONFIG["llm_provider"].lower(),
                "backend_url": DEFAULT_CONFIG["backend_url"],
                "quick_think_llm": DEFAULT_CONFIG["quick_think_llm"],
                "deep_think_llm": DEFAULT_CONFIG["deep_think_llm"],
                "google_thinking_level": DEFAULT_CONFIG.get("google_thinking_level"),
                "openai_reasoning_effort": DEFAULT_CONFIG.get("openai_reasoning_effort"),
                "anthropic_effort": DEFAULT_CONFIG.get("anthropic_effort"),
                "output_language": DEFAULT_CONFIG.get("output_language", "English"),
            }
        )


class ReportScreen(Screen[None]):
    """Show the final decision after a web run."""

    CSS = """
    #report-wrap {
        width: 96;
        max-width: 100%;
        margin: 2 2;
        padding: 1 2;
        background: $surface;
        border: tall $success;
    }

    #report-title {
        color: $success;
        text-style: bold;
        margin-bottom: 1;
    }

    #decision {
        background: $panel;
        padding: 1 1;
        border: solid $primary 50%;
        margin-bottom: 1;
    }

    #back {
        margin-top: 1;
    }
    """

    def __init__(self, final_state: dict) -> None:
        super().__init__()
        self.final_state = final_state or {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="report-wrap"):
            yield Label("Analysis complete", id="report-title")
            decision = (
                self.final_state.get("final_trade_decision") or "_No decision text._"
            )
            yield Markdown(
                f"## Portfolio Manager Decision\n\n{decision}",
                id="decision",
            )
            yield Button("Back to setup", id="back", variant="primary")
        yield Footer()

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.dismiss(None)


class TradingAgentsWebApp(App[None]):
    """Entry app served in the browser: setup → analysis → report."""

    TITLE = "TradingAgents"
    CSS = SHARED_CSS
    BINDINGS = [("q", "quit", "Quit")]

    def on_mount(self) -> None:
        apply_trading_theme(self)
        self.run_worker(self._session_loop)

    async def _session_loop(self) -> None:
        while True:
            selections = await self.push_screen_wait(SetupScreen())
            if selections is None:
                self.exit()
                return

            from cli.run import _build_run_config

            config = _build_run_config(selections, checkpoint=None)
            selected_keys = [a.value for a in selections["analysts"]]

            from tradingagents.graph.trading_graph import TradingAgentsGraph

            stats_handler = StatsCallbackHandler()
            graph = TradingAgentsGraph(
                selected_keys,
                config=config,
                debug=True,
                callbacks=[stats_handler],
            )

            final_state = await self.push_screen_wait(
                AnalysisScreen(
                    selections=selections,
                    config=config,
                    graph=graph,
                    stats_handler=stats_handler,
                )
            )
            await self.push_screen_wait(ReportScreen(final_state or {}))


def main() -> None:
    # Load .env when present so served sessions see the same keys as the CLI.
    env_path = Path.cwd() / ".env"
    if env_path.exists():
        from dotenv import load_dotenv

        load_dotenv(env_path)
    TradingAgentsWebApp().run()


if __name__ == "__main__":
    main()
