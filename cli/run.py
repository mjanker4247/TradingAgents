"""Running one analysis from the CLI: build the graph, stream it into the live view, save the report."""

from __future__ import annotations

import os
import sys
import webbrowser
from pathlib import Path

import typer

from cli.analysis_driver import drive_analysis
from cli.display import (
    console,
    display_complete_report,
    message_buffer,
)
from cli.finalize import (
    REVIEW_WARNING,
    analyst_keys_from_selections,
    needs_review,
    save_analysis_reports,
)
from cli.selections import depth_from_env, get_user_selections, unattended_gaps
from cli.stats_handler import StatsCallbackHandler
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph


def _build_run_config(selections: dict, checkpoint: bool | None) -> dict:
    """Assemble the run config from interactive selections, honoring env precedence.

    Round counts and checkpoint follow "explicit env/flag wins": an env-applied
    value on DEFAULT_CONFIG is preserved unless the user overrode it on the CLI.
    """
    config = DEFAULT_CONFIG.copy()
    # Research depth sets both round counts, but an explicit env override
    # (TRADINGAGENTS_MAX_DEBATE_ROUNDS / _MAX_RISK_ROUNDS) wins over the
    # interactive selection — leave the env-applied value in place (#977).
    rounds = (("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "max_debate_rounds"),
              ("TRADINGAGENTS_MAX_RISK_ROUNDS", "max_risk_discuss_rounds"))
    depth_was_asked = not depth_from_env()
    for env_var, key in rounds:
        if not os.environ.get(env_var):
            config[key] = selections["research_depth"]
        elif depth_was_asked:
            # The depth question appeared (it is skipped only when both are
            # set), so say which half of the answer the environment overrode.
            console.print(
                f"[green]✓ {key} from environment:[/green] {config[key]} "
                f"(set by {env_var}, so the research depth you chose does not apply to it)"
            )
    config["quick_think_llm"] = selections["quick_think_llm"]
    config["deep_think_llm"] = selections["deep_think_llm"]
    config["backend_url"] = selections["backend_url"]
    config["llm_provider"] = selections["llm_provider"].lower()
    # Provider-specific thinking configuration
    config["google_thinking_level"] = selections.get("google_thinking_level")
    config["openai_reasoning_effort"] = selections.get("openai_reasoning_effort")
    config["anthropic_effort"] = selections.get("anthropic_effort")
    config["output_language"] = selections.get("output_language", "English")
    # --checkpoint/--no-checkpoint overrides only when explicitly given; omitting
    # the flag preserves TRADINGAGENTS_CHECKPOINT_ENABLED / the default (#976).
    if checkpoint is not None:
        config["checkpoint_enabled"] = checkpoint
    return config


def _ui_mode() -> str:
    """``textual`` (default) or ``headless`` for tests / non-interactive drivers."""
    return os.environ.get("TRADINGAGENTS_UI", "textual").strip().lower()


def _launch_live_ui(
    *,
    selections: dict,
    config: dict,
    graph: TradingAgentsGraph,
    stats_handler: StatsCallbackHandler,
    portfolio=None,
) -> dict:
    """Show the live analysis UI; return the final merged state.

    Monkeypatch this in unit tests (or set TRADINGAGENTS_UI=headless) to skip Textual.
    """
    if _ui_mode() == "headless":
        final_state, _tracker = drive_analysis(
            selections=selections,
            config=config,
            graph=graph,
            stats_handler=stats_handler,
            portfolio=portfolio,
            on_refresh=lambda: None,
        )
        return final_state

    from cli.tui import run_analysis_app

    return run_analysis_app(
        selections=selections,
        config=config,
        graph=graph,
        stats_handler=stats_handler,
        portfolio=portfolio,
    )


def run_analysis(checkpoint: bool | None = None, portfolio=None, flags=None):
    flags = flags or {}
    # With no terminal nothing can answer a prompt: name every question still
    # open before any model is called, rather than stopping at the first one.
    if not (sys.stdin and sys.stdin.isatty()):
        gaps = unattended_gaps(flags)
        if gaps:
            console.print("[red]No terminal to answer the setup questions. Set:[/red]")
            for gap in gaps:
                console.print(f"  {gap}")
            raise typer.Exit(code=1)

    selections = get_user_selections(flags)

    config = _build_run_config(selections, checkpoint)

    stats_handler = StatsCallbackHandler()

    graph = TradingAgentsGraph(
        analyst_keys_from_selections(selections),
        config=config,
        debug=True,
        callbacks=[stats_handler],
    )

    final_state = _launch_live_ui(
        selections=selections,
        config=config,
        graph=graph,
        stats_handler=stats_handler,
        portfolio=portfolio,
    )

    # Post-analysis prompts (outside the Textual app for clean interaction)
    console.print("\n[bold cyan]Analysis Complete![/bold cyan]\n")

    # A decision nobody can read is not a position. Say so here rather than
    # leaving the run to look like a normal result.
    if needs_review(final_state):
        console.print(f"[yellow]{REVIEW_WARNING}[/yellow]\n")

    _offer_reports(
        final_state,
        graph,
        selections["ticker"],
        save=flags.get("save"),
        show=flags.get("show"),
        html=flags.get("html"),
    )


def _yes(question: str) -> bool:
    return typer.prompt(question, default="Y").strip().upper() in ("Y", "YES", "")


def _graphical_browser():
    """A browser that opens a page in its own window on this machine, or None.

    Over SSH the page sits on the remote machine, and a terminal browser (lynx,
    w3m, elinks) would take over the terminal, so neither is offered.
    """
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return None
    try:
        browser = webbrowser.get()
    except webbrowser.Error:
        return None
    if type(browser) is webbrowser.GenericBrowser or isinstance(browser, webbrowser.Elinks):
        return None
    return browser


def _open_page(page: Path) -> None:
    """Offer to open the saved page in a browser window, and say where it is if opening fails."""
    browser = _graphical_browser()
    if browser is None or not _yes("Open it in your browser?"):
        return
    try:
        opened = browser.open(page.as_uri())
    except (webbrowser.Error, OSError):
        opened = False
    if not opened:
        console.print(f"  [dim]Could not open a browser; the page is at:[/dim] {page}")


def _offer_reports(final_state, graph, ticker, save=None, show=None, html=None):
    """Save the report tree and show it; ``save``/``show``/``html`` answer the questions when given.

    A saved report includes the HTML page unless ``html`` is False. Someone
    answering the save question at the prompt is also asked about the page and
    offered to open it; a run whose flags answer the save question asks neither.
    """
    asked = save is None
    if asked:
        save = typer.prompt("Save report?", default="Y").strip().upper() in ("Y", "YES", "")
    if save:
        # Under results_dir, not the working directory: in Docker the working
        # directory is inside the container and the report goes with it, while
        # results_dir is the mounted volume the rest of the run already writes to.
        save_path = graph.default_report_path(ticker)
        if asked:   # someone at the prompt may pick another folder
            save_path = Path(typer.prompt(
                "Save path (press Enter for default)", default=str(save_path)
            ).strip())
        if html is None:
            html = _yes("Also save it as an HTML page?") if asked else True
        result = save_analysis_reports(
            graph, final_state, ticker, save_path=save_path, html=html
        )
        if result.ok:
            console.print(f"\n[green]✓ Report saved to:[/green] {result.save_path}")
            console.print(f"  [dim]Complete report:[/dim] {result.report_file.name}")
            if result.html_page is not None:
                console.print(f"  [dim]HTML report:[/dim] {result.html_page.name}")
                if asked:
                    _open_page(result.html_page)
        else:
            console.print(f"[red]Error saving report: {result.error}[/red]")

    if show is None:
        show = typer.prompt("\nDisplay full report on screen?", default="Y").strip().upper() in ("Y", "YES", "")
    if show:
        display_complete_report(final_state)


# Re-export for older tests/monkeypatches that imported these names from run.
__all__ = [
    "run_analysis",
    "_build_run_config",
    "_launch_live_ui",
    "message_buffer",
    "TradingAgentsGraph",
    "drive_analysis",
]
