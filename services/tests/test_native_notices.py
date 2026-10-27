"""Every native package an entry pins (env.native) has its notice in catalog/notices/conda-forge/: the same file and
sha256, its own licence files as recorded, and its component in the entry's licence (component accounting)."""
import hashlib
import json
from pathlib import Path

from gpu_worker import manifests as catalog
from orchestrator import licences

NOTICES = catalog.ROOT.parent / "notices" / "conda-forge"


def test_every_native_package_has_its_notice_its_licence_files_and_its_component():
    pinned = [(m, n) for m in catalog.manifests().values() for n in (m.env or {}).get("native", [])]
    assert pinned, "brainreg pins NiftyReg's native part"
    for m, n in pinned:
        stem = Path(n["url"]).name.removesuffix(".tar.bz2")
        notice = json.loads((NOTICES / stem / "notice.json").read_text())
        assert (notice["url"], notice["sha256"], notice["bytes"]) == (n["url"], n["sha256"], n["bytes"]), stem
        assert notice["licenceFiles"], stem
        for f in notice["licenceFiles"]:
            data = (NOTICES / stem / f["file"]).read_bytes()
            assert (len(data), hashlib.sha256(data).hexdigest()) == (f["bytes"], f["sha256"]), (stem, f["file"])
        assert notice["component"] in m.licence and notice["component"] in licences.COMPONENTS, (m.id, stem)


def test_every_notice_is_of_a_package_an_entry_pins():
    pinned = {Path(n["url"]).name.removesuffix(".tar.bz2")
              for m in catalog.manifests().values() for n in (m.env or {}).get("native", [])}
    assert {d.name for d in NOTICES.iterdir() if d.is_dir()} == pinned
