# Eli Felse | Reading

[![MIT License](https://img.shields.io/badge/MIT_License-869b9f?style=flat)](LICENSE)

!["Eli" Chibi Character with grey hair and green eyes, and grey x hair clip, reading a book](https://i.imgur.com/noWvnFM.png)

**A module for [Eli Felse Base](https://github.com/ella0333/Eli_Felse_Base) that lets
an AI agent read light novels, or any other EPUB, one page at a time, thinking about
each page as it goes and picking up where it left off next time.**

## Quick start

```bash
cd Eli_Felse_Base
.venv\Scripts\activate        # Windows   (Mac/Linux: source .venv/bin/activate)
git clone https://github.com/ella0333/Eli_Felse_Reading.git data/modules/reading
cd data/modules/reading
pip install -r requirements.txt
python scripts/setup.py       # adds your books, and sets up illustrations
cd ../../..                   # Back to Eli_Felse_Base
elifelse run
```

"Read" will appear in the activity menu once you have added something to read,
followed by the kinds you added, for example "Read (Books, Light Novels)".

New to all of this? Follow the **[base project's guide](https://github.com/ella0333/Eli_Felse_Base/blob/master/docs/getting-started.md)** and come back here when you are finished

Need help choosing a compatible model? See the 
**[Model Options Guide](https://github.com/ella0333/Eli_Felse_Base/blob/master/docs/model-options.md)**

Requires base **0.5.5 or newer**.

## Adding books

This module does not include or download any books. You need to supply your own
DRM-free EPUB files that you have bought or otherwise have the right to use. Any EPUB
will work, but light novels are the easiest fit because most of them are sold as EPUBs
and come with illustrations. If you want to try it before buying anything, Project
Gutenberg and Standard Ebooks have free public domain books in EPUB format.

Before running setup, put each book or series in its own folder. If a series has more
than one volume, start each file name with its number in reading order: 1, 2, 3.

The setup wizard adds one book or series at a time. For each one it asks for the
title, a short description the agent reads when it picks what to read, what kind it is
(Books, Light Novels, or anything you type), and the folder you made for it. When you
have more than one kind, the agent picks the kind first. It prints the files in the order they will be read so you can check
it. Run the wizard again any time to add more.

## Settings

```yaml
activities:
  reading:
    books_dir: books       # where the wizard keeps your books, relative to this module
    images: off            # off | model | describe | both
```

`images` decides how the agent sees illustrations. `model` attaches them for the
agent's own model to look at, `describe` has Nano Banana 2 on OpenRouter turn each one
into a short text description (it needs `OPENROUTER_API_KEY` in the base's `.env`),
`both` does both, and `off` skips illustration pages.

## Standalone reading harness

You can also run this module without the Eli Felse framework. Connect any
**[compatible model](https://github.com/ella0333/Eli_Felse_Base/blob/master/docs/model-options.md)** and let it read directly.

```bash
git clone https://github.com/ella0333/Eli_Felse_Reading.git
cd Eli_Felse_Reading
python -m venv .venv
.venv\Scripts\activate        # Windows   (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python standalone_setup.py    # configures the model connection and adds your books
python standalone.py          # read
```

The model picks a series and reads it page by page until it decides to stop or
reaches the session's page limit (20 by default, set during setup). Press `Ctrl+C` at
any time to quit.

## Docs

- **[Module integration](docs/module-integration.md):** how it plugs into the base, the books folder, menus, config, data files
- **[Standalone mode](docs/standalone-mode.md):** standalone harness setup and how sessions work

## Learn More about the Project

Meet Eli Felse, a framework built to explore safer ways to create autonomous AI assistants.
Eli Felse is part of a public demo, weekly blogs, open-source releases, and a growing community.

**[24/7 live public demo](https://elifelse.org/eli/)**

**[Introduction Blog](https://elifelse.org/dev-blog/meet-eli)**

**[Join the Discord community](https://discord.com/invite/2C4znNnyM7)**

---

[![elif else](https://img.shields.io/badge/elif_else-869b9f?style=flat)](https://elifelse.org/)
[![MIT License](https://img.shields.io/badge/MIT_License-869b9f?style=flat)](LICENSE)
