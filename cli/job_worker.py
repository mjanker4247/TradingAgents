"""Detached worker: run one analysis job without a browser session.

Usage::

    python -m cli.job_worker <job_id>
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _load_dotenv() -> None:
    env_path = Path.cwd() / ".env"
    if env_path.exists():
        from dotenv import load_dotenv

        load_dotenv(env_path)


def run_job(job_id: str) -> int:
    """Execute a queued job; return process exit code."""
    # Import after dotenv so TRADINGAGENTS_* path overrides are visible when
    # modules call build_default_config().
    from cli.analysis_driver import drive_analysis
    from cli.display import message_buffer
    from cli.finalize import analyst_keys_from_selections, save_analysis_reports
    from cli.jobs import JobStore
    from cli.stats_handler import StatsCallbackHandler
    from cli.view_model import current_report_markdown
    from tradingagents.default_config import build_default_config
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    store = JobStore(build_default_config())
    record = store.get(job_id)
    if record is None:
        print(f"Unknown job: {job_id}", file=sys.stderr)
        return 2

    selections = store.load_selections(job_id)
    config = store.load_config(job_id)
    if selections is None or config is None:
        store.update(job_id, status="failed", error="Missing selections or config on disk.")
        return 2

    store.update(job_id, status="running", pid=os_getpid(), error=None)

    stats_handler = StatsCallbackHandler()
    graph = TradingAgentsGraph(
        analyst_keys_from_selections(selections),
        config=config,
        debug=True,
        callbacks=[stats_handler],
    )

    def on_refresh() -> None:
        stats = stats_handler.get_stats()
        store.write_snapshot(
            job_id,
            {
                "agent_status": dict(message_buffer.agent_status),
                "messages": list(message_buffer.messages)[-80:],
                "tool_calls": [
                    [ts, name, args]
                    for ts, name, args in list(message_buffer.tool_calls)[-40:]
                ],
                "current_report": current_report_markdown(),
                "stats": stats,
            },
        )
        store.update(job_id, status="running")

    try:
        final_state, _tracker = drive_analysis(
            selections=selections,
            config=config,
            graph=graph,
            stats_handler=stats_handler,
            portfolio=None,
            on_refresh=on_refresh,
        )
        store.write_final_state(job_id, final_state)
        on_refresh()

        save = save_analysis_reports(
            graph,
            final_state,
            selections["ticker"],
            html=True,
        )
        fields: dict = {"status": "completed", "error": None, "pid": None}
        if save.ok:
            fields["report_path"] = str(save.save_path)
            if save.html_page is not None:
                fields["html_path"] = str(save.html_page)
        else:
            fields["error"] = f"Analysis finished but save failed: {save.error}"
        store.update(job_id, **fields)
        return 0 if save.ok else 1
    except BaseException as exc:  # noqa: BLE001 - persist failure for the UI
        err = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
        store.update(job_id, status="failed", error=err, pid=None)
        store.write_snapshot(
            job_id,
            {
                **store.read_snapshot(job_id),
                "current_report": f"**Error**\n\n```\n{err}\n```",
            },
        )
        return 1


def os_getpid() -> int:
    import os

    return os.getpid()


def main(argv: list[str] | None = None) -> None:
    _load_dotenv()
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("Usage: python -m cli.job_worker <job_id>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(run_job(args[0]))


if __name__ == "__main__":
    main()
