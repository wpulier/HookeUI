"""Synthetic browser startup failures; no Chromium or native worker is launched."""
import pytest

from tests import browser_cdp


def test_failed_process_creation_removes_private_profile(tmp_path, monkeypatch):
    profile = tmp_path / 'browser-profile'
    def temporary_profile(**kwargs):
        profile.mkdir()
        return str(profile)
    def fail_to_start(*args, **kwargs):
        raise OSError('synthetic executable failure')
    monkeypatch.setattr(browser_cdp.tempfile, 'mkdtemp', temporary_profile)
    monkeypatch.setattr(browser_cdp.subprocess, 'Popen', fail_to_start)
    with pytest.raises(OSError, match='synthetic executable failure'):
        browser_cdp.Browser('/synthetic/chromium')
    assert not profile.exists()


@pytest.mark.parametrize('failure', ['endpoint-read', 'websocket-connect'])
def test_failed_endpoint_setup_reaps_process_and_removes_profile(tmp_path, monkeypatch, failure):
    profile = tmp_path / 'browser-profile'
    def temporary_profile(**kwargs):
        profile.mkdir()
        (profile / 'DevToolsActivePort').write_text('12345\n/devtools/browser/synthetic\n')
        return str(profile)
    class Process:
        returncode = None
        def poll(self): return self.returncode
        def terminate(self): pass
        def wait(self, timeout): self.returncode = 0
    process = Process()
    def fail(*args, **kwargs): raise OSError('synthetic endpoint failure')
    monkeypatch.setattr(browser_cdp.tempfile, 'mkdtemp', temporary_profile)
    monkeypatch.setattr(browser_cdp.subprocess, 'Popen', lambda *a, **k: process)
    if failure == 'endpoint-read':
        monkeypatch.setattr(browser_cdp.Path, 'read_text', fail)
    else:
        monkeypatch.setattr(browser_cdp, 'connect', fail)
    with pytest.raises(OSError, match='synthetic endpoint failure'):
        browser_cdp.Browser('/synthetic/chromium')
    assert process.poll() is not None
    assert not profile.exists()


def test_endpoint_timeout_kills_and_reaps_unresponsive_process(tmp_path, monkeypatch):
    profile = tmp_path / 'browser-profile'
    def temporary_profile(**kwargs):
        profile.mkdir()
        return str(profile)
    class Process:
        returncode = None
        killed = False
        def poll(self): return self.returncode
        def terminate(self): pass
        def kill(self): self.killed = True
        def wait(self, timeout):
            if not self.killed:
                raise browser_cdp.subprocess.TimeoutExpired('synthetic chromium', timeout)
            self.returncode = -9
    process = Process()
    monkeypatch.setattr(browser_cdp.tempfile, 'mkdtemp', temporary_profile)
    monkeypatch.setattr(browser_cdp.subprocess, 'Popen', lambda *a, **k: process)
    clock = iter([0, 31])
    monkeypatch.setattr(browser_cdp.time, 'monotonic', lambda: next(clock))
    with pytest.raises(browser_cdp.BrowserError, match='did not start its DevTools endpoint'):
        browser_cdp.Browser('/synthetic/chromium')
    assert process.killed and process.poll() == -9
    assert not profile.exists()


def test_cleanup_failure_preserves_startup_error_and_removes_profile(tmp_path, monkeypatch):
    profile = tmp_path / 'browser-profile'
    def temporary_profile(**kwargs):
        profile.mkdir()
        (profile / 'DevToolsActivePort').write_text('12345\n/devtools/browser/synthetic\n')
        return str(profile)
    class Process:
        def poll(self): return None
        def terminate(self): raise OSError('synthetic cleanup failure')
    def fail_connect(*args, **kwargs): raise OSError('synthetic connection failure')
    monkeypatch.setattr(browser_cdp.tempfile, 'mkdtemp', temporary_profile)
    monkeypatch.setattr(browser_cdp.subprocess, 'Popen', lambda *a, **k: Process())
    monkeypatch.setattr(browser_cdp, 'connect', fail_connect)
    with pytest.raises(OSError, match='synthetic connection failure') as caught:
        browser_cdp.Browser('/synthetic/chromium')
    assert any('synthetic cleanup failure' in note for note in caught.value.__notes__)
    assert not profile.exists()


def test_owned_browser_bounds_the_close_handshake_with_unread_devtools_replies(tmp_path, monkeypatch):
    profile = tmp_path / 'browser-profile'
    profile.mkdir()
    (profile / 'DevToolsActivePort').write_text('12345\n/devtools/browser/synthetic\n')
    class Process:
        returncode = None
        def poll(self): return self.returncode
        def terminate(self): self.returncode = 0
        def wait(self, timeout): return self.returncode
    class Socket:
        def close(self): pass
    observed = {}
    def connect(url, **kwargs):
        observed.update(kwargs)
        return Socket()
    monkeypatch.setattr(browser_cdp.tempfile, 'mkdtemp', lambda **kwargs: str(profile))
    monkeypatch.setattr(browser_cdp.subprocess, 'Popen', lambda *a, **k: Process())
    monkeypatch.setattr(browser_cdp, 'connect', connect)
    browser = browser_cdp.Browser('/synthetic/chromium')
    try:
        # A full receive queue blocks the close handshake; shutdown must not inherit the library's 10-second wait.
        assert observed.get('close_timeout') == 1
    finally:
        browser.close()
    assert not profile.exists()


def test_failed_socket_close_still_kills_reaps_and_removes_owned_browser(tmp_path):
    profile = tmp_path / 'browser-profile'
    profile.mkdir()
    class Process:
        returncode = None
        killed = False
        def poll(self): return self.returncode
        def terminate(self): pass
        def kill(self): self.killed = True
        def wait(self, timeout):
            if not self.killed:
                raise browser_cdp.subprocess.TimeoutExpired('synthetic chromium', timeout)
            self.returncode = -9
    class Socket:
        def close(self): raise OSError('synthetic close handshake failure')
    browser = browser_cdp.Browser.__new__(browser_cdp.Browser)
    browser.profile, browser.process, browser.socket = profile, Process(), Socket()
    with pytest.raises(OSError, match='close handshake failure'):
        browser.close()
    assert browser.process.returncode == -9 and browser.process.killed
    assert browser.socket is None and not profile.exists()
    browser.close()  # cleanup remains safe to repeat after a partial failure
