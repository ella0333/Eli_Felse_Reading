"""Printing a reading session to the terminal.

The shape follows the rest of the family: a ruled header, indented lines for
the page, and the agent's own voice left unindented so it stands out.
"""

from __future__ import annotations

import sys

RULE_WIDTH = 60
PREVIEW_CHARS = 300


def _write(line: str) -> None:
    """Print ASCII-safe, the way the base's print_system does."""
    try:
        print(line)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "ascii"
        print(line.encode(encoding, errors="replace").decode(encoding))


def print_system(text: str) -> None:
    _write(f"[system] {text}")


def print_header(text: str) -> None:
    _write("\n" + "=" * RULE_WIDTH + f"\n  {text}\n" + "=" * RULE_WIDTH)


def print_page(header: str, text: str, has_image: bool) -> None:
    """The page header, a short preview of its text, and whether an illustration came with it."""
    _write(f"\n{header}")
    if text:
        preview = text[:PREVIEW_CHARS] + ("..." if len(text) > PREVIEW_CHARS else "")
        for line in preview.splitlines():
            if line.strip():
                _write(f"  {line.strip()}")
    if has_image:
        _write("  Illustration attached")


def print_thinking(text: str) -> None:
    """The agent's reasoning, matching how the base prints it at a menu."""
    if text:
        _write(f"\nThinking: {text}")


def print_menu(text: str) -> None:
    """What the model is shown, unindented, the way the base prints a menu."""
    _write(f"\n{text}")


def print_choice(text: str) -> None:
    """The model's pick, matching the base's `Choice:` line."""
    if text:
        _write(f"Choice: {text}")
