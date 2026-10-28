"""Human names for recorded execution facts. Called only by the UI's explicit inspection.

The capability catalogue and option labels are reused; current settings and model-written
answers are never evidence that a model ran. No imports of model implementations or lookups.
"""
from .capabilities import load
from .options import GROUPS, VISION, VOICE


def powers(steps):
    catalogue = load()
    roles = {r["id"]: r for r in catalogue["roles"]}
    tools = {}
    for capability in catalogue["capabilities"]:
        tool = capability["how"].get("tool")
        if tool:
            tools.setdefault(tool, set()).add(capability["title"])
    by_id = {s["id"]: s for s in steps}

    def ancestors(step):
        seen = set()
        while step and step["id"] not in seen:
            seen.add(step["id"])
            yield step
            step = by_id.get(step.get("parent"))

    model_steps = [s for s in steps if s.get("model") or s.get("provider") == "algorithm"]
    model_parents = {p["id"] for s in model_steps for p in list(ancestors(s))[1:]}
    rows, seen = [], set()
    for step in steps:
        operation = step["operation"]
        app_tool = operation.startswith("tool:") and step["id"] not in model_parents
        if step not in model_steps and not app_tool:
            continue
        role = roles.get(step.get("role"), {})
        option = GROUPS.get(step.get("role"), {}).get(step.get("optionId"))
        model = step.get("model", "")
        # A hosted model id is known at dispatch; a provider's revision stays in the
        # technical receipt. Never use the selected outline setting as its actual model.
        generic = VISION.get(step.get("optionId")) or VISION.get(model) or VOICE.get(model)
        label = option.label if option else role.get("label") or (generic.label if generic else model)
        if not label:
            label = "HookeUI" if app_tool else operation.replace("-", " ")
        title = role.get("title", "Model")
        for parent in ancestors(step):
            # The same vision model can answer, point or name. Only the recorded
            # naming operation proves this call named the already outlined object.
            if operation == "vision" and parent["operation"] == "name":
                title = "Naming"
                break
            if parent["operation"].startswith("tool:"):
                name = parent["operation"][5:]
                titles = tools.get(name, set())
                title = next(iter(titles)) if len(titles) == 1 else name.replace("_", " ").capitalize()
                break
        key = (title, label, step["outcome"], step.get("cache"))
        if key in seen:
            continue
        seen.add(key)
        rows.append({"capability": title, "label": label, "provider": step.get("provider", ""),
                     "outcome": step["outcome"], "cache": step.get("cache"), "stepId": step["id"]})
    return rows
