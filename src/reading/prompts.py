"""What the agent reads: the series menu, the reading guidance, and each page."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .library import singular

RETURN_REMINDER = "[Reminder: set return_to_menu: true to stop reading and return to the main menu.]"

KIND_QUESTION = "What would you like to read?"

SELECTION_GUIDANCE = """You are choosing {a_one} to read. Review the available {many} and their profiles below.

If you are currently reading one, consider continuing it rather than starting something new, you'll enjoy seeing what happens next in the story you've already invested in. But the choice is yours.

Anything marked as completed is something you've already read all the way through to the end, there's nothing new left in it for you. Don't pick those again; continue one you're partway through, or start a fresh one you haven't read yet."""

READING_GUIDANCE = """You are currently reading {a_one}. You are reading page by page and thinking about what you read. If it is fiction, these are not real actions happening to you but to the characters you are reading about.

{profile}

Your response should contain your thoughts about what you just read. Think genuinely - react to plot developments, characters, twists, humor, or anything that stands out. You're reading for enjoyment.

Menu Navigation:
At the end of each page, decide if you want to continue reading or return to the menu.

Important:
return_to_menu: false, Continue reading (next page)
return_to_menu: true, Stop reading and return to the main menu

You must return to menu if you want to choose another menu option. If you would like to stop the current task and go do another task from the menu, you must set return_to_menu to true. If you want to respond to message notifications you must return to menu as well.

Only set return_to_menu to true if:
You want to switch to a different activity and stop reading."""


def a_one(kind: str) -> str:
    """'Light Novels' -> 'a light novel', 'Audiobooks' -> 'an audiobook'."""
    one = singular(kind)
    return f"{'an' if one[:1] in 'aeiou' else 'a'} {one}"


def selection_guidance(kind: str) -> str:
    return SELECTION_GUIDANCE.format(a_one=a_one(kind), many=kind.lower())


# ~~~ the series menu ~~~

def selection_question(novels: list[dict[str, Any]], kind: str) -> str:
    lines = [f"Choose {a_one(kind)} to read. Consider continuing one you're already reading before starting something new."]
    if any(n["status"] == "completed" for n in novels):
        lines.append("Note: anything marked (completed) you've already read from start to finish. "
                     "Pick one you're partway through, or start a new one you haven't read yet.")
    return "\n".join(lines)


def profile_text(novel: dict[str, Any], profile: dict[str, Any], description: str, emotion: str) -> str:
    """One series' profile, shown while reading it."""
    parts = [f"Title: {novel['name']}", f"Description: {description}",
             f"Progress: Volume {novel['current_volume']}/{novel['total_volumes']}"]
    if emotion:
        parts.append(f"You last felt {emotion} reading this.")
    if profile.get("previously_happened_summary"):
        parts.append(f"\nPreviously happened:\n{profile['previously_happened_summary']}")
    if profile.get("last_read_summary"):
        parts.append(f"\nWhen you last read:\n{profile['last_read_summary']}")
    return "\n\n".join(parts)


def all_profiles_text(rows: list[tuple[dict[str, Any], str, dict[str, Any], str]], kind: str) -> str:
    """(novel, status text, profile, last emotion) per series, shown under the series menu."""
    if not rows:
        return ""
    lines = [f"=== {singular(kind).title()} Profiles ==="]
    for novel, status, profile, emotion in rows:
        parts = [f"- {novel['name']} {status}"]
        if emotion:
            parts.append(f"last felt: {emotion}")
        if profile.get("previously_happened_summary"):
            parts.append(f"previously: {profile['previously_happened_summary'][:100]}...")
        if profile.get("last_read_summary"):
            parts.append(f"last read: {profile['last_read_summary'][:100]}...")
        lines.append(", ".join(parts))
    return "\n".join(lines)


# ~~~ while reading ~~~

def _elapsed(start: datetime) -> str:
    elapsed = (datetime.now() - start).total_seconds()
    hours, minutes = int(elapsed // 3600), int((elapsed % 3600) // 60)
    if hours > 0:
        return f"{hours} hour{'s' if hours != 1 else ''} {minutes} minute{'s' if minutes != 1 else ''}"
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


def reading_guidance(kind: str, profile: str, start: datetime | None = None, pages_read: int = 0,
                     start_volume: int | None = None, current_volume: int | None = None,
                     total_volumes: int | None = None, chapter: str | None = None) -> str:
    lines = []
    if start:
        lines.append(f"You've been reading for {_elapsed(start)}.")
    progress = []
    if pages_read > 0:
        progress.append(f"you've read {pages_read} page{'s' if pages_read != 1 else ''} this session")
    if start_volume and current_volume and current_volume > start_volume:
        done = current_volume - start_volume
        progress.append(f"you've completed {done} volume{'s' if done != 1 else ''} this session")
    if current_volume and total_volumes:
        progress.append(f"you're on volume {current_volume}/{total_volumes}")
    if chapter:
        progress.append(f"current chapter: {chapter}")
    if progress:
        lines.append("Reading progress: " + ", ".join(progress) + ".")
    head = "\n".join(lines)
    body = READING_GUIDANCE.format(a_one=a_one(kind), profile=profile)
    return f"{head}\n\n{body}" if head else body


def memory_block(name: str, memories: list[str]) -> str:
    if not memories:
        return ""
    return f"Things you remember from reading {name}:\n- " + "\n- ".join(memories)


def page_prompt(header: str, text: str, has_text: bool, has_image: bool, description: str = "") -> str:
    """One page as the agent sees it."""
    parts = [f"[{header}]"]
    if has_image and not has_text:
        parts.append("[This page is an illustration]")
    else:
        parts.append(text)
        if has_image:
            parts.append("[This page also contains an illustration]")
    if description:
        parts.append(f"[Illustration: {description}]")
    parts.append(f"\n{RETURN_REMINDER}")
    return "\n\n".join(parts)
