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

from cli.finalize import (
    REVIEW_WARNING,
    analyst_keys_from_selections,
    complete_report_markdown,
    needs_review,
    save_analysis_reports,
)
from cli.models import AnalystType, AssetType
from cli.prompts import (
    detect_asset_type,
    filter_analysts_for_asset_type,
    parse_analysis_date,
    parse_ticker,
)
from cli.selections import depth_from_env, unattended_gaps
from cli.jobs import JobStore, start_background_job
from cli.theme import SHARED_CSS, apply_responsive_narrow, apply_trading_theme
from cli.web_jobs import JobWatchScreen, JobsScreen
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph


def _env_ready(flags: dict) -> list[str]:
    """Env gaps that block Start. Form fields and save/show are handled in the UI."""
    stub = {
        **flags,
        "ticker": flags.get("ticker") or "x",
        "date": flags.get("date") or "x",
        "analysts": flags.get("analysts") or "x",
        # Stub so unattended_gaps does not demand CLI --save/--show; ReportScreen owns save.
        "save": True,
        "show": True,
    }
    return [gap for gap in unattended_gaps(stub) if not gap.startswith("--")]


class SetupScreen(Screen[dict | None]):
    """Collect ticker / date / analysts / depth; require LLM settings from the env."""

    CSS = """
    #setup {
        width: 72;
        max-width: 100%;
        margin: 1 1;
        padding: 1 1;
        background: $surface;
        border: tall $primary;
    }

    #brand {
        color: $success;
        text-style: bold;
        margin-bottom: 0;
        height: 1;
    }

    #subtitle {
        color: $text-muted;
        margin-bottom: 0;
        height: auto;
    }

    .row {
        height: auto;
        margin: 0;
        align: left middle;
    }

    #actions {
        margin-top: 1;
        height: auto;
    }

    Checkbox {
        width: auto;
    }

    .narrow #setup {
        width: 100%;
        margin: 0;
        border: none;
    }

    .narrow .row {
        layout: vertical;
        height: auto;
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
                yield Button("Back to list", variant="default", id="back")
                yield Button("Quit", variant="default", id="quit")
        yield Footer()

    def on_mount(self) -> None:
        apply_responsive_narrow(self)
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

    def on_resize(self, event) -> None:
        apply_responsive_narrow(self, event.size.width)

    @on(Button.Pressed, "#quit")
    def quit_pressed(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#back")
    def back_pressed(self) -> None:
        # Empty dict signals "return to jobs list" without quitting the app.
        self.dismiss({})

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
    """Full post-run report: review warning, all sections, optional save + HTML."""

    CSS = """
    #report-wrap {
        width: 96;
        max-width: 100%;
        margin: 1 1;
        padding: 1 1;
        background: $surface;
        border: tall $success;
    }

    #report-title {
        color: $success;
        text-style: bold;
        margin-bottom: 0;
        height: 1;
    }

    #review-banner {
        color: $warning;
        background: $warning 12%;
        padding: 0 1;
        margin: 0 0 1 0;
        border: tall $warning 40%;
        height: auto;
    }

    #report-body {
        background: $panel;
        padding: 0 1;
        border: solid $primary 50%;
        margin-bottom: 1;
        height: auto;
        max-height: 70%;
    }

    #save-row, #actions {
        height: auto;
        margin-top: 0;
    }

    #save-status {
        color: $text-muted;
        height: auto;
        margin: 0 0 1 0;
    }

    .narrow #report-wrap {
        width: 100%;
        margin: 0;
        border: none;
    }
    """

    def __init__(
        self,
        final_state: dict,
        graph,
        ticker: str,
        *,
        report_path: str | None = None,
        html_path: str | None = None,
    ) -> None:
        super().__init__()
        self.final_state = final_state or {}
        self.graph = graph
        self.ticker = ticker
        self._html_page: Path | None = Path(html_path) if html_path else None
        self._preset_report = report_path
        self._preset_html = html_path

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="report-wrap"):
            yield Label("Analysis complete", id="report-title")
            if needs_review(self.final_state):
                yield Static(REVIEW_WARNING, id="review-banner")
            yield Markdown(complete_report_markdown(self.final_state), id="report-body")
            yield Static("", id="save-status")
            with Horizontal(id="save-row", classes="row"):
                yield Checkbox("Save report to disk", value=True, id="do-save")
                yield Checkbox("Also save HTML page", value=True, id="do-html")
            with Horizontal(id="actions", classes="row"):
                yield Button("Save report", variant="success", id="save")
                yield Button("Download HTML", variant="primary", id="download-html", disabled=True)
                yield Button("Back to list", variant="default", id="back")
        yield Footer()

    def on_mount(self) -> None:
        apply_responsive_narrow(self)
        status = self.query_one("#save-status", Static)
        download = self.query_one("#download-html", Button)
        if self._preset_report:
            lines = [f"[green]Saved on server[/green] {self._preset_report}"]
            if self._preset_html:
                lines.append(f"[dim]HTML report:[/dim] {self._preset_html}")
                download.disabled = False
                self._html_page = Path(self._preset_html)
            status.update("\n".join(lines))
        else:
            default = self.graph.default_report_path(self.ticker)
            status.update(f"[dim]Default save path:[/dim] {default}")

    def on_resize(self, event) -> None:
        apply_responsive_narrow(self, event.size.width)

    @on(Checkbox.Changed, "#do-save")
    def save_toggled(self, event: Checkbox.Changed) -> None:
        html_box = self.query_one("#do-html", Checkbox)
        html_box.disabled = not event.value
        if not event.value:
            html_box.value = False

    @on(Button.Pressed, "#save")
    def save_pressed(self) -> None:
        if not self.query_one("#do-save", Checkbox).value:
            self.query_one("#save-status", Static).update(
                "[yellow]Enable “Save report to disk” first, or use Back to setup.[/yellow]"
            )
            return
        html = self.query_one("#do-html", Checkbox).value
        result = save_analysis_reports(
            self.graph,
            self.final_state,
            self.ticker,
            html=html,
        )
        status = self.query_one("#save-status", Static)
        download = self.query_one("#download-html", Button)
        if not result.ok:
            status.update(f"[red]Error saving report: {result.error}[/red]")
            self._html_page = None
            download.disabled = True
            return
        lines = [
            f"[green]Saved[/green] {result.save_path}",
            f"[dim]Complete report:[/dim] {result.report_file.name}",
        ]
        self._html_page = result.html_page
        if result.html_page is not None:
            lines.append(f"[dim]HTML report:[/dim] {result.html_page.name}")
            download.disabled = False
        else:
            download.disabled = True
        status.update("\n".join(lines))

    @on(Button.Pressed, "#download-html")
    def download_html(self) -> None:
        if self._html_page is None or not self._html_page.is_file():
            self.query_one("#save-status", Static).update(
                "[yellow]Save the report with HTML enabled first.[/yellow]"
            )
            return
        self.app.deliver_text(
            self._html_page,
            save_filename=self._html_page.name,
            mime_type="text/html",
            open_method="download",
        )

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.dismiss(None)


class TradingAgentsWebApp(App[None]):
    """Browser UI: job list ↔ setup ↔ watch background runs ↔ reports."""

    TITLE = "TradingAgents"
    CSS = SHARED_CSS
    BINDINGS = [("q", "quit", "Quit")]

    def on_mount(self) -> None:
        apply_trading_theme(self)
        self.run_worker(self._session_loop)

    async def _open_job_report(self, job_id: str, store: JobStore) -> None:
        job = store.get(job_id)
        final_state = store.read_final_state(job_id)
        config = store.load_config(job_id) or DEFAULT_CONFIG.copy()
        selections = store.load_selections(job_id)
        if job is None or final_state is None or selections is None:
            self.notify("Finished report is not available yet", severity="error")
            return
        graph = TradingAgentsGraph(
            analyst_keys_from_selections(selections),
            config=config,
            debug=False,
            callbacks=[],
        )
        await self.push_screen_wait(
            ReportScreen(
                final_state,
                graph=graph,
                ticker=job.ticker,
                report_path=job.report_path,
                html_path=job.html_path,
            )
        )

    async def _session_loop(self) -> None:
        store = JobStore()
        while True:
            action = await self.push_screen_wait(JobsScreen(store))
            if action is None or action[0] == "quit":
                self.exit()
                return

            kind, job_id = action
            if kind == "open" and job_id:
                job = store.get(job_id)
                if job is None:
                    self.notify(f"Unknown job {job_id}", severity="error")
                    continue
                if job.status == "completed":
                    await self._open_job_report(job_id, store)
                    continue
                # queued / running / failed → watch (failed still shows snapshot/error)
                opened = await self.push_screen_wait(JobWatchScreen(job_id, store))
                if opened:
                    await self._open_job_report(opened, store)
                continue

            if kind != "new":
                continue

            selections = await self.push_screen_wait(SetupScreen())
            if selections is None:
                self.exit()
                return
            if selections == {}:
                continue

            from cli.run import _build_run_config

            config = _build_run_config(selections, checkpoint=None)
            try:
                record = start_background_job(selections, config)
            except OSError as exc:
                self.notify(f"Could not start background job: {exc}", severity="error")
                continue
            self.notify(
                f"Started {record.label} in the background — safe to close the browser.",
                severity="information",
            )
            opened = await self.push_screen_wait(JobWatchScreen(record.job_id, store))
            if opened:
                await self._open_job_report(opened, store)


def main() -> None:
    # Load .env when present so served sessions see the same keys as the CLI.
    env_path = Path.cwd() / ".env"
    if env_path.exists():
        from dotenv import load_dotenv

        load_dotenv(env_path)
    TradingAgentsWebApp().run()


if __name__ == "__main__":
    main()
