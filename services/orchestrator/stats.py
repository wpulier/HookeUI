"""Statistics over a table's numbers (the Prism chain Will set for Part 1 on 2026-10-23: measured cells, a table,
statistics, graphs), computed here in code and never by a model; the agent and the studio only say what this module
returned. Every function is pure and deterministic: the same values give the same numbers, byte for byte, with no I/O
and no randomness.

The conventions every function here keeps, so later analyses (Welch and paired t, Mann-Whitney, ANOVA with Tukey,
Kruskal-Wallis with Dunn, correlation, regression) can be added beside `describe` and read the same way:
- values come in as anything `numpy.asarray` takes and go through `finite` first: NaN and infinite values are not
  data, so they are left out and the count says how many were used;
- results are plain Python floats and ints (JSON as they are), and a number the data cannot give (the SD of one
  value) is None, never 0 or NaN;
- scipy is imported inside the function that needs it. `scipy.special` is warmed off the event loop at start
  (looplag.WARM_IMPORTS, through scipy.ndimage) as an optimization; table callers still use off-loop reads;
  `scipy.stats` takes about 3 s on its first import, so a function that needs it must be called off the loop."""
from __future__ import annotations

import math

import numpy as np


def finite(values) -> np.ndarray:
    """The values as a flat float array, without NaN or infinite values."""
    array = np.asarray(values, dtype=float).ravel()
    return array[np.isfinite(array)]


def t_critical(df: int, confidence: float = 0.95) -> float:
    """The two-sided Student t critical value for `df` degrees of freedom: scipy.stats.t.ppf((1 + confidence) / 2, df),
    by the scipy.special function that ppf itself calls."""
    from scipy.special import stdtrit

    if df < 1:
        raise ValueError("the t distribution needs at least one degree of freedom")
    return float(stdtrit(df, (1.0 + confidence) / 2.0))


def describe(values) -> dict:
    """n, mean, SD (n - 1), SEM, the mean's 95% confidence interval (Student t, n - 1 df), median, quartiles (linear,
    as numpy.percentile), min and max of the finite values. With none, only n (0) is a number; with one, the mean,
    median, quartiles, min and max are that value and SD, SEM and the interval are None."""
    data = finite(values)
    n = int(data.size)
    out: dict = {"n": n, "mean": None, "sd": None, "sem": None, "ci95": None,
                 "median": None, "q1": None, "q3": None, "min": None, "max": None}
    if n == 0:
        return out
    q1, median, q3 = np.percentile(data, [25, 50, 75])
    mean = float(np.mean(data))
    out.update(mean=mean, median=float(median), q1=float(q1), q3=float(q3),
               min=float(data.min()), max=float(data.max()))
    if n >= 2:
        sd = float(np.std(data, ddof=1))
        sem = sd / math.sqrt(n)
        half = t_critical(n - 1) * sem
        out.update(sd=sd, sem=sem, ci95=[mean - half, mean + half])
    return out
