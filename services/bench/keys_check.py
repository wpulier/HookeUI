"""Check every API key (from wherever it comes: keys.py) with one cheap authenticated call each (orchestrator/key_tests.py).
Prints only pass/fail and the provider's error text; key values are never printed."""
from __future__ import annotations

import os

import httpx

from orchestrator.keys import KNOWN_KEYS, load_keys


def check(name: str, fn) -> None:
    try:
        detail = fn()
        print(f"  ok    {name:18s} {detail}")
    except Exception as exc:  # report and continue with the next provider
        message = str(exc)
        for key in KNOWN_KEYS:
            if os.environ.get(key):
                message = message.replace(os.environ[key], "<redacted>")
        print(f"  FAIL  {name:18s} {type(exc).__name__}: {message[:160]}")


def gemini() -> str:
    from google import genai

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])  # keep a reference while paging
    models = [m.name for m in client.models.list()]
    return f"{len(models)} models; gemini-3.8-flash {'available' if 'models/gemini-3.8-flash' in models else 'MISSING'}"


def openai_() -> str:
    from openai import OpenAI

    ids = {m.id for m in OpenAI(api_key=os.environ["OPENAI_API_KEY"]).models.list()}
    wanted = ["gpt-6-luna", "gpt-live-1", "gpt-realtime-2.1"]
    return ", ".join(f"{w} {'ok' if w in ids else 'missing'}" for w in wanted)


def fal() -> str:
    # /users/current returns 200 for a valid key and 401 for an invalid one.
    r = httpx.get("https://rest.alpha.fal.ai/users/current", headers={"Authorization": f"Key {os.environ['FAL_KEY']}"}, timeout=15)
    r.raise_for_status()
    return "authenticated"


def huggingface() -> str:
    from huggingface_hub import HfApi

    api = HfApi(token=os.environ["HF_TOKEN"])
    user = api.whoami()["name"]
    access = []
    for repo in ("facebook/sam3.1", "facebook/sam3"):
        try:
            api.model_info(repo, files_metadata=False)
            files = api.list_repo_files(repo)
            access.append(f"{repo} granted ({len(files)} files)")
        except Exception as exc:
            access.append(f"{repo} NOT granted ({type(exc).__name__})")
    return f"user {user}; " + "; ".join(access)


def roboflow() -> str:
    r = httpx.get("https://api.roboflow.com/", params={"api_key": os.environ["ROBOFLOW_API_KEY"]}, timeout=15)
    r.raise_for_status()
    return f"workspace {r.json().get('workspace', '?')}"


def main() -> None:
    """Every service's key, tested by the app's own test (orchestrator/key_tests.py), with where it came from."""
    from gpu_worker.manifests import services
    from orchestrator import key_tests
    from orchestrator.keys import SOURCE_WORDS, resolve
    from orchestrator.paths import Paths

    resolved = resolve()
    load_keys()  # the gated-repo check below reads HF_TOKEN from the environment
    print("Keys file:", Paths.from_env().keys_file)
    for service in services().values():
        r = resolved.get(service.key)
        if r is None or not r.present:
            print(f"  skip  {service.name:18s} {service.key} is not set")
            continue
        outcome = key_tests.check(service.id, r.value)
        words = {"accepted": "ok  ", "rejected": "FAIL", "unchecked": "????"}[outcome.state]
        said = outcome.detail or outcome.why or outcome.state
        print(f"  {words}  {service.name:18s} {said} ({SOURCE_WORDS[r.source]})")
    if resolved.get("HF_TOKEN") and resolved["HF_TOKEN"].present:
        check("Hugging Face gated", huggingface)


if __name__ == "__main__":
    main()
