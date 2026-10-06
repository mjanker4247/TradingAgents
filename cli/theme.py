"""Shared Textual theme and CSS for the CLI / web UI."""

from __future__ import annotations

from textual.app import App
from textual.theme import Theme

# Deep teal board + amber accent: readable in the browser terminal, not purple-default.
TRADING_THEME = Theme(
    name="tradingagents",
    primary="#1F8A70",
    secondary="#2A6F97",
    accent="#E9C46A",
    foreground="#E8EEF2",
    background="#0F171C",
    surface="#162229",
    panel="#1C2A33",
    success="#3DDC97",
    warning="#F4A261",
    error="#E76F51",
    dark=True,
    variables={
        "footer-key-foreground": "#E9C46A",
        "input-selection-background": "#1F8A70 40%",
        "block-cursor-background": "#1F8A70",
        "block-cursor-foreground": "#0F171C",
    },
)

SHARED_CSS = """
Screen {
    background: $background;
}

Header {
    dock: top;
    background: $primary;
    color: $foreground;
    text-style: bold;
}

Footer {
    background: $panel;
    color: $text-muted;
}

Label.section-title {
    color: $accent;
    text-style: bold;
    margin: 1 0 0 0;
}

Label.field-label {
    color: $text-muted;
    margin-top: 1;
}

Input {
    background: $surface;
    border: tall $primary 60%;
    margin-bottom: 0;
}

Input:focus {
    border: tall $accent;
}

Select {
    background: $surface;
    border: tall $primary 60%;
}

Select:focus {
    border: tall $accent;
}

Checkbox {
    background: $surface;
    color: $foreground;
    border: tall $panel;
    margin-right: 1;
}

Checkbox.-on {
    border: tall $success;
    color: $success;
}

Button {
    margin-right: 1;
    min-width: 16;
}

Button.success, Button.-success {
    background: $success;
    color: $background;
    text-style: bold;
}

.status-ok {
    color: $success;
    background: $success 15%;
    padding: 0 1;
    margin: 1 0;
    border: tall $success 40%;
}

.status-bad {
    color: $error;
    background: $error 12%;
    padding: 0 1;
    margin: 1 0;
    border: tall $error 40%;
}

#env-summary {
    color: $text-muted;
    background: $panel;
    padding: 1 1;
    margin: 1 0;
    border: solid $primary 40%;
}
"""


def apply_trading_theme(app: App) -> None:
    """Register and activate the TradingAgents theme on an App."""
    app.register_theme(TRADING_THEME)
    app.theme = "tradingagents"
