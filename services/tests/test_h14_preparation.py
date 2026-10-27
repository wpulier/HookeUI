"""H14 preparation (GO lane-qualification 2): the frozen study's analysis, task packages, oracles, evidence records
and the T13 server, checked on synthetic data only. No participant and no app takes part."""
import importlib.util
import json
import math
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

import numpy as np
import pytest

from bench import h14_analysis as A
from bench import h14_dryrun, h14_fixtures, h14_remote_server, h14_scoring, h14_tasks
from bench import qualification_evidence as Q

ROOT = Path(__file__).resolve().parents[2]
QUAL = ROOT / "services/bench/qualification"
INVENTORY = ROOT / "services/bench/specification/atomic-cases.json"


def attempt(**kw):
    base = {"participant": "P1", "sequence": "S1", "stratum": "naive", "site": "s1", "task": "T1", "app": "hooke",
            "variant": "D1", "period": 1, "time_s": 100.0, "cap_s": 360.0, "done": True, "correct": True,
            "max_level": 0, "l1_count": 0, "lost": False, "app_caused_wrong": False}
    return {**base, **kw}


# --- the frozen files and the code agree ------------------------------------------------------------------------

def test_frozen_hashes_match_the_files_the_code_names():
    for name, sha in Q.FROZEN.items():
        line = (QUAL / name.replace(".md", ".sha256")).read_text()
        assert sha in line and name in line
    assert A.DESIGN_SHA256 == Q.FROZEN["h14-study-design.md"]


def test_tango_matches_the_frozen_power_simulation():
    spec = importlib.util.spec_from_file_location("frozen_power", QUAL / "h14_power_simulation.py")
    frozen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(frozen)
    for b, c, n in [(3, 5, 120), (0, 0, 120), (10, 2, 120), (2, 14, 120), (7, 7, 40)]:
        assert A.tango_lower(b, c, n) == pytest.approx(frozen.tango_lower(b, c, n), abs=1e-6)


def test_every_qualify_usable_group_is_covered_by_a_task_or_the_whole_study():
    groups = {json.loads(line.rstrip().rstrip(","))["group"] for line in INVENTORY.read_text().splitlines()
              if '.qualify-usable"' in line}
    assert len(groups) == 71
    covered = set(h14_tasks.coverage()) | {"P8.execution", "P8.confinement", "Q.usable-workflow"}
    assert groups <= covered


def test_cards_never_name_a_control_and_render_for_every_variant(tmp_path):
    h14_fixtures.build(tmp_path, ["T1", "T2", "T7", "T12"])
    for card in tmp_path.rglob("card.txt"):
        text = card.read_text()
        assert "{" not in text and text.strip()
    with pytest.raises(ValueError):
        h14_tasks.BY_ID["T1"] = h14_tasks.Task("T1", 1, 60, (), "x", "x", "Click the menu at time {t}")
        try:
            h14_tasks.card("T1", {"t": 1})
        finally:
            h14_tasks.BY_ID["T1"] = next(t for t in h14_tasks.TASKS if t.id == "T1")


# --- the rules of §6 ------------------------------------------------------------------------------------------

def test_ucc_and_the_composite_follow_the_design():
    assert A.ucc(attempt()) and A.composite(attempt()) == pytest.approx(math.log(100))
    for bad in (attempt(done=False), attempt(time_s=400.0), attempt(correct=False), attempt(max_level=2), attempt(l1_count=3)):
        assert not A.ucc(bad) and A.composite(bad) == pytest.approx(math.log(720))
    assert A.ucc(attempt(max_level=1, l1_count=2))


def paired(task, n, hooke_ok=1.0, napari_ok=1.0, rng=None, **hooke_kw):
    rng = rng or np.random.default_rng(0)
    out = []
    for i in range(n):
        seq = h14_dryrun.allocation(n)[i]
        for app, p in (("hooke", hooke_ok), ("napari", napari_ok)):
            period, variant = h14_dryrun.slot(seq, task, app)
            ok = bool(rng.random() < p)
            kw = {"participant": f"P{i}", "sequence": seq, "task": task, "app": app, "period": period, "variant": variant,
                  "time_s": float(np.clip(150 * math.exp(rng.normal(0, 0.3)), 10, 360)), "correct": ok}
            out.append(attempt(**{**kw, **(hooke_kw if app == "hooke" and i == 0 else {})}))
    return out


def test_a_hard_rule_fails_the_task_whatever_the_statistics():
    assert A.comparison_task("T1", paired("T1", 60, 0.95, 0.85, max_level=4))["verdict"] == "fail"
    assert A.comparison_task("T1", paired("T1", 60, 0.95, 0.85, app_caused_wrong=True, correct=False))["verdict"] == "fail"


def test_a_worse_hooke_is_never_a_pass_and_an_equal_one_can_be():
    worse = A.comparison_task("T3", paired("T3", 120, 0.65, 0.92, rng=np.random.default_rng(3)))
    assert worse["verdict"] == "inconclusive" and not worse["H3_completion"]["pass"]
    equal = A.comparison_task("T3", paired("T3", 120, 0.93, 0.9, rng=np.random.default_rng(4)))
    assert equal["sensitivity"]["glmm"]["estimable"] and equal["sensitivity"]["lmm"]["estimable"]
    assert equal["verdict"] == "pass", equal


def test_separation_uses_the_exact_conditional_fallback_of_amendment_2():
    r = A.comparison_task("T3", paired("T3", 60, 1.0, 1.0))
    glmm = r["sensitivity"]["glmm"]
    assert not glmm["estimable"] and "Amendment 2" in glmm["fallback"] and glmm["lower"] == 0.0
    assert r["sensitivity"]["agrees_with_H3"] and r["verdict"] in ("pass", "inconclusive")
    assert r["verdict"] == "pass" or not r["H2_time"]["pass"]


def test_the_exact_conditional_bound():
    assert A.exact_conditional_lower(0, 0, 100) == 0.0
    assert A.exact_conditional_lower(0, 5, 100) == pytest.approx(-0.05)  # all five favour napari: u = 1
    assert A.exact_conditional_lower(5, 0, 100) > 0                 # five pairs all favour Hooke
    assert -0.15 < A.exact_conditional_lower(6, 6, 250) < 0


def test_hooke_only_rule_is_clopper_pearson_at_080():
    def run(x, n=120):
        return A.hooke_only_task("U1", [attempt(participant=f"P{i}", task="U1", correct=i < x) for i in range(n)])
    assert run(104)["verdict"] == "pass" and run(103)["verdict"] == "inconclusive"
    assert A.clopper_pearson_lower(104, 120) >= 0.80 > A.clopper_pearson_lower(103, 120)


def test_bca_is_reproducible_and_close_to_t_for_normal_data():
    d = np.random.default_rng(5).normal(0.05, 0.6, 120)
    assert A.bca_upper(d) == A.bca_upper(d)
    assert A.bca_upper(d) == pytest.approx(A.t_upper(d), abs=0.03)


def test_validation_refuses_duplicates_and_impossible_records():
    with pytest.raises(A.AnalysisError):
        A.validate([attempt(), attempt()])
    with pytest.raises(A.AnalysisError):
        A.validate([attempt(task="U1", app="napari")])
    with pytest.raises(A.AnalysisError):
        A.validate([attempt(lost=True, app_caused_wrong=True)])


# --- packages and oracles -------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def packages(tmp_path_factory):
    out = tmp_path_factory.mktemp("h14pk")
    h14_fixtures.build(out, scale="small")
    return out


def test_packages_are_deterministic_and_variants_differ(packages, tmp_path):
    h14_fixtures.build(tmp_path, ["T1", "T7"])
    for f in ("T7/D1/nuclei-labels.tif", "T7/D1/params.json", "T1/D2/five-d.tif"):   # OME-TIFF too (fixed UUID)
        assert (tmp_path / f).read_bytes() == (packages / f).read_bytes(), f
    assert (packages / "T7/D1/nuclei.tif").read_bytes() != (packages / "T7/D2/nuclei.tif").read_bytes()
    lines = (packages / "PACKAGE.sha256").read_text().splitlines()
    assert len(lines) > 100 and all(len(line.split()[0]) == 64 for line in lines)


def test_every_oracle_accepts_the_right_answer_and_refuses_a_wrong_one(packages):
    for task in h14_scoring.SCORERS:
        for variant in ("D1", "D2"):
            pkg = packages / task / variant
            assert h14_scoring.score(pkg, h14_dryrun.ideal(task, pkg))["correct"], (task, variant)
            assert not h14_scoring.score(pkg, h14_dryrun.wrong(task, pkg, np.random.default_rng(0)))["correct"], (task, variant)


def test_t9_reads_a_csv_with_the_apps_own_column_names(packages, tmp_path):
    pkg = packages / "T9/D1"
    rows = json.loads((pkg / "params.json").read_text())["expect"]["rows"]
    csv = tmp_path / "m.csv"
    csv.write_text("index,Area (µm²),Mean intensity\n" + "".join(f"{r['label']},{r['area_um2']!r},{r['mean_intensity']!r}\n" for r in rows))
    assert h14_scoring.score(pkg, {"csv": str(csv)})["correct"]


def test_big_zarr_value_is_what_the_oracle_expects(tmp_path):
    h14_fixtures.write_ome_zarr(tmp_path / "b.zarr", (2, 600, 700), levels=2)
    meta = json.loads((tmp_path / "b.zarr/0/.zarray").read_text())
    z, y, x = 1, 517, 650
    chunk = np.frombuffer((tmp_path / f"b.zarr/0/{z}/{y // 512}/{x // 512}").read_bytes(), "<u2").reshape(meta["chunks"][1:])
    assert chunk[y % 512, x % 512] == h14_fixtures.big_value(z, y, x)
    lvl1 = np.frombuffer((tmp_path / "b.zarr/1/0/0/0").read_bytes(), "<u2").reshape(512, 512)
    assert lvl1[10, 20] == h14_fixtures.big_value(0, 20, 40)


def test_dry_run_scenario_makes_the_silent_wrong_task_fail(packages):
    attempts, _ = h14_dryrun.simulate(packages, "silent_wrong_T8", n=24)
    assert A.comparison_task("T8", attempts)["verdict"] == "fail"


# --- evidence records and the manifest -------------------------------------------------------------------------

def record():
    sha = "a" * 64
    return {"format": Q.FORMAT, "case": "Q.clean-preparation.step-2-install-hooke", "group": "Q.clean-preparation",
            "aspect": "platform-linux", "artifact": {"app": sha, "sdk": "1", "bundle": sha, "environment_lock": sha, "model": None},
            "target": {"os": "Ubuntu", "build": "24.04.3", "arch": "x86_64", "hardware": "PC1", "gpu": "RTX", "driver": "550",
                       "image": sha, "reset": "vendor media 2026-11-01", "instance": "1"},
            "fixtures": {"check.tif": sha}, "procedure": Q.FROZEN["q-procedures.md"] + "#2", "started": "t0", "ended": "t1",
            "result": "pass", "interventions": [], "retries": [], "limitations": "", "files": {"inventory.json": sha},
            "operator": "op", "reviewer": "rev", "signatures": []}


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen")
def test_records_sign_and_verify_and_tampering_is_refused(tmp_path):
    key = tmp_path / "rev"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "rev", "-f", str(key)], check=True)
    allowed = tmp_path / "allowed_signers"
    allowed.write_text(f"rev namespaces=\"{Q.NAMESPACE}\" {(tmp_path / 'rev.pub').read_text()}")
    signed = Q.sign(record(), key, "rev")
    assert Q.verify(signed, allowed) == ["rev"]
    with pytest.raises(Q.EvidenceError):
        Q.verify({**signed, "result": "fail"}, allowed)
    with pytest.raises(Q.EvidenceError):
        Q.verify({**signed, "signatures": [{**signed["signatures"][0], "signer": "someone-else"}]}, allowed)


def test_validation_refuses_weak_records():
    with pytest.raises(Q.EvidenceError):
        Q.validate({**record(), "reviewer": "op"})
    with pytest.raises(Q.EvidenceError):
        Q.validate({**record(), "procedure": "0" * 64 + "#2"})
    with pytest.raises(Q.EvidenceError):
        Q.validate({**record(), "files": {}})
    Q.validate(record())


def test_the_manifest_template_cannot_start_the_study():
    template = json.loads((QUAL / "templates/h14-binding-manifest.template.json").read_text())
    with pytest.raises(Q.EvidenceError, match="null fields"):
        Q.check_manifest(template)
    assert template["design_sha256"] == Q.FROZEN["h14-study-design.md"]
    record_template = json.loads((QUAL / "templates/evidence-record.template.json").read_text())
    assert set(record_template) == set(Q.REQUIRED)


# --- the T13 server -------------------------------------------------------------------------------------------

def test_remote_server_cuts_once_then_serves_again(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    server = h14_remote_server.serve(tmp_path, port=0, after=2, seconds=0.4)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/a.txt"

    def get():
        try:
            return urllib.request.urlopen(url, timeout=2).read()
        except Exception:
            return None
    try:
        assert get() == b"x" and get() == b"x"
        assert get() is None
        import time
        time.sleep(0.5)
        assert get() == b"x"
    finally:
        server.shutdown()
