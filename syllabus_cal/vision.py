"""Image validation/preparation and the Anthropic vision call."""

from __future__ import annotations

import base64
import io
import json
from datetime import date
from pathlib import Path

import anthropic
from anthropic import Anthropic
from PIL import Image, ImageOps
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
# Sonnet 5 thinks before answering by default, and thinking counts against
# this cap -- 4k could cut a long syllabus off mid-JSON. Only tokens actually
# generated are billed, so a high ceiling costs nothing unless it's used.
MAX_OUTPUT_TOKENS = 16000

# What the Messages API accepts. HEIC (iPhone photos) isn't one of them.
_API_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
_EXIF_ORIENTATION = 0x0112

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


class ClaudeAPIError(RuntimeError):
    """Anthropic's API refused or failed the request, explained in plain language."""


def _encode_jpeg(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def load_and_prepare_image(path: Path) -> tuple[bytes, str]:
    """Validate `path` and return (bytes, media_type) ready to base64-encode.

    Re-encodes as JPEG when the API wouldn't accept the file as-is: over
    MAX_BYTES, a format it doesn't take (HEIC), or a photo stored rotated.
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
    upright = image.getexif().get(_EXIF_ORIENTATION, 1) == 1

    if len(raw_bytes) <= MAX_BYTES and media_type in _API_MEDIA_TYPES and upright:
        return raw_bytes, media_type

    # Phone photos record rotation as a tag rather than in the pixels; apply it
    # so Claude isn't handed a flyer lying on its side.
    image = ImageOps.exif_transpose(image)
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    encoded = _encode_jpeg(image)
    scale = 0.9
    while len(encoded) > MAX_BYTES and max(image.size) > 200:
        image = image.resize(
            (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
        )
        encoded = _encode_jpeg(image)

    if len(encoded) > MAX_BYTES:
        raise ImageError(
            f"'{path}' is still over {MAX_BYTES // (1024 * 1024)}MB after downscaling."
        )

    return encoded, "image/jpeg"


def _api_error_message(exc: anthropic.APIStatusError) -> str:
    """The API's own human-readable message, without the SDK's raw-dict wrapping."""
    body = exc.body
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
    return exc.message


def _call_claude(client: Anthropic, image_b64: str, media_type: str, prompt: str) -> str:
    try:
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
    except anthropic.AuthenticationError as exc:
        raise ClaudeAPIError(
            "Anthropic rejected your API key. It may be mistyped, deleted, or expired "
            "(keys can be created with an expiry date). Make a new one at "
            "console.anthropic.com -> API keys, copy it from the dialog that appears, "
            "and paste it into .env."
        ) from exc
    except anthropic.PermissionDeniedError as exc:
        raise ClaudeAPIError(f"Your API key isn't allowed to do this: {_api_error_message(exc)}") from exc
    except anthropic.NotFoundError as exc:
        raise ClaudeAPIError(
            f"The model '{MODEL}' isn't available to your account any more. "
            f"Anthropic said: {_api_error_message(exc)}"
        ) from exc
    except anthropic.RateLimitError as exc:
        raise ClaudeAPIError(
            "Anthropic is limiting how fast you can send requests. Wait a minute and try again."
        ) from exc
    except anthropic.BadRequestError as exc:
        # Also how an empty account balance arrives, with the API's own
        # explanation of where to add credit.
        raise ClaudeAPIError(f"Anthropic refused the request: {_api_error_message(exc)}") from exc
    except anthropic.APIStatusError as exc:
        raise ClaudeAPIError(
            f"Anthropic had a problem on their end (HTTP {exc.status_code}). "
            "Try again in a few minutes."
        ) from exc
    except anthropic.APIConnectionError as exc:
        raise ClaudeAPIError("Couldn't reach Anthropic -- check your internet connection.") from exc

    # Checked before reading the text: a truncated reply fails JSON parsing,
    # and the parse-error retry would just be truncated again, at full price.
    if response.stop_reason == "max_tokens":
        raise ExtractionError(
            "This image has more events than fit in one reply. Crop it into two "
            "screenshots and run each one."
        )
    if response.stop_reason == "refusal":
        raise ExtractionError("Claude declined to read this image.")

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
