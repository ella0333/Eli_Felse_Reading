"""The page map and page reading are pure, so they are tested directly on a
tiny generated EPUB. The activity is driven end to end on the MockProvider,
with answers scripted as a real model would send them. No network calls."""

import json

from conftest import LONG, make_epub, page

from src.reading import pagemap
from src.reading.epub import extract_page
from src.reading.illustrations import Illustrations, image_mode
from src.reading.library import Library
from src.reading.prompts import page_prompt

SERIES = "The Lantern Road"
KEY = "the_lantern_road"


def read(more: bool = True, thinking: str = "I like the lantern.") -> dict:
    return {"thinking": thinking, "return_to_menu": not more}


def library(books, tmp_path) -> Library:
    lib = Library(books, tmp_path / "tracker.json", say=lambda _m: None)
    lib.sync()
    return lib


def page_prompts(mock_provider) -> list[str]:
    """The page prompts the model was sent, in order."""
    out = []
    for call in mock_provider.calls:
        last = call["messages"][-1]["content"]
        text = last if isinstance(last, str) else last[0]["text"]
        if text.startswith("[Volume "):
            out.append(text)
    return out


# ~~~ the page map ~~~
def test_pages_are_numbered_in_spine_order_not_manifest_order(tmp_path):
    epub = make_epub(tmp_path / "Volume_01.epub", [
        ("first.xhtml", page("<h1>Prologue</h1><p>" + LONG + "</p>")),
        ("second.xhtml", page("<p>" + LONG + " Second.</p>")),
    ])
    entry = pagemap.process_epub(epub)
    assert [p["page"] for p in entry["pages"]] == [1, 2]
    assert entry["pages"][0]["chapter"] == "Prologue"
    assert extract_page(epub, 2)["text"].endswith("Second.")


def test_pages_are_labelled(books):
    entry = pagemap.process_epub(books / SERIES / "Volume_01.epub")
    assert [p["type"] for p in entry["pages"]] == ["metadata", "text", "image", "text"]
    assert entry["pages"][1]["chapter"] == "Chapter 1"


def test_an_illustration_page_extracts_its_image(books):
    content = extract_page(books / SERIES / "Volume_01.epub", 3)
    assert content["image_base64"].startswith("data:image/png;base64,")


def test_sync_maps_new_folders_and_finds_the_series(books, tmp_path):
    lib = library(books, tmp_path)
    assert pagemap.map_path(books / SERIES).exists()
    novel = lib.get(KEY)
    assert novel["total_volumes"] == 2 and novel["status"] == "never_read"
    assert lib.status_text(KEY) == "(never read)"
    assert lib.description(KEY) == "Two travelers and a lantern."
    # The copyright page is never served.
    assert [p["page"] for p in lib.volume_pages(KEY, 1)] == [2, 3, 4]


def test_a_missing_description_says_so(books, tmp_path):
    (books / SERIES / "description.txt").unlink()
    assert library(books, tmp_path).description(KEY) == "No description available yet."


# ~~~ how a page is shown ~~~
def test_page_prompt_shapes():
    text_page = page_prompt("Volume 1/2 | Page 1/3", "Words.", True, False)
    assert text_page.startswith("[Volume 1/2 | Page 1/3]\n\nWords.")
    assert "return_to_menu: true" in text_page
    art = page_prompt("h", "", False, True)
    assert "[This page is an illustration]" in art
    both = page_prompt("h", "Words.", True, True, description="A lantern on a hill.")
    assert "[This page also contains an illustration]" in both
    assert "[Illustration: A lantern on a hill.]" in both


def test_image_settings():
    assert image_mode("both") == "both"
    assert image_mode(False) == "off"
    assert image_mode("nonsense") == "off"
    assert not Illustrations("describe", "").shown  # no key, nothing to describe with
    assert Illustrations("describe", "key").describe
    assert Illustrations("model").attach


# ~~~ the activity ~~~
async def test_reading_picks_up_where_it_left_off(reading, mock_provider, app):
    activity, ctx = reading
    await activity.startup(ctx)
    mock_provider.feed({"thinking": "t", "choice": "A"}, read(), read(more=False))

    await activity.run(ctx)

    shown = page_prompts(mock_provider)
    assert shown[0].startswith("[Volume 1/2 | Page 1/3 - Chapter 1]")
    assert shown[1].startswith("[Volume 1/2 | Page 2/3 - Chapter 1]")
    assert "[This page is an illustration]" in shown[1]
    novel = activity._library.get(KEY)
    assert (novel["current_volume"], novel["current_page"], novel["status"]) == (1, 4, "reading")

    mock_provider.calls.clear()
    mock_provider.feed({"thinking": "t", "choice": "A"}, read(more=False))
    await activity.run(ctx)
    # The chapter name only comes from its own page, as in the local loop.
    assert page_prompts(mock_provider)[0].startswith("[Volume 1/2 | Page 3/3]")


async def test_the_thinking_goes_into_the_context(reading, mock_provider, app):
    activity, ctx = reading
    mock_provider.feed({"thinking": "t", "choice": "A"}, read(more=False, thinking="What a hill."))
    await activity.run(ctx)
    assert any("What a hill." in str(m["content"]) for m in app.provider.context.messages)


async def test_the_illustration_is_attached_for_the_model(reading, mock_provider):
    activity, ctx = reading
    mock_provider.feed({"thinking": "t", "choice": "A"}, read(), read(more=False))
    await activity.run(ctx)
    content = mock_provider.calls[2]["messages"][-1]["content"]
    assert isinstance(content, list) and content[1]["type"] == "image_url"


async def test_with_images_off_the_illustration_page_is_skipped(reading, mock_provider, app):
    activity, ctx = reading
    app.config.activities["reading"]["images"] = "off"
    mock_provider.feed({"thinking": "t", "choice": "A"}, read(), read(more=False))
    await activity.run(ctx)
    shown = page_prompts(mock_provider)
    assert shown[1].startswith("[Volume 1/2 | Page 3/3")
    assert not any("illustration" in s for s in shown)


async def test_a_description_goes_into_the_page(reading, mock_provider, app, monkeypatch):
    activity, ctx = reading
    app.config.activities["reading"].update({"images": "describe", "OPENROUTER_API_KEY": "test"})

    async def fake_describe(self, data_uri, book):
        return "A lantern on a hill."

    monkeypatch.setattr(Illustrations, "describe_image", fake_describe)
    mock_provider.feed({"thinking": "t", "choice": "A"}, read(), read(more=False))
    await activity.run(ctx)
    shown = page_prompts(mock_provider)
    assert "[Illustration: A lantern on a hill.]" in shown[1]
    assert isinstance(mock_provider.calls[2]["messages"][-1]["content"], str)  # nothing attached


async def test_the_next_volume_starts_and_the_last_one_completes(reading, mock_provider):
    activity, ctx = reading
    mock_provider.feed({"thinking": "t", "choice": "A"}, read(), read(), read(), read(), read(), read())
    note = await activity.run(ctx)
    shown = page_prompts(mock_provider)
    assert shown[3].startswith("[Volume 2/2 | Page 1/3 - Chapter 5]")
    assert activity._library.get(KEY)["status"] == "completed"
    assert note == f"Reading session ended for {SERIES}."


async def test_the_series_menu_shows_progress(reading, mock_provider):
    activity, ctx = reading
    mock_provider.feed({"thinking": "t", "choice": "A"}, read(more=False))
    await activity.run(ctx)
    mock_provider.calls.clear()
    mock_provider.feed({"thinking": "t", "choice": "A"}, read(more=False))
    await activity.run(ctx)
    menu = mock_provider.calls[0]["messages"][-1]["content"]
    assert f"A) {SERIES} (currently reading - on volume 1/2 - last read just now)" in menu
    assert "=== Book Profiles ===" in menu
    assert menu.startswith("Choose a book to read.")
    assert "last read: Mock response." in menu


async def test_a_plot_summary_is_kept_per_series(reading, mock_provider, app):
    activity, ctx = reading
    for _ in range(2):
        mock_provider.feed({"thinking": "t", "choice": "A"}, read(more=False))
        await activity.run(ctx)
    profile = json.loads((ctx.data_dir / "profiles" / f"novel_{KEY}.json").read_text(encoding="utf-8"))
    assert profile["last_read_summary"] == "Mock response."
    assert profile["previously_happened_summary"] == "Mock response."


async def test_the_survey_names_the_series(app, mock_provider, reading):
    from elifelse.innerlife.system import InnerLife
    from elifelse.loop.lifecycle import run_activity

    activity, _ctx = reading
    app.innerlife = InnerLife(mock_provider, app.schemas, app.paths, app.clock)
    mock_provider.feed({"thinking": "t", "choice": "A"}, read(more=False))
    await run_activity(app, activity)
    record = json.loads(app.innerlife.surveys_file.read_text(encoding="utf-8").splitlines()[-1])
    assert record["subject"] == f"reading {SERIES}"


async def test_no_books_hides_the_menu_line(reading, books):
    import shutil

    activity, ctx = reading
    shutil.rmtree(books / SERIES)
    assert activity.available(ctx) is False


async def test_the_menu_line_names_the_kinds(reading, books):
    activity, ctx = reading
    await activity.startup(ctx)
    assert activity.get_menu_label(ctx) == "Read (Books)"
    (books / SERIES / "kind.txt").write_text("Light Novels\n", encoding="utf-8")
    assert activity.get_menu_label(ctx) == "Read (Light Novels)"


async def test_more_than_one_kind_asks_which_first(reading, books, mock_provider):
    from conftest import standard_volume

    activity, ctx = reading
    (books / SERIES / "kind.txt").write_text("Light Novels\n", encoding="utf-8")
    standard_volume(books / "A Quiet Book" / "Volume_01.epub")
    activity._ensure_library(ctx).sync()
    assert activity.get_menu_label(ctx) == "Read (Books, Light Novels)"

    mock_provider.feed({"thinking": "t", "choice": "B"}, {"thinking": "t", "choice": "A"}, read(more=False))
    await activity.run(ctx)
    kind_menu = mock_provider.calls[0]["messages"][-1]["content"]
    assert kind_menu.startswith("What would you like to read?")
    series_menu = mock_provider.calls[1]["messages"][-1]["content"]
    assert series_menu.startswith("Choose a light novel to read.")
    assert f"A) {SERIES}" in series_menu and "A Quiet Book" not in series_menu
    assert "You are currently reading a light novel." in mock_provider.calls[2]["messages"][0]["content"]


def test_spellings_of_one_kind_count_once(books, tmp_path):
    from conftest import standard_volume

    from src.reading.library import tidy_kind

    assert tidy_kind("  light   novel ") == "Light Novel"
    (books / SERIES / "kind.txt").write_text("Light Novels\n", encoding="utf-8")
    for name, kind in (("Second", "light novel"), ("Third", "lightnovel")):
        standard_volume(books / name / "Volume_01.epub")
        (books / name / "kind.txt").write_text(kind, encoding="utf-8")
    lib = library(books, tmp_path)
    assert lib.kinds() == ["Light Novels"]
    assert len(lib.available("Light Novels")) == 3
