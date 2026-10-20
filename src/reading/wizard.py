"""Setup wizard steps shared by scripts/setup.py and standalone_setup.py.

The IO helpers mirror the base's WizardIO. The image step is the Reddit
module's, with "image posts" read as illustrations.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from . import pagemap
from .illustrations import API_KEY_ENV, VISION_MODEL
from .library import BUILT_IN_KINDS, KIND_FILE, folder_kind, kind_key, tidy_kind

_BAD_NAME_CHARS = re.compile(r'[<>:"/\\|?*]')


# ~~~ IO helpers (mirrors base WizardIO) ~~~

def say(msg: str) -> None:
    print(f"[system] {msg}")


def text(prompt: str, default: str = "", show_default: bool = True) -> str:
    suffix = f" ({default})" if default and show_default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def choice(prompt: str, options: list[str], default: int = 1) -> int:
    say(prompt)
    for i, option in enumerate(options, 1):
        say(f"  {i}. {option}")
    while True:
        answer = input(f"choose 1-{len(options)} ({default}): ").strip()
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer)
        say(f"please enter a number from 1 to {len(options)}")


def yesno(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        answer = input(f"{prompt} ({hint}): ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        say("please answer y or n")


def clean_path(answer: str) -> Path:
    """A pasted path, with the quotes Windows adds on "Copy as path" removed."""
    return Path(answer.strip().strip('"').strip("'")).expanduser()


# ~~~ books ~~~

def _natural_key(path: Path) -> list:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def series_folders(books_dir: Path) -> list[str]:
    if not books_dir.exists():
        return []
    return sorted(p.name for p in books_dir.iterdir() if p.is_dir() and any(p.glob("Volume_*.epub")))


def _ask_epubs() -> list[Path]:
    while True:
        source = clean_path(text("Folder path for this book or series"))
        if source.is_file() and source.suffix.lower() == ".epub":
            return [source]
        if source.is_dir():
            epubs = sorted((p for p in source.iterdir() if p.suffix.lower() == ".epub"), key=_natural_key)
            if epubs:
                return epubs
            say(f"no .epub files in {source}")
        else:
            say(f"{source} doesn't exist")


def ask_kind(books_dir: Path) -> str:
    """Books, Light Novels, any kind typed in before, or a new one."""
    kinds = list(BUILT_IN_KINDS)
    if books_dir.exists():
        for folder in sorted(p for p in books_dir.iterdir() if p.is_dir()):
            kind = folder_kind(folder)
            if kind_key(kind) not in {kind_key(k) for k in kinds}:
                kinds.append(kind)
    picked = choice("What kind is it?", [*kinds, "Something else"])
    if picked <= len(kinds):
        return kinds[picked - 1]
    while True:
        kind = tidy_kind(text("What is it, as it should read on the menu"))
        if kind:
            # A different spelling of one already listed is that one.
            return next((k for k in kinds if kind_key(k) == kind_key(kind)), kind)


def add_series(books_dir: Path) -> str | None:
    """One book or series: title, description, its EPUBs. None when the title is blank."""
    while True:
        title = _BAD_NAME_CHARS.sub("", text("Title of the book or series")).strip().rstrip(".")
        if not title:
            return None
        if (books_dir / title).exists():
            say(f"'{title}' is already in the books folder")
            continue
        break
    description = text("Description, shown to the agent when it picks what to read")
    kind = ask_kind(books_dir)
    epubs = _ask_epubs()

    folder = books_dir / title
    folder.mkdir(parents=True)
    for i, epub in enumerate(epubs, 1):
        volume = f"Volume_{i:02d}"
        shutil.copy2(epub, folder / f"{volume}.epub")
        say(f"  {volume}  {epub.name}")
    if description:
        (folder / "description.txt").write_text(description + "\n", encoding="utf-8")
    (folder / KIND_FILE).write_text(kind + "\n", encoding="utf-8")
    pagemap.build_page_map(folder, say=lambda _msg: None)
    say(f"saved {len(epubs)} volume{'s' if len(epubs) != 1 else ''}")
    return title


def ask_books(books_dir: Path) -> None:
    books_dir.mkdir(parents=True, exist_ok=True)
    existing = series_folders(books_dir)
    if existing:
        say(f"Already in the books folder: {', '.join(existing)}")
    say("See 'Adding books' in the README for how to save your books before adding them.")
    if yesno("Add a book or series now?", default=not existing):
        while add_series(books_dir) and yesno("Add another book or series?", default=False):
            pass
    for name in pagemap.build_missing(books_dir, say=lambda _msg: None):
        say(f"mapped the pages of {name}")


# ~~~ can the agent's own model read images? ~~~

def _get_json(url: str):
    try:
        with urlopen(Request(url, headers={"User-Agent": "elifelse-reading-setup"}), timeout=10) as resp:
            return json.loads(resp.read().decode())
    except (URLError, ValueError, OSError):
        return None


def model_sees_images(base_url: str, model: str) -> bool | None:
    """True/False when OpenRouter or LM Studio says so, None when it can't be told."""
    if not model:
        return None
    if "openrouter.ai" in base_url:
        data = _get_json("https://openrouter.ai/api/v1/models") or {}
        for m in data.get("data", []):
            if m.get("id") == model:
                return "image" in (m.get("architecture", {}).get("input_modalities") or [])
        return None
    # LM Studio lists each model with type "vlm" (vision) or "llm".
    root = base_url.rstrip("/").removesuffix("/v1")
    data = _get_json(f"{root}/api/v0/models") or {}
    for m in data.get("data", []):
        if m.get("id") == model:
            return m.get("type") == "vlm"
    return None


def write_env_key(path: Path, api_key: str) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if API_KEY_ENV in existing:
        say(f"{path} already has {API_KEY_ENV}, leaving it unchanged")
        return
    with path.open("a", encoding="utf-8") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(f"{API_KEY_ENV}={api_key}\n")


def ask_images(base_url: str, model: str, env_path: Path) -> str:
    """off / model / describe / both, and the OpenRouter key when describing."""
    if not yesno("Show illustrations to the agent? Answering no skips illustration pages", default=True):
        return "off"

    sees = model_sees_images(base_url, model)
    if sees is None:
        sees = yesno("Can your agent's model read images?", default=False)
    else:
        say(f"Your model ({model}) {'can' if sees else 'cannot'} read images.")

    nano = f"Nano Banana 2 ({VISION_MODEL}) on OpenRouter turns each image into a short text description, at minimal cost"
    if sees:
        picked = choice("How should the agent see them?", [
            "Your model looks at the images itself",
            nano,
            "Both, which can help a smaller model",
        ])
        mode = ["model", "describe", "both"][picked - 1]
    else:
        say(f"{nano}.")
        mode = "describe"

    if mode != "model" and not os.environ.get(API_KEY_ENV):
        api_key = text("OpenRouter API key")
        if api_key:
            write_env_key(env_path, api_key)
        elif mode == "both":
            say("no key, so your model will look at the images without a description")
            mode = "model"
        else:
            say("no key, so illustration pages will be skipped")
            mode = "off"
    return mode
