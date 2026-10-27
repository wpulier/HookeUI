"""Power of the H14 per-task rules H2 and H3 (h14-study-design.md §2.3, §6), by Monte Carlo.

Run with any Python that has numpy and scipy: `python h14_power_simulation.py`. Seeded, so it reproduces the table in
§2.3 to within simulation error. H3 is the Tango (1998) score lower bound for a paired difference of proportions; H2
is the one-sided t upper bound of the mean paired log composite time. Truly equal apps are assumed.
"""
import math

import numpy as np
from scipy.optimize import brentq
from scipy.stats import t

Z = 1.6448536269514722  # one-sided 95%


def tango_lower(b, c, n):
    """Lower one-sided 95% bound of p1 - p2, where b = pairs only app 1 succeeded, c = only app 2."""
    def z(d):
        a = 2 * n
        bb = -b - c + (2 * n - b + c) * d
        cc = -c * d * (1 - d)
        q = (math.sqrt(max(bb * bb - 4 * a * cc, 0.0)) - bb) / (2 * a)
        v = n * (2 * q + d * (1 - d))
        return (b - c - n * d) / math.sqrt(v) if v > 0 else (math.inf if b - c - n * d > 0 else -math.inf)
    hi = min((b - c) / n, 1 - 1e-9)
    lo = -1 + 1e-9
    if z(lo) < Z:
        return lo
    return brentq(lambda d: z(d) - Z, lo, hi) if z(hi) < Z else hi


def h3_power(n, psi, rng, p=0.9, margin=0.15, reps=3000):
    cells = [p - psi / 2, psi / 2, psi / 2, 1 - p - psi / 2]  # both, only Hooke, only napari, neither
    counts = rng.multinomial(n, cells, size=reps)
    return float(np.mean([tango_lower(x[1], x[2], n) > -margin for x in counts]))


def h2_power(n, sd, rng, margin=math.log(1.25), reps=20000):
    d = rng.normal(0.0, sd, (reps, n))
    upper = d.mean(1) + t.ppf(0.95, n - 1) * d.std(1, ddof=1) / math.sqrt(n)
    return float(np.mean(upper < margin))


if __name__ == "__main__":
    rng = np.random.default_rng(20261020)
    for n in (40, 80, 96, 120):
        for psi in (0.10, 0.16):
            task = h3_power(n, psi, rng) * h2_power(n, 0.6, rng)
            print(f"n={n} discordance={psi}: per task {task:.3f}, all 15 tasks {task ** 15:.3f}")
