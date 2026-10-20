"""Reading Activity - the Eli Felse module interface.

Registers as a drop-in activity. Provides:
- A "Read" menu entry naming the kinds on the shelf, e.g. "Read (Books, Light Novels)",
  shown once there is something to read
- A kind menu when there is more than one kind, then a series menu with
  each series' progress and profile
- A page-by-page reading loop that saves its place between sessions
- A plot summary per session, folded into a "previously happened" recap

Books are never shipped or downloaded. You put your own EPUBs in the books
folder (scripts/setup.py does it for you), one folder per series.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import prompts
from .display import print_system
from .illustrations import API_KEY_ENV, Illustrations, image_mode
from .library import Library, singular
from .profiles import Profiles, plot_summary
from .reader import ReadingSession

if TYPE_CHECKING:
    from elifelse.activities.ctx import ActivityContext

try:
    from elifelse.activities.base import Activity
except ImportError:
    # Standalone mode - provide a stub
    class Activity:
        key = ""
        menu_label = ""
        menu_group = ""
        requires = []
        requires_base = ""
        schemas = {}
        isolate_context = False
        memory_mode = "standard"
        memory_rules = ""
        survey = None
        dashboard_view = ""
        module_dir = None

        def get_menu_label(self, ctx): return self.menu_label
        def get_status(self, ctx): return ""
        def get_prompt(self, persona): return ""
        def get_subject(self, ctx): return ""
        def available(self, ctx): return True
        async def startup(self, ctx): pass
        async def run(self, ctx): pass

MODULE_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_BOOKS_DIR = MODULE_ROOT / "books"


def books_dir_from(value: Any) -> Path:
    """config `books_dir`, relative paths taken from the module folder."""
    if not value:
        return DEFAULT_BOOKS_DIR
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else MODULE_ROOT / path


class ContextIO:
    """ReaderIO on top of the base's ctx."""

    def __init__(self, ctx: ActivityContext) -> None:
        self.ctx = ctx

    def base_prompt(self) -> str:
        return self.ctx.app.base_prompt()

    def menu_preview(self) -> str:
        try:
            entries = self.ctx.app.registry.menu_entries()
        except Exception:
            return ""
        if not entries:
            return ""
        lines = [
            "Available home menu options (for reference only, not available "
            "during this activity, listed so you know what to do after "
            "returning to menu):"
        ]
        for i, entry in enumerate(entries):
            status = f" ({entry['status']})" if entry.get("status") else ""
            lines.append(f"{chr(ord('A') + i)}) {entry['label']}{status}")
        return "\n".join(lines)

    def set_system_prompt(self, text: str) -> None:
        self.ctx.app.provider.set_system_prompt(text)

    def add_context(self, role: str, text: str) -> None:
        self.ctx.app.provider.context.add(role, text)

    def notification(self) -> str:
        try:
            return self.ctx.app.notification_line()
        except Exception:
            return ""

    def last_emotion(self, name: str) -> str:
        """The emotion from the last survey after reading this series."""
        innerlife = self.ctx.app.innerlife
        if innerlife is None or not innerlife.surveys_file.exists():
            return ""
        subject = f"reading {name}"
        emotion = ""
        try:
            for line in innerlife.surveys_file.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("activity") == ReadingActivity.key and record.get("subject") == subject:
                    emotion = record.get("emotion", "")
        except OSError:
            return ""
        return emotion

    async def generate(self, prompt: str, schema: dict[str, Any],
                       image_urls: list[str] | None = None) -> dict[str, Any]:
        return await self.ctx.app.provider.generate(
            prompt, schema=self.ctx.app.schemas.finalize(schema), image_urls=image_urls or None
        )

    async def recall(self, query: str, name: str) -> list[str]:
        return await self.ctx.recall(query, source=name)

    def remember(self, text: str, name: str) -> None:
        self.ctx.remember("assistant", text, subject=name)

    async def complete(self, system: str, prompt: str) -> str | None:
        """An isolated call for summaries: no context, no pacing."""
        return await self.ctx.app.provider.raw_completion(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        )


class ReadingActivity(Activity):
    key = "reading"
    menu_label = "Read"
    requires = []
    requires_base = ">=0.5.5,<1"
    isolate_context = True
    survey = "simple"
    memory_rules = (
        "Extract what the agent read and how it reacted: the story events it found "
        "important, the characters it cares about, and its opinions of the series. "
        "When it is fiction, its events happened to the characters, not to the agent."
    )

    def __init__(self) -> None:
        self._library: Library | None = None
        self._subject = ""

    # ~~~ setup ~~~

    def _ensure_library(self, ctx: ActivityContext) -> Library:
        if self._library is None:
            self._library = Library(
                books_dir_from(ctx.config.get("books_dir")),
                ctx.data_dir / "reading_tracker.json",
                say=print_system,
            )
        return self._library

    def _profiles(self, ctx: ActivityContext) -> Profiles:
        return Profiles(ctx.data_dir / "profiles")

    def _illustrations(self, ctx: ActivityContext) -> Illustrations:
        key = ctx.config.get(API_KEY_ENV) or os.environ.get(API_KEY_ENV, "")
        return Illustrations(image_mode(ctx.config.get("images", "off")), key, say=print_system)

    async def startup(self, ctx: ActivityContext) -> None:
        library = self._ensure_library(ctx)
        await asyncio.to_thread(library.sync)
        if not library.available():
            print_system(f"No books yet in {library.books_dir}. Run scripts/setup.py in the reading module folder.")

    # ~~~ menu presence ~~~

    def available(self, ctx: ActivityContext) -> bool:
        return bool(self._ensure_library(ctx).available())

    def get_menu_label(self, ctx: ActivityContext) -> str:
        kinds = self._ensure_library(ctx).kinds()
        return f"{self.menu_label} ({', '.join(kinds)})" if kinds else self.menu_label

    def get_subject(self, ctx: ActivityContext) -> str:
        """The series just read, once. The lifecycle asks before run(), when
        nothing has been picked, and again after it, which is when the survey
        and summary need the name."""
        subject, self._subject = self._subject, ""
        return subject

    # ~~~ the activity ~~~

    async def run(self, ctx: ActivityContext) -> str:
        library = self._ensure_library(ctx)
        await asyncio.to_thread(library.sync)
        kinds = library.kinds()
        if not kinds:
            return "Nothing available to read."

        io = ContextIO(ctx)
        profiles = self._profiles(ctx)

        kind = kinds[0]
        if len(kinds) > 1:
            io.set_system_prompt(ctx.app.base_prompt())
            kind = await ctx.choose(prompts.KIND_QUESTION, options=kinds)
        novels = library.available(kind)

        rows = []
        for n in novels:
            rows.append((n, library.status_text(n["key"]), profiles.get(n["key"], n["name"]), io.last_emotion(n["name"])))
        io.set_system_prompt("\n\n".join([ctx.app.base_prompt(), prompts.selection_guidance(kind)]))
        key = await ctx.choose(
            prompts.selection_question(novels, kind),
            options=[n["key"] for n in novels],
            labels=[f"{n['name']} {status}" for n, status, _, _ in rows],
            footer=prompts.all_profiles_text(rows, kind),
        )
        novel = library.get(key)
        name = novel["name"]
        self._subject = f"reading {name}"
        ctx.set_status("Reading", {"novel": name})

        profile = prompts.profile_text(novel, profiles.get(key, name), library.description(key), io.last_emotion(name))
        session = ReadingSession(io, library, self._illustrations(ctx), profile)
        note = await session.run(key)

        max_chars = max(8000, ctx.app.config.provider.max_context_tokens
                        * ctx.app.config.provider.chars_per_token - 10000)
        summary = await plot_summary(session.page_texts, name, singular(kind), io.complete, max_chars)
        if summary:
            profiles.store_last_read_summary(key, name, summary)
            await profiles.consolidate(key, name, singular(kind), io.complete)
            print_system(f"Reading plot summary stored: {name}")
        return note
