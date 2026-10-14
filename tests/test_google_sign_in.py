"""Google sign-in and connection checks, with Google faked.

These pin down what the user sees in each failure: whether the app offers to
sign in again (AuthError) or explains a problem signing in won't fix
(GoogleConnectionError). The real browser round-trip can't be tested here.
"""

from unittest.mock import MagicMock

import httplib2
import pytest
from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError

import syllabus_cal.calendar_client as cc


@pytest.fixture
def token_file(tmp_path, monkeypatch):
    path = tmp_path / "token.json"
    monkeypatch.setattr(cc, "TOKEN_PATH", path)
    return path


def fake_creds(*, valid=False, refresh_token="r", refresh_raises=None):
    creds = MagicMock()
    creds.valid = valid
    creds.refresh_token = refresh_token
    creds.to_json.return_value = '{"refreshed": true}'
    if refresh_raises:
        creds.refresh.side_effect = refresh_raises
    return creds


def load_with(monkeypatch, creds):
    monkeypatch.setattr(
        cc.Credentials, "from_authorized_user_file", lambda *a, **k: creds
    )


# load_credentials


def test_no_token_file_asks_to_sign_in(token_file):
    with pytest.raises(cc.AuthError, match="not signed in"):
        cc.load_credentials()


def test_valid_token_is_used_without_touching_the_network(token_file, monkeypatch):
    token_file.write_text("{}")
    creds = fake_creds(valid=True)
    load_with(monkeypatch, creds)

    assert cc.load_credentials() is creds
    creds.refresh.assert_not_called()


def test_expired_token_is_refreshed_and_saved(token_file, monkeypatch):
    token_file.write_text("{}")
    creds = fake_creds()
    load_with(monkeypatch, creds)

    assert cc.load_credentials() is creds
    creds.refresh.assert_called_once()
    assert token_file.read_text() == '{"refreshed": true}'


def test_the_weekly_google_expiry_asks_to_sign_in_again(token_file, monkeypatch):
    # Apps in Google's "Testing" mode get sign-ins that die after 7 days; this
    # must lead to a sign-in offer, not a dead end.
    token_file.write_text("{}")
    load_with(monkeypatch, fake_creds(refresh_raises=RefreshError("invalid_grant")))

    with pytest.raises(cc.AuthError, match="expired"):
        cc.load_credentials()


def test_no_internet_while_refreshing_is_not_treated_as_signed_out(token_file, monkeypatch):
    token_file.write_text("{}")
    load_with(monkeypatch, fake_creds(refresh_raises=TransportError("offline")))

    with pytest.raises(cc.GoogleConnectionError, match="internet"):
        cc.load_credentials()


def test_token_without_a_refresh_token_asks_to_sign_in(token_file, monkeypatch):
    token_file.write_text("{}")
    load_with(monkeypatch, fake_creds(refresh_token=None))

    with pytest.raises(cc.AuthError):
        cc.load_credentials()


def test_corrupted_token_file_asks_to_sign_in(token_file):
    token_file.write_text('{"fake": "token"}')

    with pytest.raises(cc.AuthError, match="unreadable"):
        cc.load_credentials()


# connect: one real Calendar request, so a file that exists but no longer
# works is caught before the screenshot is sent to Claude.


def _http_error(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": str(status)}), b'{"error": {"message": "nope"}}')


def _service(listing=None, raises=None):
    service = MagicMock()
    execute = service.events.return_value.list.return_value.execute
    if raises:
        execute.side_effect = raises
    else:
        execute.return_value = listing
    return service


def _connect_with(monkeypatch, service):
    monkeypatch.setattr(cc, "load_credentials", lambda: object())
    monkeypatch.setattr(cc, "build", lambda *a, **k: service)
    return cc.connect()


def test_connect_reports_which_calendar_it_reached(monkeypatch):
    service = _service({"summary": "daryl.lacuesta@gmail.com"})

    returned_service, name = _connect_with(monkeypatch, service)

    assert returned_service is service
    assert name == "daryl.lacuesta@gmail.com"


def test_connect_treats_a_rejected_token_as_signed_out(monkeypatch):
    with pytest.raises(cc.AuthError):
        _connect_with(monkeypatch, _service(raises=_http_error(401)))


def test_connect_explains_other_refusals_without_offering_sign_in(monkeypatch):
    # e.g. the Calendar API was never enabled in the Cloud project.
    with pytest.raises(cc.GoogleConnectionError, match="refused"):
        _connect_with(monkeypatch, _service(raises=_http_error(403)))


def test_connect_without_internet(monkeypatch):
    with pytest.raises(cc.GoogleConnectionError, match="internet"):
        _connect_with(monkeypatch, _service(raises=httplib2.ServerNotFoundError("dns")))


# run_auth_flow


def test_missing_credentials_file_points_at_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "CREDENTIALS_PATH", tmp_path / "credentials.json")

    with pytest.raises(cc.GoogleConnectionError, match="SETUP.md"):
        cc.run_auth_flow()


def test_sign_in_always_shows_the_account_picker_and_gets_a_refresh_token(
    tmp_path, monkeypatch
):
    creds_path = tmp_path / "credentials.json"
    creds_path.write_text("{}")
    monkeypatch.setattr(cc, "CREDENTIALS_PATH", creds_path)
    monkeypatch.setattr(cc, "TOKEN_PATH", tmp_path / "token.json")

    flow = MagicMock()
    flow.run_local_server.return_value.to_json.return_value = "{}"
    monkeypatch.setattr(
        cc.InstalledAppFlow, "from_client_secrets_file", lambda *a, **k: flow
    )

    cc.run_auth_flow()

    prompt = flow.run_local_server.call_args.kwargs["prompt"].split()
    assert "select_account" in prompt
    assert "consent" in prompt
    assert (tmp_path / "token.json").exists()


def test_access_blocked_for_an_account_not_on_the_test_list_is_explained(
    tmp_path, monkeypatch
):
    # Picking a Google account that isn't a test user makes Google redirect
    # back with access_denied, which used to escape as a stack trace.
    from oauthlib.oauth2.rfc6749.errors import AccessDeniedError

    creds_path = tmp_path / "credentials.json"
    creds_path.write_text("{}")
    monkeypatch.setattr(cc, "CREDENTIALS_PATH", creds_path)
    flow = MagicMock()
    flow.run_local_server.side_effect = AccessDeniedError(description="access_denied")
    monkeypatch.setattr(
        cc.InstalledAppFlow, "from_client_secrets_file", lambda *a, **k: flow
    )

    with pytest.raises(cc.GoogleConnectionError, match="Test users"):
        cc.run_auth_flow()
