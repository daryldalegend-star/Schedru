"""Image preparation and the Claude call's failure handling, with Claude faked."""

import io
from datetime import date
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from PIL import Image

import syllabus_cal.vision as vision

# Image preparation


def _save(path, size=(40, 20), fmt=None, exif=None):
    image = Image.new("RGB", size, (200, 30, 30))
    kwargs = {"exif": exif} if exif is not None else {}
    image.save(path, format=fmt, **kwargs)
    return path


def test_small_upright_png_is_sent_untouched(tmp_path):
    path = _save(tmp_path / "shot.png")

    data, media_type = vision.load_and_prepare_image(path)

    assert media_type == "image/png"
    assert data == path.read_bytes()


def test_heic_is_converted_even_when_small(tmp_path):
    # Claude's API doesn't accept HEIC; iPhone photos are HEIC and usually
    # well under the size limit, so size alone can't be the trigger.
    pytest.importorskip("pillow_heif")
    path = tmp_path / "flyer.heic"
    try:
        _save(path, fmt="HEIF")
    except (KeyError, OSError, ValueError):
        pytest.skip("this pillow-heif build can't write HEIC to make a fixture")

    data, media_type = vision.load_and_prepare_image(path)

    assert media_type == "image/jpeg"
    assert Image.open(io.BytesIO(data)).format == "JPEG"


def test_rotated_phone_photo_is_turned_upright(tmp_path):
    exif = Image.Exif()
    exif[0x0112] = 6  # "rotate 90° to display", as phone cameras write it
    path = _save(tmp_path / "photo.jpg", size=(40, 20), exif=exif)

    data, media_type = vision.load_and_prepare_image(path)
    out = Image.open(io.BytesIO(data))

    assert media_type == "image/jpeg"
    assert out.size == (20, 40)


def test_upright_jpeg_with_exif_is_not_reencoded(tmp_path):
    exif = Image.Exif()
    exif[0x0112] = 1
    path = _save(tmp_path / "photo.jpg", exif=exif)

    data, _ = vision.load_and_prepare_image(path)

    assert data == path.read_bytes()


# The Claude call


def _response(text='{"events": []}', stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
    )


class FakeClient:
    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _status_error(cls, status, message):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    body = {"type": "error", "error": {"type": "x", "message": message}}
    return cls(message, response=httpx2.Response(status, request=request), body=body)


@pytest.fixture
def image(tmp_path):
    return _save(tmp_path / "shot.png")


def run(client, image):
    return vision.extract_events(image, client, today=date(2026, 10, 14))


def test_thinking_blocks_are_skipped_when_reading_the_reply(image):
    result = run(FakeClient(_response('{"events": []}')), image)
    assert result.events == []


def test_reply_cap_leaves_room_for_thinking(image):
    client = FakeClient(_response())
    run(client, image)
    assert client.calls[0]["max_tokens"] >= 16000


def test_truncated_reply_fails_clearly_without_a_paid_retry(image):
    client = FakeClient(_response('{"events": [', stop_reason="max_tokens"))

    with pytest.raises(vision.ExtractionError, match="Crop it"):
        run(client, image)
    assert len(client.calls) == 1


def test_refusal_fails_clearly(image):
    with pytest.raises(vision.ExtractionError, match="declined"):
        run(FakeClient(_response("", stop_reason="refusal")), image)


def test_malformed_json_is_retried_once(image):
    client = FakeClient(_response("not json"), _response('{"events": []}'))
    assert run(client, image).events == []
    assert len(client.calls) == 2


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_status_error(anthropic.AuthenticationError, 401, "invalid x-api-key"), "rejected your API key"),
        (
            _status_error(
                anthropic.BadRequestError, 400,
                "Your credit balance is too low to access the Anthropic API.",
            ),
            "credit balance is too low",
        ),
        (_status_error(anthropic.RateLimitError, 429, "rate limited"), "Wait a minute"),
        (_status_error(anthropic.NotFoundError, 404, "model not found"), "isn't available"),
        (_status_error(anthropic.InternalServerError, 529, "overloaded"), "their end"),
    ],
)
def test_api_failures_become_plain_language(image, error, expected):
    with pytest.raises(vision.ClaudeAPIError, match=expected):
        run(FakeClient(error), image)


def test_no_internet_becomes_plain_language(image):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.APIConnectionError(request=request)

    with pytest.raises(vision.ClaudeAPIError, match="internet"):
        run(FakeClient(error), image)
