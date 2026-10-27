"""Opt-in bounded host observations; no device synchronization or kernel timing."""
from __future__ import annotations

import os
import resource
import time


class ClickProbe:
    @classmethod
    def start(cls, request):
        if request.get("_execution") and os.environ.get("MICROMIND_CLICK_DIAGNOSTICS") == "1":
            return cls()
        return None

    def __init__(self):
        self.facts = {}
        self.wall = self.started = time.perf_counter()
        self.cpu = time.thread_time()
        self.process = time.process_time()
        self.usage = resource.getrusage(resource.RUSAGE_SELF)

    def mark(self, phase):
        wall, cpu = time.perf_counter(), time.thread_time()
        self.facts[f"probe{phase}WallMs"] = round((wall - self.wall) * 1000, 3)
        self.facts[f"probe{phase}ThreadMs"] = round((cpu - self.cpu) * 1000, 3)
        self.wall, self.cpu = wall, cpu

    def finish(self, result):
        self.mark("Readback")
        usage = resource.getrusage(resource.RUSAGE_SELF)
        self.facts.update(probeWallMs=round((self.wall - self.started) * 1000, 3),
                          probeProcessMs=round((time.process_time() - self.process) * 1000, 3),
                          probeMinorFaults=usage.ru_minflt - self.usage.ru_minflt,
                          probeMajorFaults=usage.ru_majflt - self.usage.ru_majflt)
        result["_clickDiagnostics"] = self.facts
        return result
