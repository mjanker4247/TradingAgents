"""Drive one analysis stream into ``message_buffer``, UI-agnostic."""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any

from cli.display import (
    ANALYST_ORDER,
    AnalystWallTimeTracker,
    classify_message_type,
    message_buffer,
    update_analyst_statuses,
    update_research_team_status,
)
from tradingagents.dataflows.symbols import safe_ticker_component
from tradingagents.graph.analyst_execution import build_analyst_execution_plan
from tradingagents.graph.trading_graph import TradingAgentsGraph

RefreshFn = Callable[[], None]


def run_directory(config: dict, ticker: str, trade_date: str) -> Path:
    """Where this run writes, with the ticker validated as a path component."""
    return Path(config["results_dir"]) / safe_ticker_component(ticker) / trade_date


def announce_checkpoint_state(graph, ticker: str, trade_date: str) -> None:
    """Record whether this run resumed a saved checkpoint."""
    if getattr(graph, "_resuming", False):
        message_buffer.add_message(
            "System", f"Resuming the saved run for {ticker} on {trade_date}"
        )
    else:
        message_buffer.add_message("System", f"Starting fresh for {ticker} on {trade_date}")


def _save_message_decorator(obj, func_name, log_file: Path):
    func = getattr(obj, func_name)

    @wraps(func)
    def wrapper(*args, **kwargs):
        func(*args, **kwargs)
        timestamp, message_type, content = obj.messages[-1]
        content = content.replace("\n", " ")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"{timestamp} [{message_type}] {content}\n")

    return wrapper


def _save_tool_call_decorator(obj, func_name, log_file: Path):
    func = getattr(obj, func_name)

    @wraps(func)
    def wrapper(*args, **kwargs):
        func(*args, **kwargs)
        timestamp, tool_name, args_ = obj.tool_calls[-1]
        args_str = ", ".join(f"{k}={v}" for k, v in args_.items())
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"{timestamp} [Tool Call] {tool_name}({args_str})\n")

    return wrapper


def _save_report_section_decorator(obj, func_name, report_dir: Path):
    func = getattr(obj, func_name)

    @wraps(func)
    def wrapper(section_name, content):
        func(section_name, content)
        if section_name in obj.report_sections and obj.report_sections[section_name] is not None:
            section = obj.report_sections[section_name]
            if section:
                text = (
                    "\n".join(str(item) for item in section)
                    if isinstance(section, list)
                    else section
                )
                with open(report_dir / f"{section_name}.md", "w", encoding="utf-8") as f:
                    f.write(text)

    return wrapper


def drive_analysis(
    *,
    selections: dict,
    config: dict,
    graph: TradingAgentsGraph,
    stats_handler: Any,
    portfolio=None,
    on_refresh: RefreshFn | None = None,
) -> tuple[dict, AnalystWallTimeTracker]:
    """Stream the graph into ``message_buffer``, calling ``on_refresh`` after updates.

    Returns ``(final_state, wall_time_tracker)``.
    """
    refresh = on_refresh or (lambda: None)

    selected_set = {analyst.value for analyst in selections["analysts"]}
    selected_analyst_keys = [a for a in ANALYST_ORDER if a in selected_set]
    analyst_execution_plan = build_analyst_execution_plan(selected_analyst_keys)
    analyst_wall_time_tracker = AnalystWallTimeTracker(analyst_execution_plan)

    message_buffer.init_for_analysis(selected_analyst_keys)
    start_time = time.time()

    results_dir = run_directory(config, selections["ticker"], selections["analysis_date"])
    results_dir.mkdir(parents=True, exist_ok=True)
    report_dir = results_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    log_file = results_dir / "message_tool.log"
    log_file.touch(exist_ok=True)

    message_buffer.add_message = _save_message_decorator(
        message_buffer, "add_message", log_file
    )
    message_buffer.add_tool_call = _save_tool_call_decorator(
        message_buffer, "add_tool_call", log_file
    )
    message_buffer.update_report_section = _save_report_section_decorator(
        message_buffer, "update_report_section", report_dir
    )

    refresh()

    message_buffer.add_message("System", f"Selected ticker: {selections['ticker']}")
    if selections["asset_type"] != "stock":
        message_buffer.add_message(
            "System", f"Detected asset type: {selections['asset_type']}"
        )
    message_buffer.add_message(
        "System", f"Analysis date: {selections['analysis_date']}"
    )
    message_buffer.add_message(
        "System",
        f"Selected analysts: {', '.join(analyst.value for analyst in selections['analysts'])}",
    )
    refresh()

    for spec in analyst_execution_plan.specs:
        message_buffer.update_agent_status(spec.agent_node, "in_progress")
        analyst_wall_time_tracker.mark_started(spec.key)
    refresh()

    message_buffer.add_message(
        "System",
        f"Analyzing {selections['ticker']} on {selections['analysis_date']}...",
    )
    refresh()

    init_agent_state = graph.create_run_state(
        selections["ticker"],
        selections["analysis_date"],
        selections["asset_type"],
        portfolio,
    )
    args = graph.propagator.get_graph_args(callbacks=[stats_handler])

    checkpoint_tid = graph.begin_checkpoint(
        selections["ticker"],
        selections["analysis_date"],
        selections["asset_type"],
        portfolio,
    )
    if checkpoint_tid is not None:
        args.setdefault("config", {}).setdefault("configurable", {})[
            "thread_id"
        ] = checkpoint_tid
        announce_checkpoint_state(
            graph, selections["ticker"], selections["analysis_date"]
        )

    trace: list[dict] = []
    try:
        for messages, chunk in graph.stream_run(
            graph.checkpoint_input(init_agent_state), **args
        ):
            for message in messages:
                msg_id = getattr(message, "id", None)
                if msg_id is not None:
                    if msg_id in message_buffer._processed_message_ids:
                        continue
                    message_buffer._processed_message_ids.add(msg_id)

                msg_type, content = classify_message_type(message)
                if content and content.strip():
                    message_buffer.add_message(msg_type, content)

                if hasattr(message, "tool_calls") and message.tool_calls:
                    for tool_call in message.tool_calls:
                        if isinstance(tool_call, dict):
                            message_buffer.add_tool_call(
                                tool_call["name"], tool_call["args"]
                            )
                        else:
                            message_buffer.add_tool_call(tool_call.name, tool_call.args)

            if chunk is None:
                refresh()
                continue

            update_analyst_statuses(
                message_buffer,
                chunk,
                wall_time_tracker=analyst_wall_time_tracker,
            )

            if chunk.get("investment_debate_state"):
                debate_state = chunk["investment_debate_state"]
                bull_hist = debate_state.get("bull_history", "").strip()
                bear_hist = debate_state.get("bear_history", "").strip()
                judge = (chunk.get("investment_plan") or "").strip()

                if bull_hist or bear_hist:
                    update_research_team_status("in_progress")
                if bull_hist:
                    message_buffer.update_report_section(
                        "investment_plan", f"### Bull Researcher Analysis\n{bull_hist}"
                    )
                if bear_hist:
                    message_buffer.update_report_section(
                        "investment_plan", f"### Bear Researcher Analysis\n{bear_hist}"
                    )
                if judge:
                    message_buffer.update_report_section(
                        "investment_plan", f"### Research Manager Decision\n{judge}"
                    )
                    update_research_team_status("completed")
                    message_buffer.update_agent_status("Trader", "in_progress")

            if chunk.get("trader_investment_plan"):
                message_buffer.update_report_section(
                    "trader_investment_plan", chunk["trader_investment_plan"]
                )
                if message_buffer.agent_status.get("Trader") != "completed":
                    message_buffer.update_agent_status("Trader", "completed")
                    message_buffer.update_agent_status(
                        "Aggressive Analyst", "in_progress"
                    )

            if chunk.get("risk_debate_state"):
                risk_state = chunk["risk_debate_state"]
                agg_hist = risk_state.get("aggressive_history", "").strip()
                con_hist = risk_state.get("conservative_history", "").strip()
                neu_hist = risk_state.get("neutral_history", "").strip()
                judge = (chunk.get("final_trade_decision") or "").strip()

                if agg_hist:
                    if (
                        message_buffer.agent_status.get("Aggressive Analyst")
                        != "completed"
                    ):
                        message_buffer.update_agent_status(
                            "Aggressive Analyst", "in_progress"
                        )
                    message_buffer.update_report_section(
                        "final_trade_decision",
                        f"### Aggressive Analyst Analysis\n{agg_hist}",
                    )
                if con_hist:
                    if (
                        message_buffer.agent_status.get("Conservative Analyst")
                        != "completed"
                    ):
                        message_buffer.update_agent_status(
                            "Conservative Analyst", "in_progress"
                        )
                    message_buffer.update_report_section(
                        "final_trade_decision",
                        f"### Conservative Analyst Analysis\n{con_hist}",
                    )
                if neu_hist:
                    if message_buffer.agent_status.get("Neutral Analyst") != "completed":
                        message_buffer.update_agent_status(
                            "Neutral Analyst", "in_progress"
                        )
                    message_buffer.update_report_section(
                        "final_trade_decision",
                        f"### Neutral Analyst Analysis\n{neu_hist}",
                    )
                if (
                    judge
                    and message_buffer.agent_status.get("Portfolio Manager")
                    != "completed"
                ):
                    message_buffer.update_agent_status(
                        "Portfolio Manager", "in_progress"
                    )
                    message_buffer.update_report_section(
                        "final_trade_decision",
                        f"### Portfolio Manager Decision\n{judge}",
                    )
                    message_buffer.update_agent_status("Aggressive Analyst", "completed")
                    message_buffer.update_agent_status(
                        "Conservative Analyst", "completed"
                    )
                    message_buffer.update_agent_status("Neutral Analyst", "completed")
                    message_buffer.update_agent_status("Portfolio Manager", "completed")

            refresh()
            trace.append(chunk)

        final_state: dict = {}
        for chunk in trace:
            final_state.update(chunk)

        graph.record_decision(
            selections["ticker"], selections["analysis_date"], final_state
        )
        graph.clear_checkpoint_on_success(
            selections["ticker"],
            selections["analysis_date"],
            selections["asset_type"],
            portfolio,
        )
    finally:
        graph.end_checkpoint()

    for agent in message_buffer.agent_status:
        message_buffer.update_agent_status(agent, "completed")

    message_buffer.add_message(
        "System", f"Completed analysis for {selections['analysis_date']}"
    )
    message_buffer.add_message("System", analyst_wall_time_tracker.format_summary())

    for section in message_buffer.report_sections:
        if section in final_state:
            message_buffer.update_report_section(section, final_state[section])

    refresh()
    # Attach timing for callers that show elapsed in the footer after the run.
    analyst_wall_time_tracker.start_time = start_time  # type: ignore[attr-defined]
    return final_state, analyst_wall_time_tracker
