"""The subject: the specimen in view (CONTEXT.md), kept with the conversation it was seen in.

The session's store holds it (`Thread.subjects`, journaled, so a restart comes back to it); this
decides when it changes. The running look (sight.py) is never told the old subject as if it
were still in view: it is asked whether the view still shows it (`same_as_before`), and when
two looks in a row say no, the subject is updated and the brain and the voice are told the
view changed. While in doubt, nothing may describe the view from the old subject. A specimen
the user names wins over the look's and is kept until the view changes. Within a session the
subject changes freely; a different specimen is recorded in the conversation, never a new
conversation (nor a new session: only the user starts one, ADR 0017).

It also names the session when nobody has (`store.rename_session(..., auto=True)`): from the first
specimen the user names, else from the subject in view when a request is made in an unnamed
session. Never over a name the user gave.

The scene's context["specimen"] is what the rest of the app reads (the vision prompts, the
descriptions, researchers' snapshots): this is its only writer, "" while in doubt.
"""
from __future__ import annotations

import time
from typing import Callable

from .catalogue import opened_sources
from .store import SessionStore

CONFIRM_LOOKS = 2  # looks in a row that must see something else before the subject changes
SUGGEST_AFTER_S = 3 * 3600  # a new specimen this long after the session was last asked in: a new session is suggested


class SubjectTracker:
    def __init__(self, day: SessionStore, conversation, scene, publish: Callable[[dict], None]) -> None:
        self.held_now: Callable[[], int] = lambda: 0  # footage held for this session's researchers now (App sets it)
        # Whether the studio covers the live view to look back at the session (hub.view_held; App sets it): looks are
        # then not taken, so a session only looked back at never gets a new subject from them.
        self.held: Callable[[], bool] = lambda: False
        # The specimen the user gave the asset on stage (library.py; App sets it): it wins over the looks, which then
        # never change the subject while that asset is on stage. "" for live, or an asset the user has not named.
        self.pinned: Callable[[], str] = lambda: ""
        # Told the specimen the user names, so the asset on stage keeps it (App: the library's metadata).
        self.on_named: list[Callable[[str], None]] = []
        self._fresh = False  # another source was put on stage: the next look names what is in view at once
        # When the session or the source on stage last changed: a look made on a frame taken before then saw what was on
        # stage before (live 2026-10-12 18:20:53: a look at the last session's footage, two seconds after another
        # session was opened, became that session's specimen), so it never names this one.
        self._changed_at = 0.0
        self.day = day
        self.conversation = conversation
        self.scene = scene
        self.publish = publish
        self.on_change: list[Callable[[str, str], None]] = []  # (old, new) when the view changed
        self._different = 0
        self._show(self.subject)
        day.listeners.append(self._on_event)

    @property
    def subject(self) -> str:
        return self.day.active.subject

    @property
    def named_by_user(self) -> bool:
        subjects = self.day.active.subjects
        return bool(subjects) and subjects[-1].get("named_by") == "user"

    def user_named(self) -> str:
        """What the user said the specimen is (a strong hint for the running look), or ""."""
        return self.subject if self.named_by_user else ""

    # --- what the running look saw ----------------------------------------------------------
    def observe(self, view) -> bool:
        """What the running look saw: name what is in view, or notice that it changed. Returns
        True when the look should be repeated soon to confirm a change. Ignored while the studio covers the view
        (`held`): a look that was in flight when it did is not what the user is looking at."""
        if self.held():
            return False
        if self.pinned():
            return False  # the user said what this asset shows: the looks never change it
        frame_at = getattr(view, "frame_at", 0.0) or 0.0
        if frame_at and frame_at < self._changed_at:
            return False  # seen on a frame from before the session or the source changed
        specimen = (view.specimen or "").strip()
        if self._fresh:
            if specimen:
                self._fresh = False
                self._set(specimen, "look", announce=bool(self.subject))
            return False
        if not self.subject:
            if specimen:
                self._set(specimen, "look", announce=False)
            return False
        if view.same is False or (view.same is None and self._different):
            self._different += 1
            if self._different >= CONFIRM_LOOKS:
                self._set(specimen, "look", announce=True)
            else:
                # Not sure yet what is in view, but it is no longer the old subject: nothing may
                # describe the view from it meanwhile (descriptions, answers, researchers).
                self._show("")
                return True  # the caller looks again soon to confirm
        else:
            if self._different:
                self._show(self.subject)  # it was the same after all
            self._different = 0
        return False

    def source_changed(self, specimen: str = "") -> None:
        """Another source is on stage (an asset opened, or live: day/store.py `open_source`). The view changed for
        certain, so nothing may describe it from the old subject: with the user's specimen for that asset (`specimen`),
        it is the subject at once; otherwise the next look names it, with no second look to confirm."""
        self._different = 0
        self._changed_at = time.time()
        if specimen:
            self._fresh = False
            self._set(specimen, "user", announce=True)
            return
        self._fresh = True
        self._show("")
        self.publish(self.wire())

    def guess(self, specimen: str) -> None:
        """An answer's own guess at the specimen: it fills the subject only when there is none."""
        if specimen and not self.subject:
            self._set(str(specimen)[:80], "look", announce=False)

    def name(self, name: str) -> str:
        """The user says what the specimen is ("it's a lucerne stem")."""
        name = " ".join(str(name).split())[:80]
        if not name:
            raise ValueError("no name given")
        old = self.subject
        self._different = 0
        first = not any(s.get("named_by") == "user" for s in self.day.subjects.values())
        self.day.set_subject(name, "user")
        if first:  # the first specimen the user names is what the session is about (never over the user's own name)
            self.day.rename_session(name, by="app", auto=True)
        self._show(name)
        self._fresh = False
        for hook in list(self.on_named):
            try:
                hook(name)
            except Exception:
                pass  # the subject is the user's whatever happens to the asset's record
        self.conversation.add("note", "system", f"The user says the specimen is {name}"
                                                f"{f' (the app had called it {old})' if old and old != name else ''}.")
        self.publish(self.wire())
        return name

    def _on_event(self, event: dict) -> None:
        """A request made in a session nobody has named: the session is named after what is in view (the user's own
        naming of a specimen takes its place once, `name`)."""
        if (event.get("type") == "turn" and event.get("role") == "user" and not self.day.name
                and self.scene.context.get("specimen")):
            self.day.rename_session(self.scene.context["specimen"], by="app", auto=True)

    def reset(self) -> None:
        """Another session opened (a new one, or one switched to): what its active conversation shows, or nothing yet."""
        self._different = 0
        self._fresh = False
        self._changed_at = time.time()
        self._show(self.subject)
        self.publish(self.wire())

    def _set(self, specimen: str, named_by: str, announce: bool) -> None:
        old = self.subject
        self._different = 0
        if announce:
            self._maybe_suggest(specimen)  # before it is one of the session's subjects
        self.day.set_subject(specimen, named_by)
        self._show(specimen)
        if announce:
            # A fact, not an instruction: it reaches the voice as context, and "talk about it" in
            # a note set GPT-Live speaking unprompted (live 2026-10-10 20:30: "Hey! What's on your slide?").
            self.conversation.add("note", "system", f"The view changed: it now shows {specimen or 'something else'}"
                                                    f"{f' (before: {old})' if old else ''}.")
            for hook in list(self.on_change):
                try:
                    hook(old, specimen)
                except Exception:
                    pass
        self.publish(self.wire())

    def _maybe_suggest(self, specimen: str) -> None:
        """A new session is suggested, never started (ADR 0017), when the running look has confirmed a different
        specimen (two looks: the model's judgement), the session has not seen it, the user kept working here for it
        never before, and the session was last asked in SUGGEST_AFTER_S ago or more. The studio shows it, and the
        brain and the voice are told it once as a fact."""
        day = self.day
        name = " ".join(str(specimen or "").split())[:80]
        if not name or day.suggestion is not None or not day.last_user:
            return
        if name.lower() in day.kept_specimens or name.lower() in {s.lower() for s in day.subjects}:
            return
        idle = day.clock() - day.last_user
        if idle < SUGGEST_AFTER_S:
            return
        hours = idle / 3600
        day.suggest(name, hours)
        self.conversation.add("note", "system",
                              f"This looks like a new specimen ({name}), {hours:.0f} hours after this session was last "
                              f'used ("{day.label}"). A new session for it is the user\'s to start: it may be offered '
                              "once, never started.")

    def _show(self, specimen: str) -> None:
        if specimen:
            self.scene.context["specimen"] = specimen
        else:
            self.scene.context.pop("specimen", None)
        self.scene.version += 1

    def counts(self, info) -> dict:
        """What the open session holds, as the session list counts it, with the footage held for its researchers now
        (memory only, listed by Made in this session while it is held, never counted by the list: `held_now`)."""
        if info is None:
            counts = {"sources": len(opened_sources(self.day)), "made": 0, "researchers": 0,
                      "conversations": len(self.day.threads)}
        else:
            counts = dict(info.counts)
        counts["made"] += self.held_now()
        return counts

    def wire(self) -> dict:
        """The studio's top bar: the session and what is in view (protocol.ts SessionState). What it holds is the
        session list's own count (day/catalogue.py `Info.counts`), so the chip, its menu and the sessions rail say the
        same numbers as Made in this session and the Researchers tab."""
        day = self.day
        info = day.catalogue.get(day.sid)
        started = info.created if info is not None and info.created else day.created or time.time()
        return {"type": "session", "id": day.sid, "label": day.label, "started": started,
                "specimen": self.scene.context.get("specimen", ""),
                "name": day.name, "named": day.named,
                "lastActive": info.last_active if info is not None and info.last_active else started,
                "days": list(info.days) if info is not None else list(day.days),
                "counts": self.counts(info),
                "suggest": day.suggestion}  # the app's suggestion of a new session (_maybe_suggest); never automatic
