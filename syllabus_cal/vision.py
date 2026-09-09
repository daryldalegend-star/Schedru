"""Image validation/preparation and the Anthropic vision call."""

from __future__ import annotations

import base64
import io
import json
from datetime import date
from pathlib import Path

from anthropic import Anthropic
from PIL import Image
from pydantic import ValidationError

from .prompts.extract_events import render_prompt
from .schema import ExtractionResult

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pillow_heif = None

MODEL = "claude-sonnet-5"
MAX_BYTES = 5 * 1024 * 1024
MAX_OUTPUT_TOKENS = 4096

_EXT_TO_MEDIA_TYPE = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
}

SUPPORTED_EXTENSIONS = frozenset(_EXT_TO_MEDIA_TYPE)


class ImageError(ValueError):
    """Raised when the input image can't be loaded or prepared for the API."""


class ExtractionError(RuntimeError):
    """Raised when Claude's response can't be turned into valid events."""


def load_and_prepare_image(path: Path) -> tuple[bytes, str]:
    """Validate `path` and return (bytes, media_type) ready to base64-encode.

    Downscales and re-encodes as JPEG if the file is over MAX_BYTES.
    """
    if not path.exists():
        raise ImageError(f"No such file: {path}")

    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ImageError(
            f"Unsupported image type '{ext}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    if ext in (".heic", ".heif") and pillow_heif is None:
        raise ImageError(
            "HEIC/HEIF support requires the 'pillow-heif' package (see requirements.txt)."
        )

    try:
        image = Image.open(path)
        image.load()
    except Exception as exc:
        raise ImageError(f"Could not read image '{path}': {exc}") from exc

    raw_bytes = path.read_bytes()
    media_type = _EXT_TO_MEDIA_TYPE[ext]

    if len(raw_bytes) <= MAX_BYTES:
        return raw_bytes, media_type

    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    scale = 0.9
    encoded = raw_bytes
    while len(encoded) > MAX_BYTES and max(image.size) > 200:
        image = image.resize(
            (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
        )
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        encoded = buffer.getvalue()

    if len(encoded) > MAX_BYTES:
        raise ImageError(
            f"'{path}' is still over {MAX_BYTES // (1024 * 1024)}MB after downscaling."
        )

    return encoded, "image/jpeg"


def _call_claude(client: Anthropic, image_b64: str, media_type: str, prompt: str) -> str:
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    return "".join(block.text for block in response.content if block.type == "text")


def _parse_json_response(raw_text: str) -> ExtractionResult:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    data = json.loads(text)
    return ExtractionResult.model_validate(data)


def extract_events(
    image_path: Path,
    client: Anthropic,
    today: date,
    semester_start: date | None = None,
    semester_end: date | None = None,
) -> ExtractionResult:
    """Run the full image -> validated-events pipeline for one screenshot.

    Raises ImageError before any API call if the image is unreadable/unsupported/too
    large, and ExtractionError if Claude's response still doesn't parse after one retry.
    """
    image_bytes, media_type = load_and_prepare_image(image_path)
    image_b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    prompt = render_prompt(today, semester_start, semester_end)

    raw_text = _call_claude(client, image_b64, media_type, prompt)

    try:
        return _parse_json_response(raw_text)
    except (json.JSONDecodeError, ValidationError) as first_error:
        retry_prompt = (
            f"{prompt}\n\nYour previous response failed to parse as valid JSON matching the "
            f"required schema. Parse error: {first_error}\n\n"
            f"Your previous response was:\n{raw_text}\n\n"
            f"Return ONLY corrected JSON matching the schema above."
        )
        raw_text_retry = _call_claude(client, image_b64, media_type, retry_prompt)
        try:
            return _parse_json_response(raw_text_retry)
        except (json.JSONDecodeError, ValidationError) as second_error:
            raise ExtractionError(
                "Claude's response could not be parsed as valid JSON after one retry.\n\n"
                f"First error: {first_error}\n"
                f"Second error: {second_error}\n\n"
                f"Raw response (second attempt):\n{raw_text_retry}"
            ) from second_error
