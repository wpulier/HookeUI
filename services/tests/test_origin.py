from orchestrator.origin import allowed_origin


def test_only_pages_from_this_mac_may_connect():
    assert allowed_origin("http://127.0.0.1:8770")
    assert allowed_origin("http://localhost:5180")
    assert allowed_origin(None)  # local tools (harness, scripts) send no Origin
    assert not allowed_origin("null")  # sandboxed research lessons
    assert not allowed_origin("https://example.com")
    assert not allowed_origin("http://127.0.0.1.evil.com")
