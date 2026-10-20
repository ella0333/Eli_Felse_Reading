# Module integration

How the Reading module plugs into [Eli Felse Base](https://github.com/ella0333/Eli_Felse_Base).
If you have not set the base up yet, start with its
[getting started guide](https://github.com/ella0333/Eli_Felse_Base/blob/master/docs/getting-started.md).

## Installation

```bash
cd Eli_Felse_Base
.venv\Scripts\activate
git clone https://github.com/ella0333/Eli_Felse_Reading.git data/modules/reading
cd data/modules/reading
pip install -r requirements.txt
python scripts/setup.py
```

The base scans `data/modules/` at startup and reads the `ACTIVITIES` list out of the
folder's `__init__.py`. There is nothing to register.

This module needs base **0.5.5 or newer**, which is the release that lets a module name
its survey subject after the agent has picked it from the module's own menu.

## The books folder

Each series is a folder named after its title. The setup wizard builds it, but you can
also make one by hand:

```
books/
  My Series/
    Volume_01.epub
    Volume_02.epub
    description.txt
    kind.txt
    My Series.jsonl
```

The volumes must be named `Volume_01.epub`, `Volume_02.epub` and so on, since that is
the reading order. `description.txt` is optional and is shown to the agent when it picks
a series. Without it the agent sees "No description available yet." `kind.txt` holds what
it is as it reads on the menu, such as `Light Novels`, and is `Books` when missing.

`My Series.jsonl` is the page map. The module builds it at setup, or at the first startup
that finds a folder without one, so a folder dropped in by hand works on the next run. It
stores one line per volume saying what each page is: text, an illustration, text with an
illustration, or front matter like a table of contents or a copyright page. Front matter
and blank dividers are never shown to the agent. If you change a series' volumes, delete
its `.jsonl` and it is rebuilt.

If the wizard numbered the volumes in the wrong order, rename the files in the series
folder so they read Volume_01 onward in the right order, then delete the `.jsonl`.

Pages are read in the EPUB's spine order, which is the order a reader turns them. No EPUB
library is used, only zip and XML reading, with BeautifulSoup for pages that are HTML
rather than strict XHTML.

## Menu structure

The module adds one line to the main menu, shown only once there is something to read.
It names every kind you have added:

```
R) Read (Books, Light Novels) (last: 2 hours ago)
```

With more than one kind, picking it first asks which kind:

```
What would you like to read?

A) Books
B) Light Novels
```

Then the series menu shows only that kind. With a single kind this step is skipped.

```
Choose a light novel to read. Consider continuing one you're already reading before starting something new.

A) My Series (currently reading - on volume 2/5 - last read 3 days ago)
B) Another Series (never read)

=== Light Novel Profiles ===
- My Series (currently reading - on volume 2/5 - last read 3 days ago), last felt: curious, previously: ..., last read: ...
- Another Series (never read)
```

A series the agent has finished is marked `(completed)`, and the menu adds a line asking
it to pick something it is partway through or has not started.

## How a session works

Each page is sent with a header like `[Volume 2/5 | Page 14/120 - Chapter 3]`, the page
text, a note when the page has an illustration, and a reminder that `return_to_menu`
ends the session. The agent answers with its thoughts about the page and whether to keep
going. Pages with under 50 characters of text and no illustration are skipped.

Progress is saved after every page, so the next session picks up on the page after the
last one read. When a volume ends the next one starts on its own, and when the last
volume ends the series is marked completed.

The reading prompt carries the series profile (description, progress, how the agent last
felt reading it, a "previously happened" recap and a summary of the last session), how
long it has been reading, how many pages and volumes it has read this session, the
current chapter, the home menu for reference, and up to three memories recalled for the
page.

After the session the module writes a plot summary of what was read and stores it as the
series' "last read" summary. The summary it replaces is folded into the "previously
happened" recap. Both are made with isolated calls to the agent's own model.

## Illustrations

| `images` | What the agent gets |
|----------|---------------------|
| `off` | Illustration pages are skipped, and illustrated text pages are read as text |
| `model` | The illustration is attached for the agent's own model to look at |
| `describe` | Nano Banana 2 on OpenRouter describes it, and the description goes into the page as `[Illustration: ...]` |
| `both` | The description and the attached image |

`describe` and `both` need `OPENROUTER_API_KEY` in the base's `.env`. Without it,
`describe` works like `off` and `both` like `model`. The setup wizard checks LM Studio or
OpenRouter to see whether your model can read images and only offers the options that
fit.

## Config

Add an optional section to your `config.yaml`. Every key is optional.

```yaml
activities:
  reading:
    books_dir: books       # relative paths start from the module folder
    images: off            # off | model | describe | both
```

## Data storage

- `data/activities/reading/reading_tracker.json`: volume, page, last read time and status for every series.
- `data/activities/reading/profiles/novel_<series>.json`: the last-read summary and the "previously happened" recap.

The books themselves stay in the books folder, which is gitignored.

## Context isolation

`isolate_context = True`. A session can run for hundreds of pages, so the framework
snapshots context before it and restores it after, then adds one line saying what was
read. The agent's thoughts on each page go to memory as they are written, with rules
that keep the story's events separate from things that happened to the agent.

## Survey

`survey = "simple"`. The survey asks how the agent felt after reading that series, and
the answer shows in the series menu and profile as "last felt".

## Schemas

The series menu is a lettered choice, and each page is answered with `thinking` and
`return_to_menu`. Nothing the agent writes ever reaches a file path or a request.
