"""Textual live view for a TradingAgents analysis run."""

from __future__ import annotations

import time
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Markdown, Static

from cli.analysis_driver import drive_analysis
from cli.theme import SHARED_CSS, apply_trading_theme
from cli.view_model import (
    current_report_markdown,
    footer_stats,
    progress_rows,
    recent_message_rows,
)

ANALYSIS_CSS = """
#header {
    height: 5;
    padding: 1 2;
    background: $primary;
    color: $foreground;
    border-bottom: tall $accent;
}

#header .subtitle {
    color: $accent;
}

#upper {
    height: 2fr;
    margin: 1 0 0 0;
}

.panel-title {
    color: $accent;
    text-style: bold;
    padding: 0 1;
    background: $panel;
}

#progress-panel, #messages-panel {
    background: $surface;
    border: tall $primary 70%;
    margin: 0 1;
}

#progress-panel {
    width: 2fr;
}

#messages-panel {
    width: 3fr;
}

#analysis-panel {
    height: 3fr;
    background: $surface;
    border: tall $success 70%;
    margin: 1 1;
    padding: 0 1;
}

#stats {
    height: 3;
    content-align: center middle;
    color: $foreground;
    background: $panel;
    border-top: tall $accent 50%;
}

DataTable {
    height: 1fr;
    background: $surface;
}

DataTable > .datatable--header {
    background: $panel;
    color: $accent;
    text-style: bold;
}

DataTable > .datatable--odd-row {
    background: $boost;
}

Markdown {
    background: $surface;
    padding: 0 1;
}
"""


class AnalysisScreen(Screen[dict]):
    """Live panels while one analysis streams."""

    CSS = ANALYSIS_CSS
    BINDINGS = [("q", "dismiss_quit", "Quit")]

    status_line: reactive[str] = reactive("Starting…")

    def __init__(
        self,
        *,
        selections: dict,
        config: dict,
        graph: Any,
        stats_handler: Any,
        portfolio=None,
    ) -> None:
        super().__init__()
        # Must not be named ``selections``: Screen already owns that reactive for
        # text/widget selection, and assigning a dict crashes _watch_selections.
        self.run_selections = selections
        self.config = config
        self.graph = graph
        self.stats_handler = stats_handler
        self.portfolio = portfolio
        self.start_time = time.time()
        self._final_state: dict = {}
        self._error: BaseException | None = None

    def compose(self) -> ComposeResult:
        ticker = self.run_selections.get("ticker", "")
        date = self.run_selections.get("analysis_date", "")
        yield Static(
            f"[b]TradingAgents[/b]  [dim]live analysis[/dim]\n"
            f"[#E9C46A]{ticker}[/]  ·  {date}  ·  "
            f"[dim]Tauric Research[/dim]",
            id="header",
        )
        with Horizontal(id="upper"):
            with Vertical(id="progress-panel"):
                yield Static("Progress", classes="panel-title")
                yield DataTable(id="progress", zebra_stripes=True)
            with Vertical(id="messages-panel"):
                yield Static("Messages & Tools", classes="panel-title")
                yield DataTable(id="messages", zebra_stripes=True)
        with Vertical(id="analysis-panel"):
            yield Static("Current Report", classes="panel-title")
            yield Markdown(current_report_markdown(), id="report")
        yield Static(self.status_line, id="stats")
        yield Footer()

    def on_mount(self) -> None:
        progress = self.query_one("#progress", DataTable)
        progress.add_columns("Team", "Agent", "Status")
        progress.cursor_type = "none"

        messages = self.query_one("#messages", DataTable)
        messages.add_columns("Time", "Type", "Content")
        messages.cursor_type = "none"

        self.set_interval(0.25, self.refresh_panels)
        self.run_worker(self._run_in_thread, exclusive=True, thread=True)

    def action_dismiss_quit(self) -> None:
        self.dismiss({})

    def _run_in_thread(self) -> None:
        try:
            final_state, _tracker = drive_analysis(
                selections=self.run_selections,
                config=self.config,
                graph=self.graph,
                stats_handler=self.stats_handler,
                portfolio=self.portfolio,
                on_refresh=lambda: self.app.call_from_thread(self.refresh_panels),
            )
            self._final_state = final_state
        except BaseException as exc:  # noqa: BLE001 - surface in UI then dismiss
            self._error = exc
            self.app.call_from_thread(
                self.query_one("#stats", Static).update,
                f"[red]Error: {exc}[/red]",
            )
        finally:
            self.app.call_from_thread(self._finish)

    def _finish(self) -> None:
        self.dismiss(self._final_state if self._error is None else {})

    def refresh_panels(self) -> None:
        progress = self.query_one("#progress", DataTable)
        progress.clear()
        for team, agent, status in progress_rows():
            style = {
                "pending": "yellow",
                "in_progress": "cyan",
                "completed": "green",
                "error": "red",
            }.get(status, "white")
            progress.add_row(team, agent, f"[{style}]{status}[/{style}]")

        messages = self.query_one("#messages", DataTable)
        messages.clear()
        for timestamp, msg_type, content in recent_message_rows():
            type_style = {
                "Tool": "magenta",
                "Agent": "cyan",
                "System": "yellow",
                "Data": "green",
                "User": "white",
                "Control": "dim",
            }.get(msg_type, "white")
            messages.add_row(
                f"[dim]{timestamp}[/dim]",
                f"[{type_style}]{msg_type}[/{type_style}]",
                content,
            )

        self.query_one("#report", Markdown).update(current_report_markdown())
        self.query_one("#stats", Static).update(
            footer_stats(self.stats_handler, self.start_time)
        )


class AnalysisApp(App[dict]):
    """Standalone Textual app used by the terminal CLI path."""

    TITLE = "TradingAgents"
    CSS = SHARED_CSS + "\n" + ANALYSIS_CSS

    def __init__(
        self,
        *,
        selections: dict,
        config: dict,
        graph: Any,
        stats_handler: Any,
        portfolio=None,
    ) -> None:
        super().__init__()
        self._screen_kwargs = {
            "selections": selections,
            "config": config,
            "graph": graph,
            "stats_handler": stats_handler,
            "portfolio": portfolio,
        }

    def on_mount(self) -> None:
        apply_trading_theme(self)
        self.push_screen(
            AnalysisScreen(**self._screen_kwargs),
            callback=lambda result: self.exit(result or {}),
        )


def run_analysis_app(
    *,
    selections: dict,
    config: dict,
    graph: Any,
    stats_handler: Any,
    portfolio=None,
) -> dict:
    """Run the Textual analysis UI and return the final state."""
    app = AnalysisApp(
        selections=selections,
        config=config,
        graph=graph,
        stats_handler=stats_handler,
        portfolio=portfolio,
    )
    result = app.run()
    return result or {}
