"""The page map: one line per volume saying what each page is.

It never stores page content, only a label per page (text, image,
illustrated_text, or metadata like a table of contents), so the reader knows
which pages to skip and which carry a chapter heading. Pages are numbered in
spine order, the same order epub.extract_page reads them in.

Each series folder gets a <series name>.jsonl. It is built once, at setup or
the first startup that finds the folder without one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from .epub import read_pages

METADATA_KEYWORDS = [
    "table of contents", "contents", "copyright", "all rights reserved",
    "published by", "isbn", "cover illustration", "interior design",
    "translator", "editor", "proofreader", "printing", "edition",
    "colophon", "about the author", "about the illustrator",
    "also available", "other titles", "bibliography", "index",
    "newsletter", "yen press",
    "j-novel", "seven seas", "kadokawa",
]

CHAPTER_PATTERNS = [
    re.compile(r"^chapter\s+\d+", re.IGNORECASE),
    re.compile(r"^ch\.\s*\d+", re.IGNORECASE),
    re.compile(r"^prologue$", re.IGNORECASE),
    re.compile(r"^epilogue$", re.IGNORECASE),
    re.compile(r"^interlude", re.IGNORECASE),
    re.compile(r"^act\s+\d+", re.IGNORECASE),
    re.compile(r"^part\s+\d+", re.IGNORECASE),
    re.compile(r"^intermission", re.IGNORECASE),
    re.compile(r"^final\s+chapter", re.IGNORECASE),
    re.compile(r"^side\s+story", re.IGNORECASE),
    re.compile(r"^extra\s+chapter", re.IGNORECASE),
    re.compile(r"^afterword", re.IGNORECASE),
    re.compile(r"^author'?s?\s+note", re.IGNORECASE),
]


# ~~~ classification ~~~

def classify_page(content: bytes, href: str) -> dict[str, Any]:
    soup = BeautifulSoup(content, "html.parser")
    text = soup.get_text(separator="\n", strip=True)
    text_len = len(text.strip())
    img_count = len(soup.find_all("img")) + len(soup.find_all("svg")) + len(soup.find_all("image"))

    meta = detect_metadata(soup, text, href, img_count)
    if meta:
        return {"type": "metadata", "kind": meta}

    if img_count > 0 and text_len < 100:
        return {"type": "image"}

    result: dict[str, Any] = {"type": "illustrated_text" if img_count > 0 else "text"}
    chapter = detect_chapter(soup, text)
    if chapter:
        result["chapter"] = chapter
    return result


def detect_chapter(soup: BeautifulSoup, text: str) -> str | None:
    for tag_name in ["h1", "h2", "h3", "title"]:
        for h in soup.find_all(tag_name):
            h_text = h.get_text(strip=True)
            for pattern in CHAPTER_PATTERNS:
                if pattern.search(h_text):
                    return h_text
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    for line in lines[:3]:
        for pattern in CHAPTER_PATTERNS:
            if pattern.search(line):
                return line
    return None


def detect_metadata(soup: BeautifulSoup, text: str, href: str, img_count: int = 0) -> str | None:
    text_lower = text.lower()
    href_lower = href.lower()

    if any(k in href_lower for k in ["toc", "contents", "nav"]):
        return "toc"
    if any(k in href_lower for k in ["copyright", "colophon", "imprint"]):
        return "copyright"
    # A cover with an image stays an image page, so the reader sees it.
    if "cover" in href_lower and len(text.strip()) < 30 and img_count == 0:
        return "cover"
    if "title" in href_lower and len(text.strip()) < 50 and img_count == 0:
        return "title_page"

    links = soup.find_all("a")
    if links and text.strip():
        link_text = sum(len(a.get_text(strip=True)) for a in links)
        if link_text / max(len(text.strip()), 1) > 0.6:
            return "toc"

    keyword_hits = sum(1 for kw in METADATA_KEYWORDS if kw in text_lower)
    if keyword_hits >= 3 and len(text.strip()) < 2000:
        if any(kw in text_lower for kw in ["copyright", "all rights reserved", "isbn"]):
            return "copyright"
        return "front_matter"

    if len(text.strip()) < 50 and img_count == 0:
        return "divider"

    return None


# ~~~ building ~~~

def process_epub(epub_path: Path) -> dict[str, Any]:
    """One volume's entry for the page map."""
    pages = []
    for num, (href, content) in enumerate(read_pages(epub_path), 1):
        info = classify_page(content, href)
        info["page"] = num
        pages.append(info)
    return {"volume": epub_path.stem, "total_pages": len(pages), "pages": pages}


def map_path(folder: Path) -> Path:
    return folder / f"{folder.name}.jsonl"


def build_page_map(folder: Path, say=print) -> bool:
    """Write the folder's page map. False when it has no volumes."""
    volumes = sorted(folder.glob("Volume_*.epub"))
    if not volumes:
        return False
    say(f"Mapping pages: {folder.name} ({len(volumes)} volume{'s' if len(volumes) != 1 else ''})")
    lines = []
    for volume in volumes:
        entry = process_epub(volume)
        entry["series"] = folder.name
        lines.append(json.dumps(entry, ensure_ascii=False))
    map_path(folder).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def build_missing(books_dir: Path, say=print) -> list[str]:
    """Map every series folder that has volumes but no page map yet."""
    built = []
    if not books_dir.exists():
        return built
    for folder in sorted(p for p in books_dir.iterdir() if p.is_dir()):
        if not map_path(folder).exists() and build_page_map(folder, say):
            built.append(folder.name)
    return built
