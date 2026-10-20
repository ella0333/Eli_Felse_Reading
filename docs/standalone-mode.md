# Standalone mode

`standalone.py` lets a model read your books directly, without the Eli Felse base. Same
books folder, same page map, same prompts; only the framework around them is missing.

## Setup

```bash
git clone https://github.com/ella0333/Eli_Felse_Reading.git
cd Eli_Felse_Reading
python -m venv .venv
.venv\Scripts\activate        # Windows   (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python standalone_setup.py
python standalone.py
```

### standalone_setup.py

It asks:

- **Where the model runs:** LM Studio, Ollama, or a paid API. Each has a default URL.
- **Model identifier**, e.g. `qwen3-8b` or `openai/gpt-4o-mini`.
- **Context tokens**, the clamp on every prompt. 36000 suits most local setups.
- **Pages per session**, how many pages the model reads before the run stops. 0 for none.
- **What the reader is called.**
- **System prompt**, default or your own.
- **Adding books**, the same step as the module's setup: one book or series at a time,
  with its title, description and folder.
- **Illustrations**, the same step as the module's setup.

It writes `standalone_config.yaml` and, for a paid API or an OpenRouter key, a `.env`
holding the key. The config only ever stores the env-var *name*, never the key itself.
Both are gitignored.

## Reading

The model picks a series from the same menu the module shows, then reads it page by
page, writing its thoughts on each one. Progress is kept in `data/reading_tracker.json`,
so the next run continues from the next page. After the session a plot summary is saved
to `data/profiles/`, and it shows in the series menu the next time.

## How it ends

- The model sets `return_to_menu` to stop reading.
- The session's page limit is reached.
- The last volume of the series ends.
- The model fails to answer.
- You press `Ctrl+C`.

## Config format

```yaml
provider:
  base_url: "http://127.0.0.1:1234"
  api_key_env: ""            # name of the env var holding the key ("" = local)
  model: "qwen3-8b"
  max_context_tokens: 36000
  request_timeout: 300

model_params:
  default:
    temperature: 0.7
    top_p: 0.95
    max_tokens: 3000

reading:
  session_limit: 20          # pages per run (0 = no limit)
  agent_name: "Eli"
  images: "off"              # off | model | describe | both
  books_dir: books           # optional, set by hand to keep books somewhere else

system_prompt: ""            # empty = use default
```

## What standalone leaves out

There is no memory, no survey and no notification line, since those come from the base.
The "last felt" line in the series menu is therefore never shown here.

## Context management

The session keeps its own message history and trims the oldest pair whenever the total
passes `max_context_tokens * 2` characters, which is the same approximation the base uses.
Illustrations are attached to the current page only, and the history keeps an `(image)`
note in their place.
