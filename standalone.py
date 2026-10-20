"""Standalone reading - read your EPUBs with an LLM, page by page.

The model picks a series from your books folder, then reads one page at a
time and writes its thoughts, until it decides to stop or uses up the
session's pages. Progress is saved, so the next run picks up where this one
left off. No Eli Felse Base required.

Usage:
    python standalone_setup.py   (once, to configure your model and add books)
    python standalone.py         (read)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml
from dotenv import load_dotenv

from src.reading import prompts
from src.reading.activity import books_dir_from
from src.reading.illustrations import API_KEY_ENV, Illustrations, image_mode
from src.reading.library import Library, singular
from src.reading.profiles import Profiles, plot_summary
from src.reading.reader import ReadingSession

MODULE_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = MODULE_ROOT / "standalone_config.yaml"
DATA_DIR = MODULE_ROOT / "data"

# Pages a standalone session may read before it ends. 0 = no limit.
DEFAULT_SESSION_LIMIT = 20

DEFAULT_SYSTEM_PROMPT = """\
You are {name}, an autonomous agent with some free time to read. You choose \
what to read and decide after every page whether to keep going.

You must respond in JSON, using exactly the fields the current step asks for. \
Your "thinking" field is your own reasoning, in your own voice."""

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}


def menu_schema(letters: list[str]) -> dict[str, Any]:
    """A thinking field plus a choice locked to these letters, the base's shape."""
    return {
        "type": "object",
        "properties": {
            "thinking": {"type": "string"},
            "choice": {"type": "string", "enum": list(letters)},
        },
        "required": ["thinking", "choice"],
        "additionalProperties": False,
    }


def build_menu(question: str, labels: list[str]) -> tuple[str, list[str]]:
    """Lettered menu text plus its letters, rendered like the base's menus."""
    letters = [chr(ord("A") + i) for i in range(len(labels))]
    lines = [question, ""]
    lines += [
        f"{letter}) {label}"
        for letter, label in zip(letters, labels, strict=True)
    ]
    return "\n".join(lines), letters


# Hosts that speak the Anthropic Messages API instead of OpenAI chat completions.
_ANTHROPIC_HOSTS = {"api.anthropic.com"}

# Regexes to extract the offending parameter from provider error messages.
_BAD_PARAM_RE = re.compile(
    r"[Uu]n(?:known|supported) (?:parameter|value):?\s*'?\"?(\w+)"
)
_BACKTICK_PARAM_RE = re.compile(r"`(\w+)`")

# Parameters that have a known alternate name on certain providers.
_PARAM_ALIASES: dict[str, str] = {
    "max_tokens": "max_completion_tokens",
}

# Never remove these from the payload, even on error.
_ESSENTIAL_PARAMS = {"model", "messages", "stream"}

# Maximum number of parameter-fix retries per call.
_MAX_PARAM_RETRIES = 6

# Regex to extract a JSON object from potentially wrapped text.
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

# Harmony-style channel tags some models leak into output.
_HARMONY_RE = re.compile(r"<\|[^|]*\|>")

# Placeholder content pattern.
_PLACEHOLDER_RE = re.compile(r"^[\[\(]?[.…]{2,}[\]\)]?\.?$")


def _print_system(text: str) -> None:
    """Print a framework status line, ASCII-safe."""
    line = f"[system] {text}"
    try:
        print(line)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(line.encode(enc, errors="replace").decode(enc))


def _say(text: str) -> None:
    """Print agent-facing output, ASCII-safe."""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(text.encode(enc, errors="replace").decode(enc))


def _strip_harmony_tags(text: str) -> str:
    return _HARMONY_RE.sub("", text).strip()


def _is_placeholder(text: str) -> bool:
    return not text.strip() or bool(_PLACEHOLDER_RE.match(text.strip()))


def _clean_thinking(text: str) -> str:
    """Strip mimicked '[Thinking: ...]' wrappers."""
    t = text.strip()
    nest = 0
    while t.startswith("[Thinking:"):
        t = t[len("[Thinking:"):].strip()
        nest += 1
    for _ in range(nest):
        if t.endswith("]"):
            t = t[:-1].strip()
    return t


def _parse_json(content: str) -> dict | None:
    """Parse JSON from model output, with regex fallback for chatty models."""
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = _JSON_OBJECT_RE.search(content)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return None


def _validate_response(
    content: str, schema: dict, substantive: str | None = None
) -> dict | None:
    """Parse and validate model output against a schema.

    Returns the parsed dict with cleaned fields, or None if invalid.
    `substantive` names a field that must not come back as a placeholder.
    """
    parsed = _parse_json(content)
    if parsed is None:
        return None

    # Strip leaked formatting tags from all string fields.
    for key, val in list(parsed.items()):
        if isinstance(val, str):
            parsed[key] = _strip_harmony_tags(val)

    # Check required fields.
    for field in schema.get("required", []):
        if field not in parsed:
            return None

    # Enum fields must actually be in the enum.
    for field, spec in schema.get("properties", {}).items():
        allowed = spec.get("enum")
        if allowed and str(parsed.get(field, "")).strip().upper() not in allowed:
            return None

    if substantive and _is_placeholder(str(parsed.get(substantive, ""))):
        return None

    if isinstance(parsed.get("thinking"), str):
        parsed["thinking"] = _clean_thinking(parsed["thinking"])

    return parsed


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        print("Error: standalone_config.yaml not found.")
        print("Run 'python standalone_setup.py' first.")
        sys.exit(1)

    # Load .env for API key resolution (matches base pattern).
    env_path = MODULE_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def _resolve_api_key(provider: dict) -> str:
    """Resolve the API key from an env var name (base pattern) or direct value (legacy)."""
    env_name = provider.get("api_key_env", "")
    if env_name:
        val = os.environ.get(env_name, "")
        if not val:
            print(f"Warning: api_key_env '{env_name}' is set but the environment "
                  f"variable is empty. Add it to your .env file.")
        return val
    return provider.get("api_key", "")


def _is_anthropic_host(base_url: str) -> bool:
    """Auto-detect Anthropic API by hostname."""
    host = (urlparse(base_url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _ANTHROPIC_HOSTS)


def _get_model_params(config: dict, model: str) -> dict:
    """Get model params, falling back to 'default' entry (matches base pattern)."""
    params = config.get("model_params", {})
    if isinstance(params, dict):
        if model in params:
            return params[model]
        if "default" in params:
            return params["default"]
        if "temperature" in params:
            return params
    return {"temperature": 0.7, "top_p": 0.95, "max_tokens": 3000}


def _build_openai_payload(
    model: str, messages: list, params: dict, schema: dict,
    blocked_params: set, param_renames: dict,
) -> dict:
    """Build an OpenAI-compatible chat completions payload."""
    candidates = {
        "temperature": params.get("temperature", 0.7),
        "top_p": params.get("top_p", 0.95),
        "max_tokens": params.get("max_tokens", 3000),
    }
    if params.get("repeat_penalty"):
        candidates["repeat_penalty"] = params["repeat_penalty"]
    if params.get("top_k"):
        candidates["top_k"] = params["top_k"]

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    for key, value in candidates.items():
        if key not in blocked_params:
            payload[key] = value

    payload["response_format"] = {
        "type": "json_schema",
        "json_schema": {"name": "response", "strict": True, "schema": schema},
    }

    for old, new in param_renames.items():
        if old in payload:
            payload[new] = payload.pop(old)

    return payload


def _anthropic_content(content: Any) -> Any:
    """OpenAI-style image parts as Anthropic image blocks."""
    if not isinstance(content, list):
        return content
    blocks = []
    for part in content:
        if part.get("type") == "image_url":
            url = part["image_url"]["url"]
            media_type, data = url[len("data:"):].split(";base64,", 1)
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}})
        else:
            blocks.append(part)
    return blocks


def _build_anthropic_payload(
    model: str, messages: list, params: dict, schema: dict,
    blocked_params: set,
) -> dict:
    """Build an Anthropic Messages API payload with forced tool_use for structured output."""
    system_parts: list[str] = []
    chat_msgs: list[dict] = []
    for msg in messages:
        if msg["role"] == "system":
            content = msg["content"]
            if isinstance(content, str):
                system_parts.append(content)
        else:
            chat_msgs.append({"role": msg["role"], "content": _anthropic_content(msg["content"])})

    # Merge consecutive same-role messages (Anthropic requires alternation).
    merged: list[dict] = []
    for msg in chat_msgs:
        if (merged and merged[-1]["role"] == msg["role"]
                and isinstance(merged[-1]["content"], str) and isinstance(msg["content"], str)):
            prev = merged[-1]["content"]
            cur = msg["content"]
            merged[-1]["content"] = f"{prev}\n\n{cur}"
        else:
            merged.append(dict(msg))

    if merged and merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": "(continue)"})
    if not merged:
        merged.append({"role": "user", "content": "(continue)"})

    payload = {
        "model": model,
        "messages": merged,
        "max_tokens": params.get("max_tokens", 3000),
        "stream": False,
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)

    if "temperature" not in blocked_params:
        payload["temperature"] = params.get("temperature", 0.7)
    if "top_p" not in blocked_params and params.get("top_p"):
        payload["top_p"] = params["top_p"]
    if "top_k" not in blocked_params and params.get("top_k"):
        payload["top_k"] = params["top_k"]

    payload["tools"] = [{
        "name": "response",
        "description": "Respond with the required structured data.",
        "input_schema": schema,
    }]
    payload["tool_choice"] = {"type": "tool", "name": "response"}

    return payload


def _parse_openai_response(data: dict) -> str | None:
    """Extract content text from an OpenAI chat completions response."""
    try:
        msg = data["choices"][0]["message"]
        content = msg.get("content") or ""
        if not content and msg.get("reasoning_content"):
            content = msg["reasoning_content"]
        return content
    except (KeyError, IndexError, TypeError):
        return None


def _parse_anthropic_response(data: dict) -> str | None:
    """Extract content from an Anthropic Messages API response."""
    if isinstance(data, dict) and data.get("type") == "error":
        return None
    for block in data.get("content", []):
        if block.get("type") == "tool_use":
            return json.dumps(block.get("input", {}))
        if block.get("type") == "text":
            return block.get("text", "")
    return None


def _strip_model_prefix(model: str) -> str | None:
    """Strip publisher prefix (e.g. 'openai/gpt-4o' -> 'gpt-4o')."""
    if "/" not in model:
        return None
    stripped = model.split("/", 1)[1]
    return stripped if stripped != model else None


def _try_fix_param(
    error_text: str, payload: dict,
    blocked_params: set, param_renames: dict,
) -> bool:
    """Attempt to fix a parameter error in-place. Returns True if fixed."""
    m = _BAD_PARAM_RE.search(error_text)
    if not m:
        m = _BACKTICK_PARAM_RE.search(error_text)
    if not m:
        return False
    bad_param = m.group(1)
    if bad_param in _ESSENTIAL_PARAMS:
        return False

    alias = _PARAM_ALIASES.get(bad_param)
    if alias and bad_param not in param_renames:
        param_renames[bad_param] = alias
        if bad_param in payload:
            payload[alias] = payload.pop(bad_param)
        _print_system(f"Parameter '{bad_param}' not supported; using '{alias}' instead")
        return True

    if bad_param not in blocked_params:
        blocked_params.add(bad_param)
        payload.pop(bad_param, None)
        for orig, renamed in param_renames.items():
            if orig == bad_param:
                payload.pop(renamed, None)
        _print_system(f"Parameter '{bad_param}' not supported; removed")
        return True

    return False



class Session:
    """One standalone run: the message history plus the model connection."""

    def __init__(self, config: dict, client: httpx.AsyncClient) -> None:
        self.config = config
        self.client = client
        self.provider = config["provider"]
        self.is_anthropic = _is_anthropic_host(self.provider["base_url"])
        self.blocked_params: set[str] = set()
        self.param_renames: dict[str, str] = {}
        self.model_stripped: dict[str, str] = {}

        quirks = self.provider.get("quirks", {})
        self.no_think = quirks.get("no_think_suffix", False)
        if "qwen" in self.provider.get("model", "").lower() and not self.no_think:
            self.no_think = True
            _print_system("Qwen model detected, auto-enabled /no_think suffix")

        self.messages: list[dict[str, Any]] = [{"role": "system", "content": self.base_prompt()}]

    def base_prompt(self) -> str:
        settings = self.config.get("reading", {})
        custom = self.config.get("system_prompt")
        return custom or DEFAULT_SYSTEM_PROMPT.format(name=settings.get("agent_name", "Eli"))

    def set_system_prompt(self, text: str) -> None:
        self.messages[0] = {"role": "system", "content": text}

    def add(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
        self._trim()

    def _trim(self) -> None:
        max_context = self.provider.get("max_context_tokens", 36000)
        total = sum(len(str(m["content"])) for m in self.messages)
        while total > max_context * 2 and len(self.messages) > 5:
            self.messages.pop(1)
            self.messages.pop(1)
            total = sum(len(str(m["content"])) for m in self.messages)

    async def ask(self, prompt: str, schema: dict, image_urls: list[str] | None = None) -> dict | None:
        """Send one prompt and get back a validated dict, or None on failure.

        Images go on this turn only; the history keeps an "(image)" note so
        base64 never piles up in the context."""
        content = prompt + (" /no_think" if self.no_think else "")
        self.add("user", content + ("\n(image)" if image_urls else ""))
        messages = list(self.messages)
        if image_urls:
            parts: list[dict[str, Any]] = [{"type": "text", "text": content}]
            parts += [{"type": "image_url", "image_url": {"url": u}} for u in image_urls]
            messages[-1] = {"role": "user", "content": parts}
        result = await self._call(messages, schema)
        if result is None and image_urls:
            _print_system("Retrying without image...")
            result = await self._call(self.messages, schema)
        return result

    async def complete(self, system: str, prompt: str) -> str | None:
        """An isolated call for summaries: none of the reading history."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        result = await self._call(messages, SUMMARY_SCHEMA)
        return str(result["summary"]) if result and result.get("summary") else None

    async def _call(self, messages: list[dict[str, Any]], schema: dict) -> dict | None:
        model = self.provider["model"]
        params = _get_model_params(self.config, model)
        api_key = _resolve_api_key(self.provider)
        effective_model = self.model_stripped.get(model, model)

        def build() -> dict:
            if self.is_anthropic:
                return _build_anthropic_payload(effective_model, messages, params, schema, self.blocked_params)
            return _build_openai_payload(
                effective_model, messages, params, schema, self.blocked_params, self.param_renames,
            )

        base_url = self.provider["base_url"].rstrip("/")
        if self.is_anthropic:
            url = f"{base_url}/v1/messages"
            headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
            if api_key:
                headers["x-api-key"] = api_key
        else:
            if not base_url.endswith("/v1"):
                base_url += "/v1"
            url = f"{base_url}/chat/completions"
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
        payload = build()
        timeout = self.provider.get("request_timeout", 300)

        for _attempt in range(_MAX_PARAM_RETRIES):
            try:
                resp = await self.client.post(url, json=payload, headers=headers, timeout=timeout)
            except httpx.HTTPError as e:
                _print_system(f"Connection error: {e}")
                return None

            if resp.status_code in (400, 404):
                error_text = resp.text[:500]
                error_lower = error_text.lower()

                if ("invalid model" in error_lower
                        or "not_found" in error_lower
                        or "could not resolve" in error_lower):
                    stripped = _strip_model_prefix(payload.get("model", ""))
                    if stripped:
                        _print_system(f"Model '{payload['model']}' rejected; retrying as '{stripped}'")
                        self.model_stripped[model] = stripped
                        payload["model"] = stripped
                        continue
                    break

                if ("unknown parameter" in error_lower
                        or "unsupported parameter" in error_lower
                        or "unsupported value" in error_lower
                        or "deprecated" in error_lower
                        or "not supported" in error_lower):
                    if _try_fix_param(error_text, payload, self.blocked_params, self.param_renames):
                        continue
                    break

                _print_system(f"LLM error {resp.status_code}: {error_text[:200]}")
                break

            if resp.status_code != 200:
                _print_system(f"LLM error {resp.status_code}: {resp.text[:200]}")
                return None

            data = resp.json()

            if isinstance(data, dict) and "error" in data and "choices" not in data:
                err = data["error"]
                msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                _print_system(f"LLM error: {msg[:200]}")
                return None

            content = _parse_anthropic_response(data) if self.is_anthropic else _parse_openai_response(data)
            if content is None:
                _print_system("Unexpected response shape")
                return None

            result = _validate_response(content, schema)
            if result is not None:
                return result

            _print_system("Response rejected (malformed/missing fields), retrying...")
            payload = build()

        return None

    async def choose(self, question: str, labels: list[str], footer: str = "") -> int | None:
        """Lettered menu, exactly like the base's ctx.choose. Returns the index."""
        menu_text, letters = build_menu(question, labels)
        if footer:
            menu_text += f"\n\n{footer}"
        _say(f"\n{menu_text}")
        result = await self.ask(menu_text, menu_schema(letters))
        if result is None:
            return None
        letter = str(result.get("choice", "")).strip().upper()
        if result.get("thinking"):
            _say(f"\nThinking: {result['thinking']}")
        if letter not in letters:
            return None
        _say(f"Choice: {letter}")
        label = labels[letters.index(letter)]
        self.add("assistant", f"{result.get('thinking', '')}\nChoice: {label}".strip())
        return letters.index(letter)


class StandaloneIO:
    """ReaderIO on top of the standalone Session. No memory, no surveys."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def base_prompt(self) -> str:
        return self.session.base_prompt()

    def menu_preview(self) -> str:
        return ""

    def set_system_prompt(self, text: str) -> None:
        self.session.set_system_prompt(text)

    def add_context(self, role: str, text: str) -> None:
        self.session.add(role, text)

    def notification(self) -> str:
        return ""

    def last_emotion(self, name: str) -> str:
        return ""

    async def generate(self, prompt: str, schema: dict[str, Any],
                       image_urls: list[str] | None = None) -> dict[str, Any]:
        result = await self.session.ask(prompt, schema, image_urls)
        return result if result is not None else {"error": "the model did not answer"}

    async def recall(self, query: str, name: str) -> list[str]:
        return []

    def remember(self, text: str, name: str) -> None:
        return None


# ~~~ the session ~~~
async def run_session(config: dict) -> None:
    settings = config.get("reading", {})
    library = Library(
        books_dir_from(settings.get("books_dir")),
        DATA_DIR / "reading_tracker.json",
        say=_print_system,
    )
    await asyncio.to_thread(library.sync)
    novels = library.available()
    if not novels:
        _print_system(f"No books in {library.books_dir}. Run 'python standalone_setup.py' to add some.")
        return

    profiles = Profiles(DATA_DIR / "profiles")
    key = os.environ.get(API_KEY_ENV, "")
    illustrations = Illustrations(image_mode(settings.get("images", "off")), key, say=_print_system)
    limit = int(settings.get("session_limit", DEFAULT_SESSION_LIMIT))

    async with httpx.AsyncClient() as client:
        session = Session(config, client)
        io = StandaloneIO(session)

        kinds = library.kinds()
        kind = kinds[0]
        if len(kinds) > 1:
            picked_kind = await session.choose(prompts.KIND_QUESTION, kinds)
            if picked_kind is None:
                _print_system("the model did not pick what to read, ending the session")
                return
            kind = kinds[picked_kind]
        novels = library.available(kind)

        rows = [(n, library.status_text(n["key"]), profiles.get(n["key"], n["name"]), "") for n in novels]
        session.set_system_prompt(f"{session.base_prompt()}\n\n{prompts.selection_guidance(kind)}")
        picked = await session.choose(
            prompts.selection_question(novels, kind),
            [f"{n['name']} {status}" for n, status, _, _ in rows],
            footer=prompts.all_profiles_text(rows, kind),
        )
        if picked is None:
            _print_system("the model did not pick a series, ending the session")
            return
        novel = novels[picked]
        name = novel["name"]

        profile = prompts.profile_text(
            novel, profiles.get(novel["key"], name), library.description(novel["key"]), ""
        )
        reader = ReadingSession(io, library, illustrations, profile, page_limit=limit)
        await reader.run(novel["key"])

        max_chars = max(8000, int(config["provider"].get("max_context_tokens", 36000)) * 3 - 10000)
        summary = await plot_summary(reader.page_texts, name, singular(kind), session.complete, max_chars)
        if summary:
            profiles.store_last_read_summary(novel["key"], name, summary)
            await profiles.consolidate(novel["key"], name, singular(kind), session.complete)
            _print_system(f"Plot summary saved for {name}")

    _print_system(f"session over, {reader.pages_read} page(s) read")


def main():
    print("\n+-------------------------------------+")
    print("|   Reading - Standalone Runner       |")
    print("+-------------------------------------+")
    config = load_config()
    try:
        asyncio.run(run_session(config))
    except KeyboardInterrupt:
        print()
        _print_system("stopped")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        main()
