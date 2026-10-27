"""One-click GitHub sign-in (orchestrator/github_signin.py; C5): GitHub's device flow, against a stand-in GitHub and a
stand-in Keychain. With no client ID (this copy of the app, until the project's GitHub app is registered) every step
says so and what works today, and nothing is asked of GitHub; with one, the code is shown, polling waits, slows down when
told, and keeps the token only in the Keychain, never on what the studio is sent."""
import pytest

from orchestrator import github_signin as gh
from orchestrator import keychain


class Store:
    def __init__(self):
        self.saved = {}

    def get_password(self, service, name):
        return self.saved.get((service, name))

    def set_password(self, service, name, value):
        self.saved[(service, name)] = value

    def delete_password(self, service, name):
        self.saved.pop((service, name), None)


@pytest.fixture
def store(monkeypatch):
    s = Store()
    monkeypatch.setattr(keychain, "_store", s)
    keychain.forget()
    yield s
    keychain.forget()


class GitHub:
    """GitHub's two device-flow endpoints, answering from a script."""

    def __init__(self, token_replies):
        self.asked = []
        self.token_replies = list(token_replies)

    def __call__(self, url, form):
        self.asked.append((url, dict(form)))
        if url == gh.DEVICE_URL:
            return {"device_code": "dev-123", "user_code": "WDJB-MJHT", "verification_uri": "https://github.com/login/device",
                    "interval": 5, "expires_in": 900}
        return self.token_replies.pop(0)


def test_without_the_projects_github_app_it_says_so_and_asks_github_nothing(store, monkeypatch):
    monkeypatch.delenv("MICROMIND_GITHUB_CLIENT_ID", raising=False)
    monkeypatch.setattr(gh, "CLIENT_ID", "")
    said = gh.status()
    assert said["setUp"] is False and "gh auth login" in said["says"] and "isn't set up" in said["says"]
    github = GitHub([])
    with pytest.raises(gh.NotSetUp, match="gh auth login"):
        gh.start(post=github)
    assert github.asked == [], "no client ID was invented, and GitHub was never asked"


def test_signing_in_shows_the_code_waits_and_keeps_the_token_only_in_the_keychain(store, monkeypatch):
    monkeypatch.setenv("MICROMIND_GITHUB_CLIENT_ID", "Iv-test-client")
    github = GitHub([{"error": "authorization_pending"}, {"error": "slow_down", "interval": 10},
                     {"access_token": "gho_canary", "token_type": "bearer", "scope": gh.SCOPES}])
    shown = gh.start(post=github)
    assert shown["code"] == "WDJB-MJHT" and shown["open"] == "https://github.com/login/device" and shown["every"] == 5
    assert "dev-123" not in str(shown), "the device code stays in the app"
    assert github.asked[0] == (gh.DEVICE_URL, {"client_id": "Iv-test-client", "scope": gh.SCOPES})
    assert gh.poll(shown["id"], post=github)["state"] == "waiting"
    slower = gh.poll(shown["id"], post=github)
    assert slower == {"state": "waiting", "every": 10, "says": "Waiting for you to approve it on GitHub"}
    done = gh.poll(shown["id"], post=github)
    assert done == {"state": "done", "says": "Signed in to GitHub"}, "the token is never on what the studio is sent"
    assert github.asked[-1][1] == {"client_id": "Iv-test-client", "device_code": "dev-123", "grant_type": gh.GRANT}
    assert gh.token() == "gho_canary" and store.saved[(keychain.SERVICE, gh.ACCOUNT)] == "gho_canary"
    assert gh.status()["signedIn"] is True
    assert gh.poll(shown["id"], post=github)["state"] == "expired", "a finished sign-in is not polled again"
    assert gh.sign_out() is True and gh.token() is None and gh.status()["signedIn"] is False


@pytest.mark.parametrize("reply, state", [({"error": "access_denied"}, "denied"),
                                          ({"error": "expired_token"}, "expired")])
def test_declined_or_expired_keeps_nothing(store, monkeypatch, reply, state):
    monkeypatch.setenv("MICROMIND_GITHUB_CLIENT_ID", "Iv-test-client")
    github = GitHub([reply])
    shown = gh.start(post=github)
    assert gh.poll(shown["id"], post=github)["state"] == state
    assert gh.token() is None and store.saved == {}


def test_what_github_refuses_is_said_in_plain_words(store, monkeypatch):
    monkeypatch.setenv("MICROMIND_GITHUB_CLIENT_ID", "Iv-test-client")
    with pytest.raises(gh.SignInFailed, match="Device Flow turned on"):
        gh.start(post=lambda url, form: {"error": "device_flow_disabled"})
