"""The orchestrator's own CPU threads: how many OpenCV may use, and the scheduling class of the threads the
live view runs on (the event loop and the frame analysis thread).

Live 2026-10-11 02:36-02:39: frame analysis (decode, the eyepiece disc, the slide's motion, focus: hub._analyse_batch,
on its one analysis thread) went from 14 ms to 2.2 s p50 while the machine's load reached 142 on 14 cores. OpenCV
runs each call's parallel parts on its own pool, one thread per core by default; under contention such a call
waits for its slowest chunk. The GPU workers were capped (gpu_worker/boot.py); this is the same question for the
app's own process, answered by measurement (sim `model-load`'s baseline and outside-load phases, docs/bench/priority.md
"The orchestrator's own threads"):

- CV_THREADS: OpenCV's threads in this process (cv2.setNumThreads; 0 there means none beside the caller);
- FRAME_QOS: the macOS scheduling class the event loop and the analysis thread ask for
  (pthread_set_qos_class_self_np; "interactive" is QOS_CLASS_USER_INTERACTIVE, which a process may ask for its
  own threads without privileges; "" leaves them as they start).

MICROMIND_ORCH_CV_THREADS and MICROMIND_FRAME_QOS override them (the measurements used them).

Measured 2026-10-11 (sim `model-load`'s phases, quiet and under one and three busy processes per core): no cap
and no scheduling class helped. Under one per core every setting gave 30 fps and frame analysis p50 19-21 ms; under
three, 17-18 fps and p50 29-37 ms for all but one OpenCV thread, which was worse (14.8 fps, p50 57 ms) and slower
when quiet (13 ms against 10). So both stay off (OpenCV's default, the threads as they start): kept as settings so
the live app can be measured with them if its stall comes back. The frame analysis needs no other priority: under
three busy processes per core it stayed about 30 ms at p50 while the frame rate fell, so the analysis thread is not
what limits the view there.
"""
from __future__ import annotations

import ctypes
import os
import sys

CV_THREADS: int | None = None  # None: OpenCV's own default (one per core)
FRAME_QOS = ""
QOS = {"interactive": 0x21, "initiated": 0x19, "default": 0x15}  # <sys/qos.h>


def cv_threads() -> int | None:
    value = os.environ.get("MICROMIND_ORCH_CV_THREADS")
    if value is None or value.strip() == "":
        return CV_THREADS
    try:
        n = int(value)
    except ValueError:
        return CV_THREADS
    return n if n >= 0 else None


def apply_cv_threads() -> int | None:
    """OpenCV's threads in this process, as chosen (None: left as it is). Returns what was set."""
    n = cv_threads()
    if n is None:
        return None
    import cv2

    cv2.setNumThreads(n)
    return n


def frame_qos() -> str:
    value = os.environ.get("MICROMIND_FRAME_QOS")
    value = FRAME_QOS if value is None else value.strip().lower()
    return value if value in QOS else ""


def raise_this_thread() -> str:
    """Ask for the frame path's scheduling class for the calling thread (macOS); what was asked ("" if nothing)."""
    how = frame_qos()
    if not how or sys.platform != "darwin":
        return ""
    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        return how if libc.pthread_set_qos_class_self_np(ctypes.c_uint(QOS[how]), ctypes.c_int(0)) == 0 else ""
    except (OSError, AttributeError):
        return ""
