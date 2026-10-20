"""Reading an EPUB in spine order, the order a reader turns its pages.

The page map and the page text both come from here, so the two always agree
about which file is page 12. No EPUB library is needed, only zip and XML
reading, with BeautifulSoup for pages that are HTML rather than XHTML.
"""

from __future__ import annotations

import base64
import posixpath
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

OPF_NS = "{http://www.idpf.org/2007/opf}"
CONTAINER_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
DOCUMENT_TYPES = ("application/xhtml+xml", "text/html")
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


def _find_opf(zf: zipfile.ZipFile) -> str | None:
    for name in zf.namelist():
        if name.endswith(".opf"):
            return name
    try:
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        rootfile = container.find(".//c:rootfile", CONTAINER_NS)
        if rootfile is not None:
            return rootfile.get("full-path")
    except (KeyError, ET.ParseError):
        pass
    return None


def spine(zf: zipfile.ZipFile) -> list[str]:
    """Paths inside the zip of every page document, in reading order."""
    opf_path = _find_opf(zf)
    if not opf_path:
        return []
    opf_tree = ET.fromstring(zf.read(opf_path))
    opf_dir = posixpath.dirname(opf_path)
    manifest = {}
    for item in opf_tree.iter(f"{OPF_NS}item"):
        href = unquote(item.get("href", ""))
        manifest[item.get("id")] = {
            "href": posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else href,
            "media_type": item.get("media-type", ""),
        }
    pages = []
    for itemref in opf_tree.iter(f"{OPF_NS}itemref"):
        entry = manifest.get(itemref.get("idref"))
        if entry and entry["media_type"] in DOCUMENT_TYPES:
            pages.append(entry["href"])
    return pages


def read_pages(epub_path: Path) -> list[tuple[str, bytes]]:
    """(path, raw content) of every page document, in reading order."""
    with zipfile.ZipFile(str(epub_path), "r") as zf:
        return [(path, zf.read(path)) for path in spine(zf)]


def _resolve_img_path(img_src: str, page_path: str) -> str:
    img_src = unquote(img_src.split("#")[0])
    if img_src.startswith("/"):
        return posixpath.normpath(img_src.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(page_path), img_src))


def _find_img_src(soup: BeautifulSoup) -> str | None:
    for tag in soup.find_all(["img", "image"]):
        src = tag.get("src") or tag.get("xlink:href") or tag.get(XLINK_HREF) or tag.get("href")
        if src:
            return src
    return None


def _encode_image(data: bytes, img_path: str) -> str:
    lower = img_path.lower()
    if lower.endswith(".png"):
        mime = "image/png"
    elif lower.endswith(".webp"):
        mime = "image/webp"
    else:
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(data).decode('utf-8')}"


def _extract_text(content: bytes) -> str:
    """Every text run on the page, one paragraph each."""
    try:
        tree = ET.fromstring(content)
    except ET.ParseError:
        # Pages with HTML entities like &nbsp; are not valid XML.
        soup = BeautifulSoup(content, "html.parser")
        for tag in soup(["style", "script"]):
            tag.decompose()
        return "\n\n".join(soup.get_text(separator="\n", strip=True).splitlines())
    parts = []
    for elem in tree.iter():
        if elem.text and elem.text.strip():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag not in ("style", "script"):
                parts.append(elem.text.strip())
        if elem.tail and elem.tail.strip():
            parts.append(elem.tail.strip())
    return "\n\n".join(parts)


def extract_page(epub_path: Path, page_num: int) -> dict[str, Any]:
    """Text and first image of one page (1-based, spine order).

    Always extracts both. The page map's type only decides how the page is
    presented, so extraction stays complete either way."""
    if not epub_path.exists():
        return {"text": f"[Error: {epub_path.name} not found]"}
    try:
        with zipfile.ZipFile(str(epub_path), "r") as zf:
            pages = spine(zf)
            if not pages:
                return {"text": "[Error: could not find OPF in EPUB]"}
            if page_num < 1 or page_num > len(pages):
                return {"text": f"[Error: page {page_num} out of range]"}
            page_path = pages[page_num - 1]
            content = zf.read(page_path)
            result: dict[str, Any] = {}

            try:
                text = _extract_text(content)
                if text:
                    result["text"] = text
            except Exception as e:
                result["text"] = f"[Error extracting text: {e}]"

            try:
                img_src = _find_img_src(BeautifulSoup(content, "html.parser"))
                if img_src:
                    img_path = _resolve_img_path(img_src, page_path)
                    result["image_base64"] = _encode_image(zf.read(img_path), img_path)
            except Exception:
                pass

            return result if result else {"text": "[Could not extract page content]"}
    except Exception as e:
        return {"text": f"[Error reading EPUB: {e}]"}
