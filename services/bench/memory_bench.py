"""Does the day's memory find what was said earlier? Recall over the first live session.

The saved conversation of 2026-10-04 (14:01-15:38, ~300 lines over five specimens) is put
into a fresh day as one long conversation; fifteen questions a user might ask hours later
are each scored right when the expected words are in the top three recall hits (day/memory.py).
No model calls: this measures recall alone, the part that decides what the brain gets to see.

  cd services && .venv/bin/python -m bench.memory_bench [--saved logs/saved/20261004-154005/conversation.jsonl]
"""
from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from orchestrator.brain.state import Conversation
from orchestrator.day import DayStore
from orchestrator.day.memory import Memory

BENCH = Path(__file__).resolve().parent
SAVED = BENCH.parent / "logs" / "saved" / "20261004-154005" / "conversation.jsonl"
CASES = [
    ("What was the insect we looked at?", ["musca"]),
    ("What does W M mean on the slide?", ["whole mount"]),
    ("What did I say the stem was?", ["lucent"]),
    ("What colour did the platelet look to me?", ["green"]),
    ("What did you mean by a multi-lobed nucleus?", ["lobes"]),
    ("Why do the nuclei sit off centre in the plant cells?", ["vacuole"]),
    ("What was the first question of the quiz?", ["plant cells"]),
    ("How far could the digital zoom go on the socket?", ["point five", "1.5", "five point"]),
    ("What do red blood cells do?", ["oxygen"]),
    ("What were the big cells at the top of the stem?", ["parenchyma"]),
    ("What's the difference between setae and bristles?", ["setae"]),
    ("Why are the tissues different colours?", ["stain"]),
    ("What would the stem look like unstained?", ["translucent", "pale"]),
    ("Which way were the onion cells elongated?", ["elongat", "left to right", "horizontal"]),
    ("Which image model did I want the researcher to use?", ["openai"]),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--saved", default=str(SAVED))
    args = parser.parse_args()
    records = [json.loads(line) for line in Path(args.saved).read_text().splitlines() if line.strip()]
    live = [r for r in records if str(r.get("at", "")) >= "2026-10-04T14:01"]
    store = DayStore(root=Path(tempfile.mkdtemp(prefix="memory-bench-")), day="2026-10-04")
    conversation = Conversation(store=store)
    n = conversation.restore(live)
    memory = Memory(store, conversation)
    right, rows, t0 = 0, [], time.perf_counter()
    for question, expected in CASES:
        hits = memory.recall(question, k=3)
        text = " ".join((h.get("context") or h["text"]).lower() for h in hits)
        ok = any(e in text for e in expected)
        right += ok
        rows.append({"question": question, "expected": expected, "right": ok, "top": (hits[0].get("context") or hits[0]["text"])[:160] if hits else ""})
        print(f"{'ok ' if ok else 'BAD'} {question[:56]:56} -> {rows[-1]['top'][:90]}")
    ms = (time.perf_counter() - t0) * 1000 / len(CASES)
    summary = {"lines": n, "right": f"{right}/{len(CASES)}", "msPerRecall": round(ms, 1)}
    print(json.dumps(summary))
    (BENCH / "results").mkdir(exist_ok=True)
    (BENCH / "results" / "memory-bench.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))


if __name__ == "__main__":
    main()
