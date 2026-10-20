#!/usr/bin/env python3
"""Reading module setup - interactive wizard.

Matches the style of the base's `elifelse init` wizard. Asks where your books
live, adds series to that folder one at a time (title, description, and the
EPUBs, copied in as Volume_01.epub and on), asks whether the agent sees
illustrations, and writes the answers to the base's config.yaml.
"""

import sys
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parent.parent
BASE_ROOT = MODULE_ROOT.parent.parent.parent  # data/modules/reading -> base root
BASE_CONFIG = BASE_ROOT / "config.yaml"

sys.path.insert(0, str(MODULE_ROOT))

from src.reading.activity import books_dir_from  # noqa: E402
from src.reading.wizard import ask_books, ask_images, say  # noqa: E402

# ~~~ base config + .env ~~~

def env_path() -> Path:
    """The base's .env when installed as a drop-in, otherwise the module's own."""
    if BASE_CONFIG.exists() or (BASE_ROOT / "pyproject.toml").exists():
        return BASE_ROOT / ".env"
    return MODULE_ROOT / ".env"


def base_model() -> tuple[str, str]:
    """(base_url, model) from the base's config.yaml, or ('', '')."""
    try:
        import yaml

        with open(BASE_CONFIG, encoding="utf-8") as f:
            provider = (yaml.safe_load(f) or {}).get("provider", {}) or {}
    except (OSError, ImportError):
        return "", ""
    return str(provider.get("base_url", "")), str(provider.get("model", ""))


def current_books_dir() -> str:
    """books_dir already in config.yaml, set by hand, or '' for the default."""
    try:
        import yaml

        with open(BASE_CONFIG, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, ImportError):
        return ""
    return str(((data.get("activities") or {}).get("reading") or {}).get("books_dir") or "")


def print_config(section: dict) -> None:
    """The block, indented ready to paste under `activities:`."""
    try:
        import yaml
    except ImportError:
        say("install PyYAML to see the config block")
        return
    dumped = yaml.dump(
        {"activities": {"reading": section}}, default_flow_style=False, sort_keys=False
    )
    for line in dumped.rstrip().splitlines():
        say(f"  {line}")


def write_base_config(section: dict) -> bool:
    """Merge the section into the base's config.yaml."""
    if not BASE_CONFIG.exists():
        say("no config.yaml next to the base, so nothing was written")
        return False
    try:
        import yaml
    except ImportError:
        say("PyYAML is not installed, so config.yaml was left alone")
        return False

    try:
        data = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8")) or {}
        data.setdefault("activities", {}).setdefault("reading", {}).update(section)
        BASE_CONFIG.write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
    except (OSError, yaml.YAMLError) as e:
        say(f"could not update config.yaml: {e}")
        return False
    say("")
    say(f"updated activities.reading in {BASE_CONFIG}")
    say("(the YAML round-trip drops the comments the base ships with)")
    return True


# ~~~ main wizard ~~~

def main():
    section: dict[str, object] = {}

    ask_books(books_dir_from(current_books_dir()))

    say("")
    base_url, model = base_model()
    section["images"] = ask_images(base_url, model, env_path())

    written = write_base_config(section)

    say("")
    if written:
        say("for reference, this is what went in:")
    else:
        say("add this to your config.yaml:")
    print_config(section)

    say("")
    say("Done. Start the agent with:  elifelse run")
    say("'Read' will appear in the activity menu, followed by the kinds you added.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        sys.exit(1)
