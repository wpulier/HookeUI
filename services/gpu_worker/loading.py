"""Models loading on a GPU worker: which model a request loads, how long its load may take, how big it
is said to be until measured, and what the worker tells the app while it loads (orchestrator/gpu_client.py
reads these messages; orchestrator/loads.py says them, measures them and keeps memory within bounds).

Live 2026-10-11 02:38: micro-sam loaded cold for 120 s on the analysis worker and nothing said so; its
start counted against the 180 s outline timeout, and a load longer than a call's limit would have marked
the worker hung. A load is now told as it happens, with its own limit:

  {"id": <the request it is for>, "load": "loading", "model": key, "limitS": s}
  {"id": ..., "load": "loaded", "model": key, "seconds": s, "memoryMb": mb, "pid"?: a process started for it}
  {"id": ..., "load": "failed", "model": key, "seconds": s, "error": "..."}
  {"id": ..., "load": "unloaded", "model": key, "freedMb": mb}

Light on purpose: no heavy imports, so the orchestrator reads it without loading a model's libraries.
"""
from __future__ import annotations

# A model as people say it, the memory it is documented to take once loaded (MB) until this Mac has measured it
# (MICROMIND_HOME/models/loads.json), and how long a load may take before the worker counts as hung (s): a first load
# may download weights. All from the model's manifest (catalog/models/<id>/model.toml, gpu_worker/manifests.py); the test
# load is the sim harness's own.
from collections.abc import Mapping

from .manifests import local as _manifests

class _FromCatalog(Mapping):
    """A table read from the manifests each time it is asked (never built once at import), so an entry the catalog
    gains or changes is seen without a restart (the catalog audit's G9)."""

    def __init__(self, value, extra: dict | None = None) -> None:
        self._value, self._extra = value, extra or {}

    def _table(self) -> dict:
        return {**{m.id: self._value(m) for m in _manifests().values()}, **self._extra}

    def __getitem__(self, key):
        return self._table()[key]

    def __iter__(self):
        return iter(self._table())

    def __len__(self) -> int:
        return len(self._table())


NAMES = _FromCatalog(lambda m: m.name, {"test-load": "the test load"})
DOCUMENTED_MB = _FromCatalog(lambda m: m.documented_mb, {"test-load": 0})
DOCUMENTED_PEAK_MB = _FromCatalog(lambda m: max(m.peak_mb, m.documented_mb))
LOAD_LIMIT_S = _FromCatalog(lambda m: m.load_limit_s)
DEFAULT_MB = 2048  # a model with neither a measurement nor a documented size
DEFAULT_LOAD_LIMIT_S = 300.0  # a model with no manifest
# What a request loads: the model its op runs (a request whose model is already loaded loads nothing).
SEGMENT_DEFAULT, CELLS_DEFAULT, EMBED_DEFAULT = "edgetam", "cellpose-sam", "dinov2-small"


def model_of(op: str, req: dict) -> str | None:
    """The model a request to the worker needs loaded (None: none, or one it does not name)."""
    model = req.get("model")
    if op == "segment":
        return model or SEGMENT_DEFAULT
    if op == "cells":
        return model or CELLS_DEFAULT
    if op == "embed":
        return model or EMBED_DEFAULT
    if op in ("regions", "automask", "warmup"):
        return "edgetam"
    if op == "track":
        return "edgetam-video"
    if op == "zeroshot":
        return "medsiglip"
    if op.startswith("molmo_"):
        return "molmo2-4b"
    if op == "load":
        return model
    return None


def name(model: str | None) -> str:
    return NAMES.get(model or "", model or "a model")


def limit_s(model: str | None) -> float:
    return LOAD_LIMIT_S.get(model or "", DEFAULT_LOAD_LIMIT_S)
