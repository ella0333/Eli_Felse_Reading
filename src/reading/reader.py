"""One reading session, page by page.

Shared by the base activity and the standalone harness. Each side hands in
a ReaderIO that talks to its own model connection; everything about what a
page looks like and when the session moves on lives here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from . import prompts
from .display import print_header, print_page, print_system, print_thinking
from .illustrations import Illustrations
from .library import Library

READING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"thinking": {"type": "string"}, "return_to_menu": {"type": "boolean"}},
    "required": ["thinking", "return_to_menu"],
    "additionalProperties": False,
}

MIN_TEXT_CHARS = 50


class ReaderIO(Protocol):
    def base_prompt(self) -> str: ...
    def menu_preview(self) -> str: ...
    def set_system_prompt(self, text: str) -> None: ...
    def add_context(self, role: str, text: str) -> None: ...
    def notification(self) -> str: ...
    def last_emotion(self, name: str) -> str: ...
    async def generate(self, prompt: str, schema: dict[str, Any],
                       image_urls: list[str] | None = None) -> dict[str, Any]: ...
    async def recall(self, query: str, name: str) -> list[str]: ...
    def remember(self, text: str, name: str) -> None: ...


class ReadingSession:
    def __init__(self, io: ReaderIO, library: Library, illustrations: Illustrations,
                 profile: str, page_limit: int = 0) -> None:
        self.io = io
        self.library = library
        self.illustrations = illustrations
        self.profile = profile
        self.page_limit = page_limit       # standalone only, 0 = no limit
        self.page_texts: list[str] = []    # every page prompt, for the plot summary
        self.pages_read = 0

    def _system_prompt(self, guidance: str, memories: str = "") -> str:
        parts = [self.io.base_prompt(), guidance]
        preview = self.io.menu_preview()
        if preview:
            parts.append(preview)
        if memories:
            parts.append(memories)
        return "\n\n".join(p for p in parts if p)

    async def run(self, key: str) -> str:
        novel = self.library.get(key)
        if not novel:
            return "Novel not found"
        name = novel["name"]
        print_header(f"READING: {name.upper()}")
        start_time = datetime.now()

        current_volume = novel["current_volume"]
        start_volume = current_volume
        total_volumes = novel["total_volumes"]

        pages = self.library.volume_pages(key, current_volume)
        if not pages:
            return f"Could not load pages for {name} Volume {current_volume}"
        total_readable = len(pages)

        # The stored page is the EPUB page number; find its readable index.
        stored_page = novel.get("current_page", 1)
        page_idx = 0
        for i, p in enumerate(pages):
            if p["page"] >= stored_page:
                page_idx = i
                break

        print_system(f"Reading {name} - Volume {current_volume}/{total_volumes}, "
                     f"Page {pages[page_idx]['page']} (readable page {page_idx + 1}/{total_readable})")

        current_chapter = None
        short_title = name.split(" - ")[0].strip() if " - " in name else name

        while page_idx < total_readable:
            page_info = pages[page_idx]
            chapter = page_info.get("chapter", "")
            if chapter:
                current_chapter = chapter

            content = self.library.page_content(key, current_volume, page_info)
            page_text = content.get("text", "")
            page_image = content.get("image_base64") if self.illustrations.shown else None

            # Strip the series title off the front before judging the page.
            check_text = page_text.strip()
            for prefix in [name, short_title]:
                if check_text.startswith(prefix):
                    check_text = check_text[len(prefix):].strip()
            has_text = len(check_text) >= MIN_TEXT_CHARS

            description = ""
            if page_image and self.illustrations.describe:
                description = await self.illustrations.describe_image(page_image, name) or ""
            attach = bool(page_image) and self.illustrations.attach
            has_image = attach or bool(description)

            if not has_text and not has_image:
                print_system(f"Skipping filler page {page_info['page']} "
                             f"(only {len(page_text)} chars: '{page_text[:40]}...')")
                page_idx += 1
                continue

            display_chapter = chapter or current_chapter or ""
            chapter_str = f" - {display_chapter}" if display_chapter else ""
            header = f"Volume {current_volume}/{total_volumes} | Page {page_idx + 1}/{total_readable}{chapter_str}"

            prompt = prompts.page_prompt(header, page_text, has_text, has_image, description)
            notif = self.io.notification()
            if notif:
                prompt += f"\n\n\U0001f4ec {notif}"
            self.page_texts.append(prompt)
            print_page(header, page_text if has_text else "", has_image)

            query = page_text[:200] if page_text else name
            memories = (await self.io.recall(query, name))[:3]
            self.io.set_system_prompt(self._system_prompt(
                prompts.reading_guidance(
                    novel["kind"], self.profile, start_time, pages_read=self.pages_read,
                    start_volume=start_volume, current_volume=current_volume,
                    total_volumes=total_volumes, chapter=current_chapter,
                ),
                prompts.memory_block(name, memories),
            ))

            result = await self.io.generate(prompt, READING_SCHEMA,
                                            image_urls=[page_image] if attach else None)
            if "error" in result:
                print_system(f"Error: {result['error']}")
                break

            thinking = result.get("thinking", "")
            if thinking:
                print_thinking(thinking)
                # For reading the thinking is the response, so it goes in the
                # context and multi-page sessions stay coherent.
                self.io.add_context("assistant", thinking)
                self.io.remember(thinking, name)

            # Store the NEXT page, so resuming continues instead of re-serving this one.
            next_page = pages[page_idx + 1]["page"] if page_idx + 1 < total_readable else page_info["page"]
            self.library.update_progress(key, current_volume, next_page)
            self.pages_read += 1

            returning = bool(result.get("return_to_menu", False))
            page_idx += 1

            # Runs even when returning to the menu, or progress stalls on the
            # last page of a volume.
            if page_idx >= total_readable:
                if current_volume < total_volumes:
                    current_volume += 1
                    pages = self.library.volume_pages(key, current_volume)
                    if not pages:
                        print_system(f"Could not load Volume {current_volume}")
                        break
                    total_readable = len(pages)
                    page_idx = 0
                    current_chapter = None
                    self.library.update_progress(key, current_volume, 1)
                    print_system(f"Starting Volume {current_volume}/{total_volumes}")
                else:
                    print_system(f"Finished all volumes of {name}!")
                    self.library.mark_completed(key)
                    break

            if returning:
                print("\nReturning to menu")
                break
            if self.page_limit and self.pages_read >= self.page_limit:
                print_system(f"Reached the session's limit of {self.page_limit} pages.")
                break

        return f"Reading session ended for {name}."
