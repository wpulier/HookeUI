"""The retention registry at its limit: a temporary store that is gone protects nothing and is set aside (29 Oct)."""
import json
import tempfile
from pathlib import Path

import pytest

from orchestrator import scientific_retention as retention


def registered(root: Path, store: Path) -> Path:
    path = retention._association(root, store)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"store": str(store), "runtimeRoot": str(root)}))
    return path


def test_at_the_limit_gone_temporary_stores_are_set_aside_with_a_receipt_and_a_new_store_registers(tmp_path, monkeypatch):
    """29 Oct: 1009 of the 1024 registrations were tests' homes from 17-23 Oct in the system's temporary folder, long
    gone, and every new Project store was refused ('the saved Project store registration limit was reached'), which
    stopped a voice-run experiment. Those are moved aside (never deleted); a store gone from elsewhere (an unplugged
    disk) still counts and still blocks."""
    monkeypatch.setattr(retention, "MAX_STORES", 3)
    root = (tmp_path / "envs").resolve()
    (root / retention.METADATA).mkdir(parents=True)
    temp = Path(tempfile.gettempdir()).resolve()
    gone = [registered(root, temp / f"micromind-overlay-lifecycle-gone{i}" / "results" / "projects") for i in range(2)]
    disk = registered(root, Path("/Volumes/Unplugged/results/projects"))  # unavailable, not temporary: kept
    new = (tmp_path / "home" / "results" / "projects").resolve()
    new.mkdir(parents=True)
    retention._register(root, new)
    assert retention._association(root, new).exists(), "the new store registered"
    assert not any(p.exists() for p in gone) and disk.exists()
    aside = list((root / retention.METADATA / retention.STALE).iterdir())
    assert len(aside) == 1
    receipt = json.loads((aside[0] / "receipt.json").read_text())
    assert sorted(r["name"] for r in receipt["records"]) == sorted(p.name for p in gone)
    assert all((aside[0] / p.name).exists() for p in gone), "kept beside their receipt, never deleted"


def test_at_the_limit_with_nothing_stale_it_still_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(retention, "MAX_STORES", 1)
    root = (tmp_path / "envs").resolve()
    (root / retention.METADATA).mkdir(parents=True)
    registered(root, Path("/Volumes/Unplugged/results/projects"))
    new = (tmp_path / "home").resolve()
    new.mkdir()
    with pytest.raises(retention.Uncertain, match="registration limit"):
        retention._register(root, new)
