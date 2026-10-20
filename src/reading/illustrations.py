"""How the agent sees illustrations, the same four settings as the Reddit module.

    off       illustration-only pages are skipped, illustrated pages are read as text
    model     the image is attached for the agent's own model to look at
    describe  Nano Banana 2 on OpenRouter describes it, and the description goes in the page text
    both      the description and the attached image
"""

from __future__ import annotations

import base64
from io import BytesIO
from typing import Any

import httpx

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

API_KEY_ENV = "OPENROUTER_API_KEY"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
VISION_MODEL = "google/gemini-3.1-flash-image"


def image_mode(value: Any) -> str:
    """config `images`: off / model / describe / both (true/false also accepted)."""
    if value is True:
        return "model"
    value = str(value).lower()
    return value if value in ("model", "describe", "both") else "off"


def _compress(data: bytes, max_size: int = 768) -> bytes:
    if not HAS_PIL:
        return data
    try:
        img = Image.open(BytesIO(data))
        img.thumbnail((max_size, max_size), Image.LANCZOS)
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return buf.getvalue()
    except Exception:
        return data


class Illustrations:
    def __init__(self, mode: str, openrouter_api_key: str = "", say=print) -> None:
        self.describe = mode in ("describe", "both") and bool(openrouter_api_key)
        self.attach = mode in ("model", "both")
        self._key = openrouter_api_key
        self.say = say

    @property
    def shown(self) -> bool:
        return self.describe or self.attach

    async def describe_image(self, data_uri: str, book: str) -> str | None:
        """A 1-2 sentence description from Nano Banana 2. None without a key or on failure."""
        if not self.describe:
            return None
        try:
            raw = base64.b64decode(data_uri.split(",", 1)[1])
        except (IndexError, ValueError):
            return None
        b64 = base64.b64encode(_compress(raw)).decode("utf-8")
        prompt = (f"Illustration from {book}. "
                  "Describe what this image shows in 1-2 sentences. Be concise and factual.")
        payload = {
            "model": VISION_MODEL,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]}],
            "max_tokens": 200,
            "temperature": 0.1,
        }
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
            self.say(f"Vision API error {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            self.say(f"Vision API error: {e}")
        return None
