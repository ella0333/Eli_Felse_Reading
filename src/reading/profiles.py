"""Per-series profiles: what happened so far, and what happened last time.

After each session the plot summary of what was read becomes the series'
"last read" summary, and the one it replaces is folded into a short
"previously happened" recap, like a TV recap. Both are shown to the agent
when it picks a series and while it reads.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

# (system prompt, user prompt) -> the model's text, or None on failure
Complete = Callable[[str, str], Awaitable[str | None]]

PLOT_SYSTEM = (
    "You are summarizing what was read during a reading session of the {kind} '{name}'. "
    "Summarize the plot of what was read: the key events, character actions, and story developments. "
    "Write as if reminding the reader of what happened. Keep it concise (1-2 paragraphs). "
    "Do not include meta-commentary, just the plot summary."
)

RECAP_SYSTEM = "You are a concise story summarizer. Respond with only the summary paragraph."

RECAP_PROMPT = (
    "You are summarizing previous events from the {kind} '{name}' for a reader. "
    "Consolidate these summaries into one concise 'previously on' paragraph (like a TV recap). "
    "Focus on key plot points, character developments, and important events. "
    "Keep it under 150 words. Write in second person (you). "
    "Do not include meta-commentary, just summarize the story events.\n\n"
    "Previous summaries to consolidate:\n{pending}"
)


def chunk_text(text: str, max_chars: int) -> list[str]:
    """Split on paragraph breaks into pieces no longer than max_chars."""
    if len(text) <= max_chars:
        return [text]
    chunks, current = [], ""
    for para in text.split("\n\n"):
        while len(para) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(para[:max_chars])
            para = para[max_chars:]
        if current and len(current) + len(para) + 2 > max_chars:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks


class Profiles:
    def __init__(self, profiles_dir: Path) -> None:
        self.dir = profiles_dir

    def _path(self, key: str) -> Path:
        return self.dir / f"novel_{key}.json"

    def get(self, key: str, name: str = "") -> dict[str, Any]:
        try:
            return json.loads(self._path(key).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {
                "novel_key": key,
                "novel_name": name or key,
                "previously_happened_summary": "",
                "last_read_summary": "",
            }

    def _write(self, key: str, profile: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path(key).write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")

    def store_last_read_summary(self, key: str, name: str, summary: str) -> None:
        profile = self.get(key, name)
        old_last = profile.get("last_read_summary", "")
        old_prev = profile.get("previously_happened_summary", "")
        if old_last:
            profile["_pending_consolidation"] = f"{old_prev}\n\n{old_last}" if old_prev else old_last
        profile["last_read_summary"] = summary
        self._write(key, profile)

    async def consolidate(self, key: str, name: str, kind: str, complete: Complete) -> None:
        profile = self.get(key, name)
        pending = profile.pop("_pending_consolidation", "")
        if pending:
            recap = await complete(RECAP_SYSTEM, RECAP_PROMPT.format(kind=kind, name=name, pending=pending))
            profile["previously_happened_summary"] = recap or pending[:500]
        self._write(key, profile)


async def plot_summary(page_texts: list[str], name: str, kind: str, complete: Complete, max_chars: int) -> str:
    """A plot summary of the pages read this session, chunked to fit the model."""
    if not page_texts:
        return ""
    system = PLOT_SYSTEM.format(kind=kind, name=name)
    chunks = chunk_text("\n\n---\n\n".join(page_texts), max_chars)
    if len(chunks) == 1:
        return await complete(system, f"Summarize the plot of what was read:\n\n{chunks[0]}") or ""
    parts = []
    for i, chunk in enumerate(chunks):
        part = await complete(system, f"Summarize the plot of part {i + 1} of {len(chunks)}:\n\n{chunk}")
        if part:
            parts.append(part)
    if not parts:
        return ""
    combined = await complete(
        system, "Combine these partial plot summaries into one concise summary:\n\n" + "\n\n".join(parts)
    )
    return combined or parts[0]
