"""The books folder and reading progress.

Each series is a folder named after its title:

    books/
      My Series/
        Volume_01.epub
        Volume_02.epub
        description.txt
        kind.txt             (what it is, e.g. Light Novels; Books when missing)
        My Series.jsonl      (the page map, built automatically)

Progress for every series (volume, page, last read, status) lives in one
tracker file kept with the activity's own data, not in the books folder.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from . import pagemap
from .epub import extract_page

try:
    from elifelse.textutils import format_time_ago
except ImportError:
    def format_time_ago(seconds: float) -> str:
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            m = int(seconds // 60)
            return f"{m} minute{'s' if m != 1 else ''} ago"
        if seconds < 86400:
            h = int(seconds // 3600)
            return f"{h} hour{'s' if h != 1 else ''} ago"
        d = int(seconds // 86400)
        return "yesterday" if d == 1 else f"{d} days ago"

READABLE_TYPES = ("text", "image", "illustrated_text")
NO_DESCRIPTION = "No description available yet."
DEFAULT_KIND = "Books"
BUILT_IN_KINDS = ("Books", "Light Novels")
KIND_FILE = "kind.txt"


def make_key(name: str) -> str:
    return name.lower().replace(" ", "_").replace("-", "_").replace("'", "")


def tidy_kind(kind: str) -> str:
    """'light novel' -> 'Light Novel': each word capitalized, extra spaces dropped."""
    return " ".join(w[:1].upper() + w[1:].lower() for w in kind.split())


def kind_key(kind: str) -> str:
    """What counts as the same kind: 'Light Novels', 'light novel' and
    'lightnovel' all give 'lightnovel'."""
    key = "".join(ch for ch in kind.lower() if ch.isalnum())
    return key[:-1] if key.endswith("s") and not key.endswith("ss") else key


def folder_kind(folder: Path) -> str:
    """What the folder holds, as listed on the menu: 'Light Novels', 'Books'."""
    try:
        kind = tidy_kind((folder / KIND_FILE).read_text(encoding="utf-8")) or DEFAULT_KIND
    except OSError:
        return DEFAULT_KIND
    return next((k for k in BUILT_IN_KINDS if kind_key(k) == kind_key(kind)), kind)


def singular(kind: str) -> str:
    """'Light Novels' -> 'light novel', for use inside a sentence."""
    word = kind.strip().lower()
    return word[:-1] if word.endswith("s") and not word.endswith("ss") else word


class Library:
    def __init__(self, books_dir: Path, tracker_path: Path, say=print) -> None:
        self.books_dir = books_dir
        self.tracker_path = tracker_path
        self.say = say
        self.books_dir.mkdir(parents=True, exist_ok=True)
        self.tracker = self._load_tracker()

    # ~~~ tracker ~~~

    def _load_tracker(self) -> dict[str, Any]:
        if self.tracker_path.exists():
            try:
                return json.loads(self.tracker_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"novels": {}}

    def _save_tracker(self) -> None:
        self.tracker_path.parent.mkdir(parents=True, exist_ok=True)
        self.tracker_path.write_text(json.dumps(self.tracker, indent=2, ensure_ascii=False), encoding="utf-8")

    def sync(self) -> None:
        """Map any new folders, then add or refresh every series in the tracker."""
        pagemap.build_missing(self.books_dir, self.say)
        for folder in sorted(p for p in self.books_dir.iterdir() if p.is_dir()):
            if not pagemap.map_path(folder).exists():
                continue
            volumes = sorted(folder.glob("Volume_*.epub"))
            if not volumes:
                continue
            key = make_key(folder.name)
            novels = self.tracker["novels"]
            if key not in novels:
                novels[key] = {
                    "name": folder.name,
                    "folder": str(folder),
                    "current_volume": 1,
                    "current_page": 1,
                    "total_volumes": len(volumes),
                    "last_read": None,
                    "status": "never_read",
                }
                self.say(f"New series discovered: {folder.name} ({len(volumes)} volumes)")
            else:
                novels[key]["total_volumes"] = len(volumes)
                novels[key]["folder"] = str(folder)
        self._save_tracker()

    # ~~~ reading the list ~~~

    def available(self, kind: str = "") -> list[dict[str, Any]]:
        """Series whose folder is still there, sorted by name, optionally of one kind."""
        out = []
        for key, data in sorted(self.tracker["novels"].items(), key=lambda x: x[1]["name"]):
            folder = Path(data["folder"])
            if folder.is_dir() and (not kind or kind_key(folder_kind(folder)) == kind_key(kind)):
                out.append({"key": key, **data, "kind": folder_kind(folder)})
        return out

    def kinds(self) -> list[str]:
        """Every kind on the shelf, in the order first seen. Spellings of the
        same kind ('Light Novels', 'light novel') count once, as first seen."""
        seen: dict[str, str] = {}
        for novel in self.available():
            seen.setdefault(kind_key(novel["kind"]), novel["kind"])
        return list(seen.values())

    def get(self, key: str) -> dict[str, Any] | None:
        data = self.tracker["novels"].get(key)
        if not data:
            return None
        return {"key": key, **data, "kind": folder_kind(Path(data["folder"]))}

    def status_text(self, key: str) -> str:
        novel = self.tracker["novels"].get(key)
        if not novel:
            return "unknown"
        if novel["status"] == "never_read":
            return "(never read)"
        if novel["status"] == "completed":
            return f"(completed - you've already read all {novel['total_volumes']} volumes)"
        time_ago = "unknown time ago"
        if novel.get("last_read"):
            try:
                seconds = (datetime.now() - datetime.fromisoformat(novel["last_read"])).total_seconds()
                time_ago = format_time_ago(seconds)
            except (ValueError, TypeError):
                pass
        return (f"(currently reading - on volume {novel['current_volume']}/{novel['total_volumes']}"
                f" - last read {time_ago})")

    def description(self, key: str) -> str:
        novel = self.tracker["novels"].get(key)
        if not novel:
            return "No description available."
        desc_file = Path(novel["folder"]) / "description.txt"
        if desc_file.exists():
            try:
                return desc_file.read_text(encoding="utf-8").strip() or NO_DESCRIPTION
            except OSError:
                pass
        return NO_DESCRIPTION

    # ~~~ progress ~~~

    def update_progress(self, key: str, volume: int, page: int) -> None:
        novel = self.tracker["novels"].get(key)
        if novel:
            novel["current_volume"] = volume
            novel["current_page"] = page
            novel["last_read"] = datetime.now().isoformat()
            novel["status"] = "reading"
            self._save_tracker()

    def mark_completed(self, key: str) -> None:
        novel = self.tracker["novels"].get(key)
        if novel:
            novel["status"] = "completed"
            novel["last_read"] = datetime.now().isoformat()
            self._save_tracker()
            self.say(f"Series marked as completed: {novel['name']}")

    # ~~~ pages ~~~

    def volume_pages(self, key: str, volume_num: int) -> list[dict[str, Any]]:
        """The readable pages of one volume, from the page map."""
        novel = self.tracker["novels"].get(key)
        if not novel:
            return []
        path = pagemap.map_path(Path(novel["folder"]))
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        volume_key = f"Volume_{volume_num:02d}"
        for line in lines:
            try:
                vol = json.loads(line.strip())
            except json.JSONDecodeError:
                continue
            if vol.get("volume") == volume_key:
                return [p for p in vol.get("pages", []) if p.get("type") in READABLE_TYPES]
        return []

    def page_content(self, key: str, volume_num: int, page_info: dict[str, Any]) -> dict[str, Any]:
        novel = self.tracker["novels"].get(key)
        if not novel:
            return {"text": "[Error: novel not found]"}
        epub_path = Path(novel["folder"]) / f"Volume_{volume_num:02d}.epub"
        return extract_page(epub_path, page_info["page"])
