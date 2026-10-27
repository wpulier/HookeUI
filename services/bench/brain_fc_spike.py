"""Spike S1: do boxes returned as FUNCTION CALL arguments locate things as well as boxes in
our current JSON answer format?

Arms (all on the 15 hand-labelled questions in bench/questions.json, same FOV crop):
  json-flash  current path: ASK prompt -> GeminiVision gemini-3.8-flash, thinking LOW, JSON reply
  fc-flash    gemini-3.8-flash, thinking LOW, tools outline/zoom/start_researcher, text answer
              + outline(targets) call in the same response, automatic function calling off
  fc-sol      OpenAI Responses API gpt-6.1-sol, reasoning low (it rejects "none"), same tools
  fc-*-tf     the fc arms with TEXT_FIRST appended (write the answer, then call outline)
  json-sol    (optional) ASK prompt -> OpenAIVision gpt-6.1-sol, to separate model from format

A question counts as right when the FIRST target's box centre is within tolerance of a
labelled target (bench.grounding.score); "any" also reports whether any target hit.
Arms are interleaved per question so drift in provider latency hits every arm equally.

Usage (from services/, keys from ~/Desktop/micromind.txt):
  .venv/bin/python -m bench.brain_fc_spike --arms json-flash,fc-flash,fc-flash-tf,fc-sol,fc-sol-tf --repeats 2
  .venv/bin/python -m bench.brain_fc_spike --arms json-sol --repeats 2 --out bench/results/brain-fc-spike-jsonsol.json
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import time
from pathlib import Path

import cv2
import numpy as np

from bench.grounding import score
from orchestrator.coords import gemini_box_to_frame
from orchestrator.fov import detect_fov
from orchestrator.keys import load_keys
from orchestrator.marks import encode_crop, fov_crop
from orchestrator.prompts import ASK
from orchestrator.vision import GeminiVision, OpenAIVision

BENCH = Path(__file__).resolve().parent
from orchestrator.assets import store  # noqa: E402  (the assets store: assets/README.md)
FRAMES = store().bench("frames")  # regenerated from the real recordings: python -m bench.extract_frames
FLASH = "gemini-3.8-flash"
SOL = "gpt-6.1-sol"
MAX_OUTPUT_TOKENS = 1500  # same budget as the JSON path

# Same teaching instructions as prompts.ASK; only the reply mechanism differs.
ASK_FC = """You are MicroscopeMind, looking through a microscope together with the user and
teaching them about what they see. The image is the current view through the eyepiece.
{context}
Things already pointed out on screen: {scene}
Conversation so far:
{history}
User: "{question}"

Answer the user in text, and point at what you talk about by calling the `outline` tool in
the same reply.
- Your text answer: 1-3 short, friendly sentences. Teach one interesting thing when it fits.
  Microscope images are ambiguous: say "likely" or "probably" unless it is unmistakable,
  and never invent details you cannot see.
- `outline` "targets": the things in the image your answer is about, each with a tight
  box_2d [ymin, xmin, ymax, xmax] on a 0-1000 scale of the image, a 1-4 word label and a
  calibrated confidence. If the user asks to highlight or count all of something, include
  every visible instance (up to 15). Do not call it when nothing specific is meant.
- Use `zoom` only when the user asks for a closer look, and `start_researcher` only for
  questions that need research beyond what you can see."""

# Smoke runs showed both models reply with ONLY the outline call (no text) to ASK_FC, the
# same failure bench.live_probe found for voice; this variant asks for the text first.
TEXT_FIRST = """

Order matters: FIRST write your text answer, THEN call `outline` in the same reply. Never
reply with only a tool call."""

FIELDS = dict(context="", scene="nothing yet", history="(none)")

OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "targets": {
            "type": "array",
            "description": "Things in the image the answer is about.",
            "items": {
                "type": "object",
                "properties": {
                    "box_2d": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 4,
                        "maxItems": 4,
                        "description": "[ymin, xmin, ymax, xmax] on a 0-1000 scale of the image",
                    },
                    "label": {"type": "string", "description": "1-4 word name"},
                    "confidence": {"type": "number", "description": "calibrated 0-1"},
                },
                "required": ["box_2d", "label", "confidence"],
            },
        }
    },
    "required": ["targets"],
}
TOOLS = [
    ("outline", "Outline things in the current microscope view so the user sees what you mean.", OUTLINE_SCHEMA),
    ("zoom", "Zoom the view in on one thing for a closer look.",
     {"type": "object", "properties": {"object": {"type": "string", "description": "what to zoom in on"}}, "required": ["object"]}),
    ("start_researcher", "Start a background researcher for questions that need more than looking.",
     {"type": "object", "properties": {
         "brief": {"type": "string", "description": "what to find out"},
         "effort": {"type": "string", "enum": ["low", "medium", "high"]}},
      "required": ["brief", "effort"]}),
]


def prepare(item: dict) -> dict:
    image = cv2.imread(str(FRAMES / item["frame"]))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    crop = fov_crop(detect_fov(gray), image.shape[1], image.shape[0])
    jpeg, _ = encode_crop(image, crop)
    return {"jpeg": jpeg, "crop": crop}


# --- arms: each returns (answer_text, targets, tool_names, usage, latency_ms, error, raw) ---

async def arm_json(vision, jpeg: bytes, question: str):
    result = await vision.ask([jpeg], ASK.format(question=question, **FIELDS), max_output_tokens=MAX_OUTPUT_TOKENS)
    data = result.data or {}
    error = result.error or (None if result.data else f"unparsed: {result.raw_text[:200]}")
    return (str(data.get("answer") or "").strip(), data.get("targets") or [], [], result.usage,
            result.latency_ms, error, result.raw_text[:600])


class FlashFC:
    def __init__(self, model: str = FLASH, thinking_level: str = "LOW", prompt: str = ASK_FC) -> None:
        from google import genai
        from google.genai import types

        self.model, self.types, self.prompt = model, types, prompt
        self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.config = types.GenerateContentConfig(
            max_output_tokens=MAX_OUTPUT_TOKENS,
            temperature=0.2,
            thinking_config=types.ThinkingConfig(thinking_level=thinking_level),
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=n, description=d, parameters_json_schema=s) for n, d, s in TOOLS
            ])],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    async def __call__(self, jpeg: bytes, question: str):
        types = self.types
        parts = [types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"), self.prompt.format(question=question, **FIELDS)]
        t0 = time.perf_counter()
        try:
            response = await self.client.aio.models.generate_content(model=self.model, contents=parts, config=self.config)
        except Exception as exc:
            return "", [], [], {}, (time.perf_counter() - t0) * 1000, f"{type(exc).__name__}: {exc}"[:400], ""
        latency = (time.perf_counter() - t0) * 1000
        text_parts = []
        cand = (response.candidates or [None])[0]
        for part in (cand.content.parts if cand and cand.content and cand.content.parts else []):
            if part.text and not part.thought:
                text_parts.append(part.text)
        calls = response.function_calls or []
        targets, names = [], []
        for call in calls:
            names.append(call.name)
            if call.name == "outline":
                targets += list((call.args or {}).get("targets") or [])
        usage = {}
        if response.usage_metadata:
            m = response.usage_metadata
            usage = {"input": m.prompt_token_count, "output": m.candidates_token_count,
                     "thinking": getattr(m, "thoughts_token_count", None)}
        raw = json.dumps({"text": "".join(text_parts), "calls": [{"name": c.name, "args": c.args} for c in calls],
                          "finish": str(cand.finish_reason) if cand else None}, default=str)[:600]
        return "".join(text_parts).strip(), targets, names, usage, latency, None, raw


class SolFC:
    def __init__(self, model: str = SOL, reasoning_effort: str | None = "low", prompt: str = ASK_FC) -> None:
        from openai import AsyncOpenAI

        self.model, self.reasoning_effort, self.prompt = model, reasoning_effort, prompt
        self.client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.tools = [{"type": "function", "name": n, "description": d, "parameters": s, "strict": False} for n, d, s in TOOLS]

    async def __call__(self, jpeg: bytes, question: str):
        content = [
            {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()},
            {"type": "input_text", "text": self.prompt.format(question=question, **FIELDS)},
        ]
        kwargs: dict = {"model": self.model, "input": [{"role": "user", "content": content}],
                        "max_output_tokens": MAX_OUTPUT_TOKENS, "tools": self.tools}
        if self.reasoning_effort:
            kwargs["reasoning"] = {"effort": self.reasoning_effort}
        t0 = time.perf_counter()
        try:
            response = await self.client.responses.create(**kwargs)
        except Exception as exc:
            return "", [], [], {}, (time.perf_counter() - t0) * 1000, f"{type(exc).__name__}: {exc}"[:400], ""
        latency = (time.perf_counter() - t0) * 1000
        targets, names, calls = [], [], []
        for item in response.output or []:
            if getattr(item, "type", None) == "function_call":
                names.append(item.name)
                try:
                    args = json.loads(item.arguments or "{}")
                except json.JSONDecodeError:
                    args = {"_unparsed": item.arguments}
                calls.append({"name": item.name, "args": args})
                if item.name == "outline":
                    targets += list(args.get("targets") or [])
        text = (response.output_text or "").strip()
        usage = {}
        if response.usage:
            u = response.usage
            details = getattr(u, "output_tokens_details", None)
            usage = {"input": u.input_tokens, "output": u.output_tokens,
                     "reasoning": getattr(details, "reasoning_tokens", None) if details else None}
        raw = json.dumps({"text": text, "calls": calls}, default=str)[:600]
        return text, targets, names, usage, latency, None, raw


def score_targets(targets: list, crop, item: dict) -> tuple[bool, bool, int]:
    hits = []
    for t in targets:
        box = t.get("box_2d") if isinstance(t, dict) else None
        if not (isinstance(box, list) and len(box) == 4):
            hits.append(False)
            continue
        try:
            b = gemini_box_to_frame([float(v) for v in box], crop)
        except (TypeError, ValueError):
            hits.append(False)
            continue
        center = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        hits.append(score(center, b, item["targets"], item["tolerance"]))
    return (bool(hits) and hits[0]), any(hits), len(hits)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arms", default="json-flash,fc-flash,fc-sol")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0)
    # gpt-6.1-sol rejects "none" (400: supported low/medium/high/xhigh/max), so "low" is its fastest.
    parser.add_argument("--sol-effort", default="low")
    parser.add_argument("--out", default=str(BENCH / "results" / "brain-fc-spike.json"))
    args = parser.parse_args()

    available = load_keys()
    questions = json.loads((BENCH / "questions.json").read_text())["questions"]
    if args.limit:
        questions = questions[: args.limit]
    prepared = {q["frame"]: prepare(q) for q in questions}

    arms = {}
    for name in args.arms.split(","):
        if name == "json-flash" and available["GEMINI_API_KEY"]:
            vision = GeminiVision(FLASH, thinking_level="low")
            arms[name] = lambda jpeg, q, v=vision: arm_json(v, jpeg, q)
        elif name == "fc-flash" and available["GEMINI_API_KEY"]:
            arms[name] = FlashFC()
        elif name == "fc-flash-tf" and available["GEMINI_API_KEY"]:
            arms[name] = FlashFC(prompt=ASK_FC + TEXT_FIRST)
        elif name in ("fc-sol", "fc-sol-tf") and available["OPENAI_API_KEY"]:
            arms[name] = SolFC(reasoning_effort=None if args.sol_effort == "default" else args.sol_effort,
                               prompt=ASK_FC + TEXT_FIRST if name.endswith("-tf") else ASK_FC)
        elif name == "json-sol" and available["OPENAI_API_KEY"]:
            vision = OpenAIVision(SOL, reasoning_effort=None if args.sol_effort == "default" else args.sol_effort)
            arms[name] = lambda jpeg, q, v=vision: arm_json(v, jpeg, q)
        else:
            print(f"skip {name}: unknown arm or key missing")

    rows = []
    for rep in range(args.repeats):
        for item in questions:
            p = prepared[item["frame"]]
            for name, fn in arms.items():  # sequential: latency is what we are measuring
                text, targets, tool_names, usage, latency, error, raw = await fn(p["jpeg"], item["question"])
                first, anyhit, n = score_targets(targets, p["crop"], item)
                row = {
                    "arm": name, "repeat": rep, "question": item["question"], "frame": item["frame"],
                    "kind": item["kind"], "latencyMs": round(latency), "correct": first, "anyCorrect": anyhit,
                    "nTargets": n, "hasText": bool(text), "tools": tool_names, "answer": text[:300],
                    "labels": [t.get("label") for t in targets if isinstance(t, dict)][:6],
                    "usage": usage, "error": error, "raw": raw if (error or not first) else None,
                }
                rows.append(row)
                flag = "ok " if first else ("any" if anyhit else "MISS")
                print(f"{flag:4s} {name:10s} {row['latencyMs']:6d}ms n={n:<2d} text={'y' if text else 'n'} "
                      f"tools={','.join(tool_names) or '-':16s} {item['question'][:40]:40s} {error or ''}")

    summary = {}
    for name in arms:
        rs = [r for r in rows if r["arm"] == name]
        ok = [r for r in rs if not r["error"]]
        lat = [r["latencyMs"] for r in ok]
        tok = lambda k: round(float(np.mean([r["usage"].get(k) or 0 for r in ok])), 1) if ok else None
        per_rep = [sum(r["correct"] for r in rs if r["repeat"] == k) for k in range(args.repeats)]
        summary[name] = {
            "n": len(rs), "errors": len(rs) - len(ok),
            "correct": sum(r["correct"] for r in rs), "anyCorrect": sum(r["anyCorrect"] for r in rs),
            "correctPerRepeat": per_rep, "of": len(questions),
            "accuracy": round(sum(r["correct"] for r in rs) / len(rs), 3) if rs else None,
            "p50Ms": int(np.percentile(lat, 50)) if lat else None,
            "p90Ms": int(np.percentile(lat, 90)) if lat else None,
            "textRate": round(sum(r["hasText"] for r in rs) / len(rs), 3) if rs else None,
            "outlineCallRate": round(sum("outline" in r["tools"] for r in rs) / len(rs), 3) if rs else None,
            "otherToolCalls": sum(len([t for t in r["tools"] if t != "outline"]) for r in rs),
            "meanTargets": round(float(np.mean([r["nTargets"] for r in rs])), 2) if rs else None,
            "meanTokens": {"input": tok("input"), "output": tok("output"),
                           "thinking": tok("thinking"), "reasoning": tok("reasoning")},
        }
    print(json.dumps(summary, indent=2))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"when": time.strftime("%Y-%m-%d %H:%M"), "arms": list(arms),
                               "sol": SOL, "solEffort": args.sol_effort, "flash": FLASH,
                               "summary": summary, "rows": rows}, indent=2, default=str))
    print(f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
