"""Closed desired workspace conditions, not verified results or source authority.

The model declares intent on its existing reply. This parser only retains a
detached bounded declaration; actual occurrence/source/command binding belongs
to execution's later receipt path. Other domains keep their own evidence scope.
"""
from __future__ import annotations

import json

from ..completion import valid_postconditions


def _corresponds(raw_actions, actions) -> bool:
    """Every raw slot must survive in its original executable position."""
    from .registry import parse_actions

    return (isinstance(raw_actions, list) and len(raw_actions) == len(actions)
            and parse_actions(raw_actions) == list(actions))


def parse(raw, actions, *, raw_actions=Ellipsis) -> dict | None:
    """Admit one closed declaration against this reply's actual action list.

    Invalid declarations do not change or replay actions. An output selector is
    still desired intent, never an assertion that the output actually exists.
    Omit raw_actions only for an already normalized action list (FC or Fast).
    """
    if (type(raw) is not dict or set(raw) != {"conditions"}
            or type(raw["conditions"]) is not list or not 0 < len(raw["conditions"]) <= 16
            or not isinstance(actions, (list, tuple)) or not actions):
        return None
    if raw_actions is not Ellipsis and not _corresponds(raw_actions, actions):
        return None
    conditions, expected = [], []
    keys = {"action", "predicate", "targets", "expected", "essential", "requiredPhase", "tolerance"}
    required = {"action", "predicate", "targets", "expected", "requiredPhase"}
    for number, supplied in enumerate(raw["conditions"], 1):
        if (type(supplied) is not dict or set(supplied) - keys or not required <= set(supplied)
                or type(supplied["action"]) is not int or not 1 <= supplied["action"] <= len(actions)
                or type(supplied["predicate"]) is not str or type(supplied["expected"]) is not dict
                or type(supplied["targets"]) is not list or not 0 < len(supplied["targets"]) <= 8
                or type(supplied.get("essential", True)) is not bool):
            return None
        targets, labels, identities = [], [], set()
        reserved = {s for s in supplied["targets"] if type(s) is str}
        above = supplied["expected"].get("above")
        if isinstance(above, (list, tuple)) and len(above) <= 40:
            reserved.update(s for s in above if type(s) is str)
        for selector in supplied["targets"]:
            if type(selector) is str and 0 < len(selector) <= 256:
                identity, label = ("presentation", selector), selector
            elif (type(selector) is dict and set(selector) == {"step", "output"}
                  and type(selector["step"]) is int and 1 <= selector["step"] <= len(actions)
                  and type(selector["output"]) is int and 0 <= selector["output"] < 16):
                identity = ("output", selector["step"], selector["output"])
                # Only the existing pure value validator sees this label. It is
                # never retained as an instance, command, source or proof ID.
                label = f"output:{selector['step']}:{selector['output']}"
                while label in reserved:
                    label += ":"
            else:
                return None
            if identity in identities:
                return None
            identities.add(identity)
            targets.append(selector)
            labels.append(label)
        condition = {"action": supplied["action"], "predicate": supplied["predicate"],
                     "targets": targets, "expected": supplied["expected"],
                     "essential": supplied.get("essential", True),
                     "requiredPhase": supplied["requiredPhase"], "tolerance": supplied.get("tolerance")}
        conditions.append(condition)
        expected.append({"id": str(number), **{k: v for k, v in condition.items() if k != "action"},
                         "targets": labels})
    if not any(c["essential"] for c in conditions) or not valid_postconditions(expected):
        return None
    try:
        encoded = json.dumps({"conditions": conditions}, ensure_ascii=False,
                             allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 65536:
            return None
        return json.loads(encoded)  # primitive detached values, never provider-owned mutable facts
    except (TypeError, ValueError, RecursionError, UnicodeError):
        return None
