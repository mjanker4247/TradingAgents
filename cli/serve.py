"""Serve the TradingAgents Textual app as a local web service."""

from __future__ import annotations

import sys


def serve_web(host: str = "localhost", port: int = 8000, public_url: str | None = None) -> None:
    """Start textual-serve with ``python -m cli.web_app`` as the app command."""
    from textual_serve.server import Server

    command = f"{sys.executable} -m cli.web_app"
    server = Server(
        command,
        host=host,
        port=port,
        title="TradingAgents",
        public_url=public_url,
    )
    server.serve()
