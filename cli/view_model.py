"""Plain view-model helpers for the live analysis UI (Textual or headless)."""

from __future__ import annotations

import time

from cli.display import format_tokens, format_tool_args, message_buffer


def progress_rows() -> list[tuple[str, str, str]]:
    """Return (team, agent, status) rows for the progress panel."""
    all_teams = {
        "Analyst Team": [
            "Market Analyst",
            "Sentiment Analyst",
            "News Analyst",
            "Fundamentals Analyst",
        ],
        "Research Team": ["Bull Researcher", "Bear Researcher", "Research Manager"],
        "Trading Team": ["Trader"],
        "Risk Management": [
            "Aggressive Analyst",
            "Neutral Analyst",
            "Conservative Analyst",
        ],
        "Portfolio Management": ["Portfolio Manager"],
    }

    rows: list[tuple[str, str, str]] = []
    for team, agents in all_teams.items():
        active = [a for a in agents if a in message_buffer.agent_status]
        if not active:
            continue
        for i, agent in enumerate(active):
            status = message_buffer.agent_status.get(agent, "pending")
            rows.append((team if i == 0 else "", agent, status))
    return rows


def recent_message_rows(limit: int = 12) -> list[tuple[str, str, str]]:
    """Return newest-first (time, type, content) rows for the messages panel."""
    all_messages: list[tuple[str, str, str]] = []

    for timestamp, tool_name, args in message_buffer.tool_calls:
        formatted_args = format_tool_args(args)
        all_messages.append((timestamp, "Tool", f"{tool_name}: {formatted_args}"))

    for timestamp, msg_type, content in message_buffer.messages:
        content_str = str(content) if content else ""
        if len(content_str) > 200:
            content_str = content_str[:197] + "..."
        all_messages.append((timestamp, msg_type, content_str))

    all_messages.sort(key=lambda row: row[0], reverse=True)
    return all_messages[:limit]


def footer_stats(stats_handler=None, start_time: float | None = None) -> str:
    """Build the footer status line."""
    agents_completed = sum(
        1 for status in message_buffer.agent_status.values() if status == "completed"
    )
    agents_total = len(message_buffer.agent_status)
    reports_completed = message_buffer.get_completed_reports_count()
    reports_total = len(message_buffer.report_sections)

    parts = [f"Agents: {agents_completed}/{agents_total}"]

    if stats_handler:
        stats = stats_handler.get_stats()
        parts.append(f"LLM: {stats['llm_calls']}")
        parts.append(f"Tools: {stats['tool_calls']}")
        if stats["tokens_in"] > 0 or stats["tokens_out"] > 0:
            parts.append(
                f"Tokens: {format_tokens(stats['tokens_in'])}\u2191 "
                f"{format_tokens(stats['tokens_out'])}\u2193"
            )
        else:
            parts.append("Tokens: --")

    parts.append(f"Reports: {reports_completed}/{reports_total}")

    if start_time:
        elapsed = time.time() - start_time
        parts.append(f"\u23f1 {int(elapsed // 60):02d}:{int(elapsed % 60):02d}")

    return " | ".join(parts)


def current_report_markdown() -> str:
    """Markdown for the current report panel."""
    if message_buffer.current_report:
        return message_buffer.current_report
    return "*Waiting for analysis report...*"
