"""Textual screens for listing and watching background analysis jobs."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Label, Markdown, Static

from cli.jobs import JobRecord, JobStore, stop_job
from cli.theme import SHARED_CSS, apply_responsive_narrow


def _status_style(status: str) -> str:
    return {
        "queued": "yellow",
        "running": "cyan",
        "completed": "green",
        "failed": "red",
    }.get(status, "white")


class JobsScreen(Screen[tuple[str, str | None] | None]):
    """Home: list background jobs; start new or open an existing one.

    Dismiss value: ``(\"new\", None)``, ``(\"open\", job_id)``, ``(\"quit\", None)``,
    or ``None`` on quit binding.
    """

    CSS = SHARED_CSS + """
    #jobs-wrap {
        width: 100%;
        height: 1fr;
        padding: 1 1;
    }

    #jobs-title {
        color: $success;
        text-style: bold;
        height: 1;
    }

    #jobs-hint {
        color: $text-muted;
        height: auto;
        margin-bottom: 1;
    }

    #jobs-table {
        height: 1fr;
        background: $surface;
        border: tall $primary 70%;
    }

    #jobs-actions {
        height: auto;
        margin-top: 1;
    }
    """

    BINDINGS = [("q", "quit_jobs", "Quit"), ("r", "refresh", "Refresh")]

    def __init__(self, store: JobStore | None = None) -> None:
        super().__init__()
        self.store = store or JobStore()
        self._rows: list[JobRecord] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="jobs-wrap"):
            yield Label("Analyses", id="jobs-title")
            yield Static(
                "Runs continue on the server after you close this browser. "
                "Open a row to watch progress or review a finished report.",
                id="jobs-hint",
            )
            yield DataTable(id="jobs-table", zebra_stripes=True, cursor_type="row")
            with Horizontal(id="jobs-actions"):
                yield Button("New analysis", variant="success", id="new")
                yield Button("Open selected", variant="primary", id="open")
                yield Button("Refresh", variant="default", id="refresh")
                yield Button("Quit", variant="default", id="quit")
        yield Footer()

    def on_mount(self) -> None:
        apply_responsive_narrow(self)
        table = self.query_one("#jobs-table", DataTable)
        table.add_columns("Status", "Ticker", "Date", "Updated", "Job ID")
        self.refresh_jobs()
        self.set_interval(2.0, self.refresh_jobs)

    def on_resize(self, event) -> None:
        apply_responsive_narrow(self, event.size.width)

    def refresh_jobs(self) -> None:
        table = self.query_one("#jobs-table", DataTable)
        self._rows = self.store.list_jobs()
        table.clear()
        for job in self._rows:
            style = _status_style(job.status)
            table.add_row(
                f"[{style}]{job.status}[/{style}]",
                job.ticker,
                job.analysis_date,
                job.updated_at.replace("T", " ")[:19],
                job.job_id,
                key=job.job_id,
            )

    def _selected_job_id(self) -> str | None:
        table = self.query_one("#jobs-table", DataTable)
        if table.row_count == 0:
            return None
        row = table.cursor_row
        if row is None or row < 0 or row >= len(self._rows):
            return None
        # DataTable may reorder; prefer row key when available.
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            if row_key is not None and row_key.value:
                return str(row_key.value)
        except Exception:  # noqa: BLE001 - fall back to list index
            pass
        return self._rows[row].job_id

    def action_refresh(self) -> None:
        self.refresh_jobs()

    def action_quit_jobs(self) -> None:
        self.dismiss(("quit", None))

    @on(Button.Pressed, "#new")
    def new_pressed(self) -> None:
        self.dismiss(("new", None))

    @on(Button.Pressed, "#open")
    def open_pressed(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            self.notify("Select a job first", severity="warning")
            return
        self.dismiss(("open", job_id))

    @on(Button.Pressed, "#refresh")
    def refresh_pressed(self) -> None:
        self.refresh_jobs()

    @on(Button.Pressed, "#quit")
    def quit_pressed(self) -> None:
        self.dismiss(("quit", None))

    @on(DataTable.RowSelected, "#jobs-table")
    def row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key is not None and event.row_key.value:
            self.dismiss(("open", str(event.row_key.value)))


class JobWatchScreen(Screen[str | None]):
    """Poll a running (or finished) job from disk; dismiss with job_id when done."""

    CSS = SHARED_CSS + """
    #header {
        height: 3;
        padding: 0 1;
        background: $primary;
        color: $foreground;
        border-bottom: tall $accent;
    }

    #upper {
        height: 2fr;
        margin: 0;
        layout: horizontal;
    }

    .panel-title {
        color: $accent;
        text-style: bold;
        padding: 0 1;
        height: 1;
        background: $panel;
    }

    #progress-panel, #messages-panel {
        background: $surface;
        border: tall $primary 70%;
        margin: 0 1 0 0;
        height: 1fr;
    }

    #progress-panel { width: 2fr; margin-left: 1; }
    #messages-panel { width: 3fr; margin-right: 1; }

    #analysis-panel {
        height: 3fr;
        background: $surface;
        border: tall $success 70%;
        margin: 0 1;
        padding: 0 1;
    }

    #stats {
        height: 1;
        content-align: center middle;
        color: $foreground;
        background: $panel;
        border-top: tall $accent 50%;
    }

    #watch-actions {
        height: auto;
        dock: bottom;
        padding: 0 1;
        background: $panel;
    }

    DataTable { height: 1fr; background: $surface; }

    .narrow #upper { layout: vertical; height: auto; max-height: 40%; }
    .narrow #progress-panel, .narrow #messages-panel {
        width: 1fr; height: 10; margin: 0 1;
    }
    """

    BINDINGS = [("q", "back", "Back"), ("s", "stop", "Stop")]

    status_line: reactive[str] = reactive("Connecting…")

    def __init__(self, job_id: str, store: JobStore | None = None) -> None:
        super().__init__()
        self.job_id = job_id
        self.store = store or JobStore()
        self._finished = False

    def compose(self) -> ComposeResult:
        job = self.store.get(self.job_id)
        title = job.label if job else self.job_id
        yield Static(
            f"[b]Watching[/b]  {title}\n[dim]{self.job_id}[/dim]",
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
            yield Markdown("*Waiting…*", id="report")
        yield Static(self.status_line, id="stats")
        with Horizontal(id="watch-actions"):
            yield Button("Open report", variant="success", id="open-report", disabled=True)
            yield Button("Stop job", variant="error", id="stop")
            yield Button("Back to list", variant="default", id="back")
        yield Footer()

    def on_mount(self) -> None:
        apply_responsive_narrow(self)
        progress = self.query_one("#progress", DataTable)
        progress.add_columns("Agent", "Status")
        progress.cursor_type = "none"
        messages = self.query_one("#messages", DataTable)
        messages.add_columns("Time", "Type", "Content")
        messages.cursor_type = "none"
        self.set_interval(1.0, self.poll)

    def on_resize(self, event) -> None:
        apply_responsive_narrow(self, event.size.width)

    def poll(self) -> None:
        job = self.store.get(self.job_id)
        snap = self.store.read_snapshot(self.job_id)
        if job is None:
            self.query_one("#stats", Static).update("[red]Job missing on disk[/red]")
            return

        progress = self.query_one("#progress", DataTable)
        progress.clear()
        for agent, status in (snap.get("agent_status") or {}).items():
            style = _status_style(
                "completed" if status == "completed" else "running" if status == "in_progress" else "queued"
            )
            # Map agent status names to colors loosely
            color = {
                "pending": "yellow",
                "in_progress": "cyan",
                "completed": "green",
                "error": "red",
            }.get(status, "white")
            progress.add_row(agent, f"[{color}]{status}[/{color}]")

        messages = self.query_one("#messages", DataTable)
        messages.clear()
        rows = list(snap.get("messages") or [])[-12:]
        rows.reverse()
        for item in rows:
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            ts, msg_type, content = item[0], item[1], str(item[2])
            if len(content) > 200:
                content = content[:197] + "..."
            messages.add_row(f"[dim]{ts}[/dim]", str(msg_type), content)

        report = snap.get("current_report") or "*Waiting for analysis report…*"
        self.query_one("#report", Markdown).update(report)

        stats = snap.get("stats") or {}
        style = _status_style(job.status)
        parts = [
            f"[{style}]{job.status}[/{style}]",
            f"LLM: {stats.get('llm_calls', 0)}",
            f"Tools: {stats.get('tool_calls', 0)}",
        ]
        tin, tout = stats.get("tokens_in", 0), stats.get("tokens_out", 0)
        if tin or tout:
            parts.append(f"Tokens: {tin}\u2191 {tout}\u2193")
        if job.error:
            parts.append(f"[red]{job.error}[/red]")
        self.query_one("#stats", Static).update(" | ".join(parts))

        open_btn = self.query_one("#open-report", Button)
        if job.status == "completed":
            open_btn.disabled = False
            if not self._finished:
                self._finished = True
                self.notify("Analysis completed", severity="information")
        elif job.status == "failed":
            open_btn.disabled = True
            self.query_one("#stop", Button).disabled = True

    def action_back(self) -> None:
        self.dismiss(None)

    def action_stop(self) -> None:
        stop_job(self.job_id, self.store.config)
        self.notify("Stop signal sent", severity="warning")

    @on(Button.Pressed, "#back")
    def back_pressed(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#stop")
    def stop_pressed(self) -> None:
        self.action_stop()

    @on(Button.Pressed, "#open-report")
    def open_report(self) -> None:
        job = self.store.get(self.job_id)
        if job and job.status == "completed":
            self.dismiss(self.job_id)
        else:
            self.notify("Job is not finished yet", severity="warning")
