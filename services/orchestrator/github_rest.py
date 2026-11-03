"""GitHub's REST API with the sign-in's token, for Project remotes (ADR 0028).

The token is held only in the client (its header, and a pack push's password): never logged, returned or written. Anything GitHub says back is
untrusted text (ADR 0002) and reaches a person only through `untrusted.inline`. Tests pass a stand-in `http` with
the same `call(method, path, payload) -> (status, data)` shape as `publish_api._Api`.
"""
from __future__ import annotations

from . import untrusted
from .project_remote_layers import RemoteRefused

API = 'https://api.github.com'


def refused(what: str, status: int, data) -> str:
    """GitHub's refusal in plain words. A GitHub App's token is refused where the app isn't installed, so that one says
    where to install it (github_signin.INSTALL_URL)."""
    message = data.get('message') if isinstance(data, dict) else ''
    said = untrusted.inline(message, 160) if message else ''
    words = f'GitHub refused {what} ({status}{": " + said if said else ""})'
    if status == 403 and isinstance(message, str) and 'not accessible by integration' in message.lower():
        from .github_signin import INSTALL_URL
        words += f'. Install the HookeUI app on your GitHub account ({INSTALL_URL}), then try again'
    return words


class GitHubRefused(RemoteRefused):
    def __init__(self, what, status, data):
        super().__init__(refused(what, status, data))
        self.status = status


class SignInAgain(RemoteRefused):
    pass


class Client:
    def __init__(self, token: str | None = None, *, http=None, anonymous=False):
        self.anonymous = anonymous
        self._token = token  # for a pack push's password only (github_pack)
        if http is None:
            if not token and not anonymous:
                raise SignInAgain('Sign in to GitHub first')
            import httpx
            headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
                       'User-Agent': 'HookeUI'}
            if token:
                headers['Authorization'] = f'Bearer {token}'
            self._client = httpx.Client(base_url=API, timeout=60, headers=headers)
        else:
            self._client = None
        self.http = http

    def call(self, method: str, path: str, payload: dict | None = None):
        if self.http is not None:
            return self.http.call(method, path, payload)
        response = self._client.request(method, path, json=payload)
        try:
            data = response.json() if response.content else {}
        except ValueError:
            data = {}
        return response.status_code, data

    @property
    def packs(self) -> bool:
        """Whether this client can send a pack (github_pack): signed in, or a stand-in transport that takes one."""
        if self.http is not None:
            return hasattr(self.http, 'push_pack')
        return not self.anonymous and bool(self._token)

    def push_pack(self, repo: str, objects: list, commit: str) -> str:
        """One pack of new Git objects to the pending branch for `commit` (github_pack.push); returns its name.
        Raises github_pack.PackUnavailable when it cannot be made, and the caller uses REST instead."""
        from .github_pack import PackUnavailable, push
        if self.http is not None:
            pack = getattr(self.http, 'push_pack', None)
            if pack is None:
                raise PackUnavailable('this transport has no pack push')
            return pack(repo, objects, commit)
        if self.anonymous or not self._token:
            raise PackUnavailable('a pack push needs a sign-in')
        return push(self._token, repo, objects, commit)

    def upload(self, url: str, body: bytes):
        """POST raw bytes (a release asset) to an uploads.github.com URL."""
        if self.http is not None:
            return self.http.upload(url, body)
        response = self._client.post(url, content=body, headers={'Content-Type': 'application/octet-stream'})
        try:
            data = response.json() if response.content else {}
        except ValueError:
            data = {}
        return response.status_code, data

    def download(self, url: str, limit: int) -> bytes:
        """GET public bytes (a raw file or release asset) with no token, bounded; redirects to GitHub's storage only."""
        if self.http is not None:
            return self.http.download(url, limit)
        import httpx
        with httpx.Client(follow_redirects=True, timeout=120, trust_env=False,
                          headers={'User-Agent': 'HookeUI'}) as client:
            with client.stream('GET', url) as response:
                if response.status_code != 200:
                    raise RemoteRefused(f'GitHub could not provide a file of this project ({response.status_code})')
                host = response.url.host or ''
                if not (host == 'github.com' or host.endswith('.githubusercontent.com')):
                    raise RemoteRefused('A download was redirected away from GitHub; nothing was used')
                body = bytearray()
                for block in response.iter_bytes():
                    body += block
                    if len(body) > limit:
                        raise RemoteRefused('A file of this project is larger than its manifest says')
                return bytes(body)

    def asset(self, repo: str, asset_id: int, limit: int) -> bytes:
        """GET a release asset's bytes signed in (a private repository's), bounded. GitHub answers with a redirect to
        its storage, to which the sign-in is not sent."""
        if self.http is not None:
            return self.http.asset(repo, asset_id, limit)
        if type(asset_id) is not int or self.anonymous:
            raise RemoteRefused('A private project file needs a GitHub sign-in')
        import httpx
        with self._client.stream('GET', f'/repos/{repo}/releases/assets/{asset_id}',
                                 headers={'Accept': 'application/octet-stream'}) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                target = httpx.URL(response.headers.get('location', ''))
                if target.scheme != 'https' or not (target.host or '').endswith(('.githubusercontent.com', '.github.com')):
                    raise RemoteRefused('A download was redirected away from GitHub; nothing was used')
                return self.download(str(target), limit)
            if response.status_code != 200:
                raise RemoteRefused(f'GitHub could not provide a file of this project ({response.status_code})')
            body = bytearray()
            for block in response.iter_bytes():
                body += block
                if len(body) > limit:
                    raise RemoteRefused('A file of this project is larger than its manifest says')
            return bytes(body)

    def ok(self, method: str, path: str, payload=None, *, what: str, allow=()):
        status, data = self.call(method, path, payload)
        if status == 401:
            raise SignInAgain('Your GitHub sign-in has expired; sign in again')
        if status >= 400 and status not in allow:
            raise GitHubRefused(what, status, data)
        return status, data


def signed_in_client(*, http=None) -> Client:
    if http is not None:
        return Client(http=http)
    from . import github_signin
    return Client(github_signin.token())
