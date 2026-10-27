"""Critical model journeys of the researcher repair, three real attempts each, every attempt kept
(docs/bench/research.md, "Journeys"). Each runs the real ResearchManager and research process with the real
models (keys from the keys file); nothing is faked but the camera (there is none: researchers work from words).

  J1 a long, specific request reaches the model whole (its last line is obeyed)
  J2 a question asked while it works is kept and answered when the run ends
  J3 "keep going" on gpt-6.1-sol continues from the package (no response chain)
  J4 "keep going" on gemini-3.8-flash, the same
  J5 "go deeper on that": a continue at standard effort
  J6 "remove the second question" from a lesson
  J7 "rename stoma to stomatal pore and remove the nucleus" on a labelled photo
  J8 "spawn a researcher to run 5 motion tests then compute the average", at quick effort (the brain's live choice
     on 2026-10-11, when a 75 s clock cut its write-up): five numbers and their average, written up (the camera is a
     stand-in giving known speeds; the model and the app's research path are real)
  J9 "make the presentation prettier" with a lesson: the lesson restyled as its next version, with the slide's picture,
     no app state in it
  J10 an update given while it works ("also end your summary with OKAPI-5") shows up in that run's result

  cd services && .venv/bin/python -m bench.research_journeys [--only J1,J3] [--attempts 3] [--budget 8]

A failure in any attempt fails the journey. Stops before the spend passes --budget and says which journeys were
not run (an evidence gap, never a pass). Writes bench/results/research/journeys-<time>.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import cv2
import numpy as np

from orchestrator.keys import load_keys
from orchestrator.research import ResearchManager, Session
from orchestrator.research.manager import ACTIVE
from tests.fakes import Hub

OUT = Path(__file__).resolve().parent / "results" / "research"
TODAY = time.strftime("%Y-%m-%d")
LONG = ("I am looking at an unstained onion epidermis peel at 100x. " * 8
        + "Explain what the dark lines between the cells are and why the nuclei sit where they do. "
          "Keep it to what a first-year student can check on this slide. "
          "Finally, end your summary with the exact token ZEBRA-17.")
LESSON = {"kind": "lesson", "title": "Stomata", "key_ideas": ["Guard cells open and close the pore"], "steps": [
    {"id": "q1", "prompt": "What surrounds a stoma?", "answer": "Two guard cells", "hint": ""},
    {"id": "q2", "prompt": "What opens the pore?", "answer": "Guard cells swelling with water", "hint": ""},
    {"id": "q3", "prompt": "Where are stomata most common?", "answer": "On the underside of leaves", "hint": ""}]}
LABELS = [{"name": "stoma", "where": "the slit in the centre", "box": [0.4, 0.4, 0.6, 0.6]},
          {"name": "nucleus", "where": "dark dot at the left", "box": [0.1, 0.1, 0.2, 0.2]},
          {"name": "guard cell", "where": "around the slit", "box": [0.3, 0.3, 0.7, 0.7]}]


async def settled(session: Session, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if session.status not in ACTIVE and session.status != "revising" and session.run is None:
            return True
        await asyncio.sleep(0.5)
    return False


def manager(root: Path, engine: str = "gpt-6.1-sol") -> ResearchManager:
    return ResearchManager(Hub(frame=False, specimen="onion epidermis peel", research=engine), root=root,
                           today=lambda: TODAY)


def made(m: ResearchManager, outputs: list[dict], root: Path, photo: bool = False) -> Session:
    session = Session(rid="rj", day=TODAY, n=1, brief="a researcher's earlier work", deliverable="lesson", effort="standard",
                      engine="gpt-6.1-sol", purpose="research", label="", object_id=None, origin={"channel": "chat"},
                      status="done", result={"title": "t", "summary": "s", "one_liner": "o", "significance": "low"},
                      outputs=outputs)
    folder = root / TODAY / "rj"
    (folder / "outputs").mkdir(parents=True, exist_ok=True)
    (folder / "outputs" / "o1.html").write_text("<html><body>lesson</body></html>")
    if photo:
        (folder / "inputs").mkdir(parents=True, exist_ok=True)
        image = np.full((240, 320, 3), 90, np.uint8)
        cv2.ellipse(image, (160, 120), (40, 20), 0, 0, 360, (40, 160, 60), -1)
        (folder / "inputs" / "crop.jpg").write_bytes(cv2.imencode(".jpg", image)[1].tobytes())
        (folder / "outputs" / "o1.png").write_bytes(cv2.imencode(".png", image)[1].tobytes())
    m.sessions[session.rid] = session
    return session


async def j1(root: Path) -> dict:
    m = manager(root)
    s = await m.start(LONG, effort="quick", words=LONG, asked_with="Explain what the dark lines between the cells are")
    ok = await settled(s, 150)
    summary = str((s.result or {}).get("summary") or "")
    m.stop()
    return {"ok": ok and s.brief == LONG.strip() and "ZEBRA-17" in summary, "status": s.status, "spent": s.spent,
            "evidence": summary[-160:]}


async def j2(root: Path) -> dict:
    m = manager(root)
    s = await m.start("What do guard cells do in a leaf epidermis at 100x?", effort="quick")
    await asyncio.sleep(2)
    await m.followup(s.rid, "What is the single most useful thing to look for on the slide?", mode="answer")
    kept = bool(s.pending) or bool(s.history)
    end = time.monotonic() + 240
    while time.monotonic() < end and not any(h.get("mode") == "answer" for h in s.history):
        await asyncio.sleep(1)
    answer = next((h for h in s.history if h.get("mode") == "answer"), {})
    m.stop()
    return {"ok": kept and answer.get("status") == "done" and bool((answer.get("result") or {}).get("summary")),
            "status": answer.get("status"), "spent": s.spent, "evidence": str((answer.get("result") or {}).get("summary"))[:160]}


async def keep_going(root: Path, engine: str, effort: str | None = None) -> dict:
    m = manager(root, engine)
    s = await m.start("What do guard cells do in a leaf epidermis at 100x?", effort="quick")
    first = await settled(s, 150)
    words = "keep going: go deeper on how to tell an open stoma from a closed one"
    await m.followup(s.rid, words, mode="continue", words=words, asked_with=words, effort=effort)
    second = await settled(s, 400 if effort == "standard" else 150)
    summary = str((s.result or {}).get("summary") or "")
    m.stop()
    return {"ok": first and second and s.status in ("done", "partial") and bool(summary) and len(s.history) == 1
                  and (effort is None or s.effort == effort),
            "status": s.status, "effort": s.effort, "spent": s.spent, "evidence": summary[:160]}


async def j6(root: Path) -> dict:
    m = manager(root)
    s = made(m, [{"id": "o1", "kind": "html", "path": "outputs/o1.html", "version": 1, "caption": "Lesson",
                  "content": LESSON}], root)
    change = "remove the second question"
    await m.followup(s.rid, change, mode="revise", target="o1", change=change, words=change, asked_with=change)
    ok = await settled(s, 200)
    new = next((o for o in s.outputs if o.get("replaces") == "o1"), {})
    prompts = [st.get("prompt") for st in (new.get("content") or s.lesson() or {}).get("steps") or []]
    m.stop()
    return {"ok": ok and bool(new) and "What opens the pore?" not in prompts and len(prompts) == 2,
            "status": (s.history[-1] if s.history else {}).get("status"), "spent": s.spent, "evidence": prompts}


async def j7(root: Path) -> dict:
    m = manager(root)
    s = made(m, [{"id": "o1", "kind": "image", "path": "outputs/o1.png", "version": 1, "caption": "Labels",
                  "content": {"kind": "labels", "items": LABELS}}], root, photo=True)
    change = "rename stoma to stomatal pore and remove the nucleus"
    await m.followup(s.rid, change, mode="revise", target="o1", change=change, words=change, asked_with=change)
    ok = await settled(s, 200)
    new = next((o for o in s.outputs if o.get("replaces") == "o1"), {})
    names = [l.get("name") for l in (new.get("content") or {}).get("items") or []]
    m.stop()
    return {"ok": ok and sorted(n.lower() for n in names) == ["guard cell", "stomatal pore"], "names": names,
            "drawn_by_app": "drawn by the app" in str(new.get("caption")), "spent": s.spent, "evidence": names}


SPEEDS = (2.7, 3.8, 2.4, 3.3, 3.4)  # mean 3.12 px/s: what the stand-in camera's five motion tests measure


class StandInCamera:
    """The app's hands with a stand-in camera: each motion test is a tracks entry with a known mean net speed, filed
    as hands.py files it (Session.ran) and readable through a stand-in session index (`index`). Nothing else runs."""

    def __init__(self) -> None:
        self.busy, self.entries, self.n = None, {}, 0

    async def run(self, session, operation: str, why: str = "", **_) -> dict:
        if operation != "motion":
            return {"ok": False, "error": "this stand-in camera measures motion only", "next": "run motion"}
        await asyncio.sleep(5)  # a motion test's own time (about 5-7 s live)
        speed = SPEEDS[self.n % len(SPEEDS)]
        self.n += 1
        entry = {"id": f"m{self.n + 1}", "kind": "tracks", "title": f"Motion, for {session.name}",
                 "at": time.time(), "summary": f"12 tracks; mean net speed {speed} px/s"}
        self.entries[entry["id"]] = entry
        words = f"measured what moves in the live view over 4.0 s: {entry['title']} ({entry['id']}): {entry['summary']}"
        session.ran.append({"id": entry["id"], "operation": "motion", "why": why, "run": session.run_number(),
                            "at": round(time.time(), 2), "title": entry["title"], "kind": "tracks", "words": words})
        return {"ok": True, "id": entry["id"], "title": entry["title"], "words": words,
                "values": {"summary": {"tracks": 12, "meanNetSpeedPxS": speed}}}

    def index(self) -> str:
        """A stand-in for the app's GET /api/resources on this machine, serving what the motion tests made."""
        entries = self.entries

        class Index(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):  # noqa: N802 - http.server's name
                path = urlsplit(self.path).path
                if path == "/api/resources/summary":
                    body = {"day": TODAY, "kinds": [{"kind": "tracks", "name": "Motion", "count": len(entries)}]}
                elif path == "/api/resources":
                    body = {"day": TODAY, "entries": list(entries.values())}
                elif path.startswith("/api/resources/") and path.rsplit("/", 1)[1] in entries:
                    entry = entries[path.rsplit("/", 1)[1]]
                    body = {"entry": entry, "detail": {"summary": entry["summary"]}}
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Index)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.server = server
        return f"http://127.0.0.1:{server.server_address[1]}"


async def j8(root: Path) -> dict:
    m = manager(root)
    camera = StandInCamera()
    m.hands, m.resources_url = camera, camera.index()
    words = "spawn a researcher to run 5 motion tests then compute the average"
    s = await m.start("Run 5 motion tests on the live view, then compute the average of their mean net speeds.",
                      effort="quick", words=words, asked_with=words)
    ok = await settled(s, 900)
    camera.server.shutdown()
    said = json.dumps(s.result or {}, ensure_ascii=False)
    numbers = [str(v) for v in SPEEDS]
    m.stop()
    return {"ok": ok and s.status == "done" and camera.n == 5 and all(n in said for n in numbers) and "3.12" in said,
            "status": s.status, "tests": camera.n, "spent": s.spent, "evidence": str((s.result or {}).get("summary"))[:200]}


async def j9(root: Path) -> dict:
    from orchestrator.brain.registry import ToolContext
    from research_worker.presentation_lesson import PHOTO
    from tests.fakes import research_tools

    m = manager(root)
    s = Session(rid="rj", day=TODAY, n=1, brief="a short lesson on stomata", deliverable="lesson", effort="standard",
                engine="gpt-6.1-sol", purpose="research", label="stomata", object_id=None, origin={"channel": "chat"},
                status="running", root=root)
    m.sessions[s.rid] = s
    folder = root / TODAY / "rj"
    for sub in ("inputs", "outputs"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    image = np.full((240, 320, 3), 90, np.uint8)
    cv2.ellipse(image, (160, 120), (40, 20), 0, 0, 360, (40, 160, 60), -1)
    (folder / "inputs" / "crop.jpg").write_bytes(cv2.imencode(".jpg", image)[1].tobytes())
    s.has_crop = True
    m._event({"event": "output", "rid": "rj", "kind": "html", "path": "outputs/01-lesson.html", "caption": "Lesson",
              "content": LESSON})
    (folder / "outputs" / "01-lesson.html").write_text("<!doctype html><html><body><h1>Stomata</h1>" + "".join(
        f'<section id="{st["id"]}">{st["prompt"]} {st["answer"]}</section>' for st in LESSON["steps"]) + "</body></html>")
    m._finish(s, "done", {"title": "Stomata", "summary": "Two guard cells open and close each pore",
                          "limitations": ["No scale is known"],
                          "report": {"methods": "Looked at the slide", "observations": "Two guard cells per pore",
                                     "measurements": "None", "limitations": "No scale is known"}})
    before = m.presentations.current(s, "lesson")
    m.hub.settings.presentation = "auto"  # the app's default (set after the findings: no report job of their own)
    ctx = ToolContext("chat", 1, None, "make the presentation prettier", "j9")
    run = research_tools(m.hub, m).start("revise_work", {"researcher": s.rid, "target": "presentation",
                                                       "change": "make the presentation prettier"}, ctx)
    await run.task
    job = next((j for j in s.presentation_jobs if j.get("contentKind") == "lesson"), {})
    end = time.monotonic() + 200
    while time.monotonic() < end and job.get("state") in ("queued", "preparing"):
        await asyncio.sleep(1)
    new = m.presentations.current(s, "lesson")
    html = (folder / new["path"]).read_text() if new and new is not before else ""
    spent = float(job.get("cost") or job.get("estimatedCost") or 0)
    if html:  # kept beside the results, to look at
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"j9-restyled-{time.strftime('%Y%m%d-%H%M%S')}.html").write_text(html)
    m.presentations.stop()
    m.stop()
    return {"ok": bool(run.ok) and job.get("state") == "ready" and new.get("replaces") == before["id"]
                  and "data:image/jpeg;base64," in html and PHOTO not in html and "Captured" in html
                  and not re.search(r"queued|pending", html, re.I),
            "state": job.get("state"), "error": job.get("error"), "receipt": run.summary, "spent": spent,
            "evidence": f"v{new.get('version')} replaces {new.get('replaces')}; {len(html)} chars"}


async def j10(root: Path) -> dict:
    m = manager(root)
    s = await m.start("What do guard cells do in a leaf epidermis at 100x? Look up two sources first.", effort="quick")
    await asyncio.sleep(4)
    noted = s.status in ACTIVE
    if noted:
        m.note(s.rid, "Also: end your summary with the exact token OKAPI-5.")
    ok = await settled(s, 300)
    summary = str((s.result or {}).get("summary") or "")
    m.stop()
    return {"ok": noted and ok and s.status == "done" and "OKAPI-5" in summary, "noted": noted, "status": s.status,
            "spent": s.spent, "evidence": summary[-160:]}


JOURNEYS = {"J1": j1, "J2": j2, "J3": lambda root: keep_going(root, "gpt-6.1-sol"),
            "J4": lambda root: keep_going(root, "gemini-3.8-flash"),
            "J5": lambda root: keep_going(root, "gpt-6.1-sol", "standard"), "J6": j6, "J7": j7, "J8": j8, "J9": j9,
            "J10": j10}


async def main_async(args) -> dict:
    wanted = args.only.split(",") if args.only else list(JOURNEYS)
    out, spent, gaps = {}, 0.0, []
    for name in wanted:
        attempts = []
        for n in range(args.attempts):
            if spent >= args.budget:
                gaps.append(f"{name} attempt {n + 1}")
                continue
            with tempfile.TemporaryDirectory() as tmp:
                try:
                    row = await JOURNEYS[name](Path(tmp))
                except Exception as exc:  # noqa: BLE001 - a crashed attempt is a failed attempt, kept
                    row = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300], "spent": 0.0}
            spent += float(row.get("spent") or 0)
            attempts.append(row)
            print(f"{name} attempt {n + 1}: {'pass' if row['ok'] else 'FAIL'} ${row.get('spent', 0):.3f} "
                  f"total ${spent:.2f}  {json.dumps(row.get('evidence'))[:120]}", flush=True)
        out[name] = {"passed": len(attempts) == args.attempts and all(a["ok"] for a in attempts), "attempts": attempts}
    return {"journeys": out, "spent": round(spent, 4), "gaps": gaps}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="")
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--budget", type=float, default=8.0)
    args = parser.parse_args()
    load_keys()
    result = asyncio.run(main_async(args))
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"journeys-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(result, indent=2, default=str))
    for name, row in result["journeys"].items():
        print(f"{name}: {'PASSED' if row['passed'] else 'FAILED'} ({sum(a['ok'] for a in row['attempts'])}/{len(row['attempts'])})")
    print(f"spent ${result['spent']:.2f}" + (f"; not run (evidence gaps): {', '.join(result['gaps'])}" if result["gaps"] else "")
          + f"; written {path}")


if __name__ == "__main__":
    main()
