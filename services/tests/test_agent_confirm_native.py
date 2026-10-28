"""The Mac app's own signing code and the server agree (ADR 0026): an answer signed by AgentConfirmKey is accepted for
exactly that request, code and answer, and refused for anything else. Needs macOS and swiftc."""
import json
import platform
import shutil
import subprocess

import pytest

from orchestrator.door import native
from orchestrator.door.connections import Refused
from orchestrator.paths import REPO

pytestmark = pytest.mark.skipif(platform.system() != "Darwin" or shutil.which("swiftc") is None,
                                reason="needs macOS and swiftc")


def test_an_answer_signed_by_the_apps_code_is_accepted_only_for_what_it_signed(tmp_path):
    program = tmp_path / "agent-confirm"
    subprocess.run(["swiftc", "-swift-version", "5", str(REPO / "packaging/macos/HookeUI/AgentShared.swift"),
                    str(REPO / "packaging/macos/tests/agent-confirm/main.swift"), "-o", str(program)],
                   check=True, capture_output=True, timeout=300)
    pending, code = "pc_" + "a" * 24, "KX7-M3P"
    made = json.loads(subprocess.run([str(program), pending, code, "connect"], check=True, capture_output=True,
                                     text=True, timeout=30).stdout)
    environ = {"HOOKE_CONFIRM_KEY": made["publicKey"]}
    assert native.check(pending, code, "connect", made["signature"], studio_ok=False, environ=environ) == "native"
    for other in ((pending, code, "decline"), (pending, "AAA-AAA", "connect"), ("pc_" + "b" * 24, code, "connect")):
        with pytest.raises(Refused) as refused:
            native.check(*other, made["signature"], studio_ok=True, environ=environ)
        assert refused.value.code == "bad-confirm"
