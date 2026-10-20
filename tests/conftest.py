"""Fixtures, mirroring the base's own tests/conftest.py.

The module is a drop-in folder rather than an installed package, so the repo
root goes on sys.path the same way standalone.py reaches it. No real book is
shipped: make_epub writes a tiny one into a temporary folder.
"""

import base64
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from elifelse.app import App  # noqa: E402
from elifelse.config import Config  # noqa: E402
from elifelse.persona import Persona  # noqa: E402
from elifelse.providers.mock import MockProvider  # noqa: E402

from src.reading.activity import ReadingActivity  # noqa: E402

# A 1x1 PNG, enough to count as an illustration.
PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

LONG = "The lantern swung in the wind as the travelers climbed the hill road toward the old mill. " * 2


def page(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>p</title></head>'
        f"<body>{body}</body></html>"
    )


def make_epub(path: Path, pages: list[tuple[str, str]]) -> Path:
    """An EPUB whose spine lists `pages` (file name, xhtml) in order.

    The manifest lists them in reverse, so a reader that follows the
    manifest instead of the spine gets the order wrong."""
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = "".join(
        f'<item id="p{i}" href="text/{name}" media-type="application/xhtml+xml"/>'
        for i, (name, _) in reversed(list(enumerate(pages)))
    )
    manifest += '<item id="img" href="images/pic.png" media-type="image/png"/>'
    spine = "".join(f'<itemref idref="p{i}"/>' for i in range(len(pages)))
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        f"<manifest>{manifest}</manifest><spine>{spine}</spine></package>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/images/pic.png", PIXEL)
        for name, xhtml in pages:
            zf.writestr(f"OEBPS/text/{name}", xhtml)
    return path


def standard_volume(path: Path, chapter: str = "Chapter 1") -> Path:
    """Copyright page, a chapter opening, an illustration, a short divider, more text."""
    return make_epub(path, [
        ("copyright.xhtml", page("<p>Copyright 2026. All rights reserved.</p>")),
        ("c1.xhtml", page(f"<h1>{chapter}</h1><p>{LONG}</p>")),
        ("art.xhtml", page('<img src="../images/pic.png"/>')),
        ("c2.xhtml", page(f"<p>{LONG} Later that night.</p>")),
    ])


@pytest.fixture
def books(tmp_path):
    """A books folder with one two-volume series and its description."""
    folder = tmp_path / "books" / "The Lantern Road"
    standard_volume(folder / "Volume_01.epub")
    standard_volume(folder / "Volume_02.epub", chapter="Chapter 5")
    (folder / "description.txt").write_text("Two travelers and a lantern.", encoding="utf-8")
    return tmp_path / "books"


@pytest.fixture
def config(tmp_path, books):
    cfg = Config()
    cfg.data_dir = str(tmp_path / "data")
    cfg.provider.kind = "mock"
    cfg.provider.response_delay_min = 0
    cfg.provider.response_delay_max = 0
    cfg.memory.enabled = False
    cfg.day_cycle.enabled = False
    cfg.activities["reading"] = {"books_dir": str(books), "images": "model"}
    return cfg


@pytest.fixture
def mock_provider(config):
    return MockProvider(config)


@pytest.fixture
def persona():
    return Persona(name="Testa", pronouns="they/them", personality="A test persona.")


@pytest.fixture
def app(config, persona, mock_provider):
    return App(config, persona, provider=mock_provider)


@pytest.fixture
def reading(app):
    app.registry.register(ReadingActivity)
    activity = app.registry.get("reading")
    return activity, app.registry.ctx_for(activity)
