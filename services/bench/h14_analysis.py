"""The H14 study's analysis, exactly as the frozen design says (qualification/h14-study-design.md §6, §7.4, §7.5).

Input: one JSON object per attempt (`attempts.jsonl`, the fields of ATTEMPT_FIELDS), already scored by
bench.h14_scoring. Output: one JSON with every task's bounds, rules and verdicts, and the evidence binding.

Per comparison task, all must hold (§6):
- H1a: no Hooke attempt at intervention level 4 (developer repair);
- H1b: no Hooke attempt with an app-caused wrong accepted result;
- H2: the one-sided 95% upper bound of mean D = Y(Hooke) - Y(napari) is below ln(1.25), by the t-interval and by a
  BCa bootstrap (10,000 resamples, seed 20261020); both must pass;
- H3: the Tango score one-sided 95% lower bound of p(UCC, Hooke) - p(UCC, napari) is above -0.15;
- the mixed-model sensitivity analyses (§7.4) must agree with H2 and H3 on pass or fail.
Per Hooke-only task: H1a, H1b, and a one-sided 95% Clopper-Pearson lower bound of UCC of at least 0.80.

A bound that crosses its margin is inconclusive, which is unqualified. When the logistic GLMM has no finite estimate
(complete separation), the H3 sensitivity analysis is the exact conditional test on the discordant pairs (design
§7.4, Amendment 2). N is 250 (Amendment 3). Nothing here widens a margin or drops a rule. This module only reads data; it never runs a
session, and its verdicts are not qualification until an independent reviewer signs the evidence record.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from scipy import optimize, stats

DESIGN_SHA256 = "3a5b915760559eed21f926e0cd6d115fc1a70dd9fccc7c032248bcf23aeaa80d"
ALPHA = 0.05
TIME_MARGIN = math.log(1.25)
UCC_MARGIN = 0.15
CP_FLOOR = 0.80
BOOT_RESAMPLES = 10_000
BOOT_SEED = 20261020
COMPARISON = tuple(f"T{i}" for i in range(15))
HOOKE_ONLY = tuple(f"U{i}" for i in range(1, 8))
APPS = ("napari", "hooke")
ATTEMPT_FIELDS = {
    "participant": str, "sequence": str, "stratum": str, "site": str, "task": str, "app": str,
    "variant": str, "period": int, "time_s": (int, float), "cap_s": (int, float), "done": bool,
    "correct": bool, "max_level": int, "l1_count": int, "lost": bool, "app_caused_wrong": bool,
}


class AnalysisError(ValueError):
    pass


# --- per attempt (§6 definitions, §7.1 levels) -------------------------------------------------------------------

def ucc(a: dict) -> bool:
    """Unassisted correct completion: done within the cap, correct, nothing above level 1, at most two level-1 prompts."""
    return bool(a["done"] and a["time_s"] <= a["cap_s"] and a["correct"] and a["max_level"] <= 1 and a["l1_count"] <= 2)


def composite(a: dict) -> float:
    """Y: ln(min(time, cap)) for a UCC attempt, ln(2 x cap) for any other attempt."""
    return math.log(min(a["time_s"], a["cap_s"])) if ucc(a) else math.log(2 * a["cap_s"])


def validate(attempts: list[dict]) -> None:
    for i, a in enumerate(attempts):
        for k, t in ATTEMPT_FIELDS.items():
            if k not in a or not isinstance(a[k], t) or (t is int and isinstance(a[k], bool)):
                raise AnalysisError(f"attempt {i}: field {k!r} missing or not {t}")
        if a["app"] not in APPS or a["task"] not in COMPARISON + HOOKE_ONLY:
            raise AnalysisError(f"attempt {i}: unknown app or task")
        if a["task"] in HOOKE_ONLY and a["app"] != "hooke":
            raise AnalysisError(f"attempt {i}: {a['task']} is Hooke-only")
        if not 0 <= a["max_level"] <= 4 or a["time_s"] < 0 or a["cap_s"] <= 0:
            raise AnalysisError(f"attempt {i}: level, time or cap out of range")
        if a["lost"] and a["app_caused_wrong"]:
            raise AnalysisError(f"attempt {i}: an app-caused result is a failure, never lost (§7.2)")
    seen = set()
    for a in attempts:
        key = (a["participant"], a["task"], a["app"])
        if key in seen:
            raise AnalysisError(f"two attempts for {key}: one attempt per task per app (§2.2)")
        seen.add(key)


# --- interval methods ------------------------------------------------------------------------------------------

def tango_lower(b: int, c: int, n: int, alpha: float = ALPHA) -> float:
    """Tango (1998) score lower bound of p1 - p2 for paired binary data; b = only app 1 succeeded, c = only app 2."""
    z = stats.norm.ppf(1 - alpha)

    def score(d):
        a2 = 2 * n
        bb = -b - c + (2 * n - b + c) * d
        cc = -c * d * (1 - d)
        q = (math.sqrt(max(bb * bb - 4 * a2 * cc, 0.0)) - bb) / (2 * a2)
        v = n * (2 * q + d * (1 - d))
        diff = b - c - n * d
        return diff / math.sqrt(v) if v > 0 else (math.inf if diff > 0 else (-math.inf if diff < 0 else 0.0))

    hi = min((b - c) / n, 1 - 1e-12)
    lo = -1 + 1e-12
    if score(lo) < z:
        return -1.0
    if score(hi) >= z:
        return hi
    return optimize.brentq(lambda d: score(d) - z, lo, hi, xtol=1e-12)


def t_upper(d: np.ndarray, alpha: float = ALPHA) -> float:
    n = len(d)
    if n < 2:
        return math.inf
    sd = float(np.std(d, ddof=1))
    return float(np.mean(d)) + stats.t.ppf(1 - alpha, n - 1) * sd / math.sqrt(n)


def bca_upper(d: np.ndarray, alpha: float = ALPHA, resamples: int = BOOT_RESAMPLES, seed: int = BOOT_SEED) -> float:
    """One-sided (1 - alpha) BCa upper bound of the mean (Efron 1987), with a fixed seed so it reproduces."""
    n = len(d)
    theta = float(np.mean(d))
    if n < 2 or np.all(d == d[0]):
        return theta
    rng = np.random.default_rng(seed)
    boots = d[rng.integers(0, n, size=(resamples, n))].mean(axis=1)
    p = np.clip(np.mean(boots < theta), 1 / (resamples + 1), resamples / (resamples + 1))
    z0 = stats.norm.ppf(p)
    jack = (d.sum() - d) / (n - 1)
    dev = jack.mean() - jack
    denom = 6 * (np.sum(dev ** 2) ** 1.5)
    acc = float(np.sum(dev ** 3) / denom) if denom > 0 else 0.0
    zq = stats.norm.ppf(1 - alpha)
    adj = stats.norm.cdf(z0 + (z0 + zq) / (1 - acc * (z0 + zq)))
    return float(np.quantile(boots, adj))


def clopper_pearson_lower(x: int, n: int, alpha: float = ALPHA) -> float:
    return 0.0 if x == 0 else float(stats.beta.ppf(alpha, x, n - x + 1))


# --- mixed-model sensitivity (§7.4) ----------------------------------------------------------------------------

def crossover_upper(d: np.ndarray, hooke_first: np.ndarray, hooke_d1: np.ndarray, alpha: float = ALPHA) -> dict:
    """App effect on Y from the linear mixed model with app, period and variant fixed and participant random.

    With one attempt per app per participant and no missing cells, that model's REML estimate of the app effect is
    the intercept of the ordinary regression of D on effect-coded period and variant (Jones & Kenward, crossover
    analysis): the participant's random intercept cancels in D. Returns the estimate and its one-sided t bound.
    """
    n = len(d)
    x = np.column_stack([np.ones(n), np.where(hooke_first, 1.0, -1.0), np.where(hooke_d1, 1.0, -1.0)])
    x = x[:, [0] + [j for j in (1, 2) if np.ptp(x[:, j]) > 0]]
    if n <= x.shape[1]:
        return {"estimable": False, "reason": "too few participants"}
    beta, *_ = np.linalg.lstsq(x, d, rcond=None)
    resid = d - x @ beta
    dof = n - x.shape[1]
    s2 = float(resid @ resid) / dof
    cov = s2 * np.linalg.inv(x.T @ x)
    upper = float(beta[0] + stats.t.ppf(1 - alpha, dof) * math.sqrt(cov[0, 0]))
    return {"estimable": True, "estimate": float(beta[0]), "upper": upper}


GH_NODES, GH_WEIGHTS = np.polynomial.hermite.hermgauss(30)


def _glmm_terms(params, y, h, per, var, pid, n_p):
    mu, log_s, b_app, b_per, b_var = params
    s = math.exp(log_s)
    alpha = mu + math.sqrt(2) * s * GH_NODES                               # quadrature points of the random intercept
    eta = alpha[None, :] + (b_app * h + b_per * per + b_var * var)[:, None]
    ll = y[:, None] * eta - np.logaddexp(0, eta)                         # per attempt, per node
    per_participant = np.zeros((n_p, len(GH_NODES)))
    np.add.at(per_participant, pid, ll)
    m = per_participant.max(axis=1, keepdims=True)
    return float(np.sum(m[:, 0] + np.log(np.exp(per_participant - m) @ GH_WEIGHTS / math.sqrt(math.pi))))


def exact_conditional_lower(b: int, c: int, n: int, alpha: float = ALPHA) -> float:
    """Amendment 2: with m = b + c discordant pairs, c favouring napari, the lower bound of the risk difference is
    (m / n) x (1 - 2u), u the one-sided Clopper-Pearson upper bound of c / m; 0 when there are no discordant pairs."""
    m = b + c
    if m == 0:
        return 0.0
    u = 1.0 if c == m else float(stats.beta.ppf(1 - alpha, c + 1, m - c))
    return (m / n) * (1 - 2 * u)


def glmm_rd_lower(rows: list[tuple[int, int, int, int, int]], alpha: float = ALPHA) -> dict:
    """Random-intercept logistic GLMM (app, period, variant fixed; participant random), 30-node Gauss-Hermite.

    rows: (participant index, ucc 0/1, hooke 0/1, period-2 0/1, variant-D2 0/1). The margin is on the risk scale,
    so the marginal risk difference (Hooke minus napari, averaged over the random intercept and over balanced period
    and variant) gets a one-sided delta-method lower bound from the observed information.
    """
    arr = np.asarray(rows, dtype=float)
    pid = arr[:, 0].astype(int)
    y, h, per, var = arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4]
    n_p = int(pid.max()) + 1
    if y.min() == y.max() or len({(hh, yy) for hh, yy in zip(h, y)}) < 4:
        return {"estimable": False, "reason": "no variation in success within an app (separation)"}

    def nll(p):
        return -_glmm_terms(p, y, h, per, var, pid, n_p)

    p0 = np.array([stats.logistic.ppf(np.clip(y.mean(), 0.02, 0.98)), 0.0, 0.0, 0.0, 0.0])
    fit = optimize.minimize(nll, p0, method="BFGS", options={"gtol": 1e-7, "maxiter": 2000})
    if not np.all(np.isfinite(fit.x)) or abs(fit.x[2]) > 15 or fit.x[1] < -12:
        return {"estimable": False, "reason": "fit did not converge to a finite interior estimate"}

    def rd(p):
        mu, log_s, b_app, b_per, b_var = p
        alpha_nodes = mu + math.sqrt(2) * math.exp(log_s) * GH_NODES
        total = 0.0
        for per_v in (0, 1):
            for var_v in (0, 1):
                base = alpha_nodes + b_per * per_v + b_var * var_v
                total += GH_WEIGHTS @ (stats.logistic.cdf(base + b_app) - stats.logistic.cdf(base)) / math.sqrt(math.pi)
        return total / 4

    hess = _numeric_hessian(nll, fit.x)
    try:
        cov = np.linalg.inv(hess)
    except np.linalg.LinAlgError:
        return {"estimable": False, "reason": "singular information matrix"}
    grad = _numeric_gradient(rd, fit.x)
    var_rd = float(grad @ cov @ grad)
    if not np.isfinite(var_rd) or var_rd <= 0 or np.any(np.linalg.eigvalsh((hess + hess.T) / 2) <= 0):
        return {"estimable": False, "reason": "information matrix not positive definite"}
    est = float(rd(fit.x))
    return {"estimable": True, "estimate": est, "lower": est - stats.norm.ppf(1 - alpha) * math.sqrt(var_rd)}


def _numeric_gradient(f, x, h=1e-5):
    g = np.zeros_like(x)
    for i in range(len(x)):
        e = np.zeros_like(x)
        e[i] = h
        g[i] = (f(x + e) - f(x - e)) / (2 * h)
    return g


def _numeric_hessian(f, x, h=1e-4):
    k = len(x)
    out = np.zeros((k, k))
    for i in range(k):
        for j in range(i, k):
            ei = np.zeros(k)
            ej = np.zeros(k)
            ei[i] = h
            ej[j] = h
            out[i, j] = out[j, i] = (f(x + ei + ej) - f(x + ei - ej) - f(x - ei + ej) + f(x - ei - ej)) / (4 * h * h)
    return out


# --- per task ---------------------------------------------------------------------------------------------------

def comparison_task(task: str, attempts: list[dict]) -> dict:
    mine = [a for a in attempts if a["task"] == task]
    hooke_all = [a for a in mine if a["app"] == "hooke"]
    h1a = not any(a["max_level"] == 4 for a in hooke_all)
    h1b = not any(a["app_caused_wrong"] for a in hooke_all)
    usable = [a for a in mine if not a["lost"]]
    by_p: dict[str, dict[str, dict]] = {}
    for a in usable:
        by_p.setdefault(a["participant"], {})[a["app"]] = a
    pairs = [(p["hooke"], p["napari"]) for p in by_p.values() if set(p) == set(APPS)]
    n = len(pairs)
    out = {"task": task, "pairs": n, "lost": sum(a["lost"] for a in mine),
           "H1a_no_developer_repair": h1a, "H1b_no_silent_wrong_result": h1b}
    if n < 2:
        out.update(verdict="inconclusive", reason="fewer than two complete pairs")
        return out
    d = np.array([composite(hh) - composite(nn) for hh, nn in pairs])
    b = sum(ucc(hh) and not ucc(nn) for hh, nn in pairs)
    c = sum(ucc(nn) and not ucc(hh) for hh, nn in pairs)
    t_ub, bca_ub = t_upper(d), bca_upper(d)
    lb = tango_lower(b, c, n)
    h2 = t_ub < TIME_MARGIN and bca_ub < TIME_MARGIN
    h3 = lb > -UCC_MARGIN
    hooke_first = np.array([hh["period"] == 1 for hh, _ in pairs])
    hooke_d1 = np.array([hh["variant"] == "D1" for hh, _ in pairs])
    lmm = crossover_upper(d, hooke_first, hooke_d1)
    index = {pid: i for i, pid in enumerate(sorted({hh["participant"] for hh, _ in pairs}))}
    rows = [(index[a["participant"]], int(ucc(a)), int(a["app"] == "hooke"), int(a["period"] == 2), int(a["variant"] == "D2"))
            for pair in pairs for a in pair]
    glmm = glmm_rd_lower(rows)
    if not glmm["estimable"] and glmm["reason"].startswith("no variation"):
        glmm = {**glmm, "fallback": "exact conditional on the discordant pairs (Amendment 2)",
                "lower": exact_conditional_lower(b, c, n)}
    agree_h2 = lmm["estimable"] and ((lmm["upper"] < TIME_MARGIN) == h2)
    agree_h3 = "lower" in glmm and ((glmm["lower"] > -UCC_MARGIN) == h3)
    out.update({
        "ucc_rate": {"hooke": round(sum(ucc(hh) for hh, _ in pairs) / n, 4),
                     "napari": round(sum(ucc(nn) for _, nn in pairs) / n, 4)},
        "discordant": {"hooke_only": b, "napari_only": c},
        "H2_time": {"mean_D": float(d.mean()), "t_upper": t_ub, "bca_upper": bca_ub,
                    "margin": TIME_MARGIN, "pass": h2},
        "H3_completion": {"difference": (b - c) / n, "tango_lower": lb, "margin": -UCC_MARGIN, "pass": h3},
        "sensitivity": {"lmm": lmm, "glmm": glmm, "agrees_with_H2": agree_h2, "agrees_with_H3": agree_h3},
    })
    if not (h1a and h1b):
        out.update(verdict="fail", reason="a hard rule failed (H1a or H1b)")
    elif h2 and h3 and agree_h2 and agree_h3:
        out.update(verdict="pass", reason="H1a, H1b, H2 and H3 hold and the sensitivity analyses agree")
    elif not (agree_h2 and agree_h3):
        out.update(verdict="inconclusive", reason="a sensitivity analysis disagrees or cannot be estimated (§6)")
    else:
        out.update(verdict="inconclusive", reason="a bound crosses its margin (§6)")
    return out


def hooke_only_task(task: str, attempts: list[dict]) -> dict:
    mine = [a for a in attempts if a["task"] == task]
    h1a = not any(a["max_level"] == 4 for a in mine)
    h1b = not any(a["app_caused_wrong"] for a in mine)
    usable = [a for a in mine if not a["lost"]]
    n, x = len(usable), sum(ucc(a) for a in usable)
    lb = clopper_pearson_lower(x, n) if n else 0.0
    out = {"task": task, "attempts": n, "ucc": x, "cp_lower": lb, "floor": CP_FLOOR,
           "H1a_no_developer_repair": h1a, "H1b_no_silent_wrong_result": h1b}
    if not (h1a and h1b):
        out.update(verdict="fail", reason="a hard rule failed (H1a or H1b)")
    elif lb >= CP_FLOOR:
        out.update(verdict="pass", reason="H1a, H1b and the Clopper-Pearson rule hold")
    else:
        out.update(verdict="inconclusive", reason="the Clopper-Pearson lower bound is below 0.80")
    return out


def analyse(attempts: list[dict], *, required_completers: int = 250, binding_sha256: str | None = None,
            data_sha256: str | None = None) -> dict:
    validate(attempts)
    completers = {a["participant"] for a in attempts}
    tasks = {t: comparison_task(t, attempts) for t in COMPARISON}
    tasks.update({t: hooke_only_task(t, attempts) for t in HOOKE_ONLY})
    short = len(completers) < required_completers
    all_pass = all(r["verdict"] == "pass" for r in tasks.values()) and not short
    return _plain({
        "format": "hooke-h14-analysis/1",
        "design_sha256": DESIGN_SHA256,
        "binding_manifest_sha256": binding_sha256,
        "locked_data_sha256": data_sha256,
        "completers": len(completers),
        "required_completers": required_completers,
        "tasks": tasks,
        "study_verdict": "pass" if all_pass else ("inconclusive" if not any(r["verdict"] == "fail" for r in tasks.values()) else "fail"),
        "note": ("Analysis output only. It is not qualification until the independent reviewer signs the evidence "
                 "record (q-procedures.md §6)." + (" Fewer completers than the binding manifest requires." if short else "")),
    })


def _plain(obj):
    """Plain JSON types only (numpy scalars from scipy become bool, int and float)."""
    return json.loads(json.dumps(obj, default=lambda v: v.item() if hasattr(v, "item") else str(v)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("attempts", type=Path, help="locked attempts.jsonl")
    ap.add_argument("--manifest", type=Path, help="the frozen h14-binding-manifest.json")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    raw = args.attempts.read_bytes()
    attempts = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    required, binding = 250, None
    if args.manifest:
        from bench import qualification_evidence
        manifest = qualification_evidence.check_manifest(json.loads(args.manifest.read_text()))
        required, binding = int(manifest["study"]["completers"]), hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    result = analyse(attempts, required_completers=required, binding_sha256=binding,
                     data_sha256=hashlib.sha256(raw).hexdigest())
    text = json.dumps(result, indent=1, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n")
    print(text if not args.out else f"{result['study_verdict']}: written to {args.out}")


if __name__ == "__main__":
    main()
