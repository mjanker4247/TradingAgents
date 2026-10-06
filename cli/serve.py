"""Serve the TradingAgents Textual app as a local web service."""

from __future__ import annotations

import sys
from pathlib import Path

_TEMPLATES_DIR = Path(__file__).resolve().parent / "web_templates"


def serve_web(host: str = "localhost", port: int = 8000, public_url: str | None = None) -> None:
    """Start textual-serve with ``python -m cli.web_app`` as the app command.

    Uses a custom HTML shell so the browser terminal starts at a denser type
    size (about 11–14px by viewport) instead of textual-serve's 16px default.
    Pass ``?fontsize=N`` to override.
    """
    from textual_serve.server import Server

    command = f"{sys.executable} -m cli.web_app"
    server = Server(
        command,
        host=host,
        port=port,
        title="TradingAgents",
        public_url=public_url,
        templates_path=_TEMPLATES_DIR,
    )
    server.serve()
