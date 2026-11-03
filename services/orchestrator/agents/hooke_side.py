"""What a launch needs from the rest of Hooke (ADR 0029), in one place the conductor calls and tests replace.

Everything a launched agent is given is made here, for it alone: its door connection, its Project access and its Study
assignment. The owner's steps (the Study and the assignment) are recorded as the researcher's, because the researcher
asked for the agent in Hooke; everything the agent does after is recorded as the agent's own, by its connection.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from ..paths import REPO  # the source checkout, or Contents/Resources/app in the Mac app

CLI_SOURCES = ("packaging/macos/hooke/main.swift", "packaging/macos/HookeUI/AgentShared.swift")
BUILD_S = 180


class HookeSide:
    def __init__(self, built):
        self.built = built
        self.door = os.environ.get("HOOKE_DOOR_URL") or "http://127.0.0.1:8770"

    # --- the `hooke` command and the skill ------------------------------------------------------------------------
    def cli(self) -> str | None:
        """The `hooke` command a launched agent runs: the Mac app's own, else one built from this checkout."""
        override = os.environ.get("HOOKE_CLI")
        if override and os.access(override, os.X_OK):
            return override
        bundle = os.environ.get("HOOKEUI_APP")
        if bundle:
            helper = Path(bundle) / "Contents/Helpers/hooke"
            return str(helper) if os.access(helper, os.X_OK) else None
        sources = [REPO / s for s in CLI_SOURCES]
        if not all(s.is_file() for s in sources) or shutil.which("swiftc") is None:
            return None
        digest = hashlib.sha256(b"".join(s.read_bytes() for s in sources)).hexdigest()[:12]
        built = Path(self.built.paths.home) / "agents" / "bin" / f"hooke-{digest}"
        if os.access(built, os.X_OK):
            return str(built)
        built.parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(["swiftc", "-O", "-swift-version", "5", *map(str, sources), "-o", str(built)],
                           check=True, capture_output=True, timeout=BUILD_S)
        except (OSError, subprocess.SubprocessError):
            return None
        return str(built)

    def skill(self) -> str | None:
        path = REPO / ".agents/skills/hooke/SKILL.md"
        return str(path) if path.is_file() else None

    def door_url(self) -> str:
        return self.door

    # --- the Project, the connection, the assignment --------------------------------------------------------------
    def project(self, sid: str | None) -> tuple[str, str]:
        from .conductor import Refused

        day = self.built.day
        sid = sid or day.sid
        info = day.catalogue.get(sid) if sid else None
        if info is None:
            raise Refused("Open a Project first: the agent works in one Project.", next="open or make a Project", status=409)
        return sid, info.name or "this Project"

    def connect(self, label: str, kind: str, launch: str, project: str) -> tuple[str, str]:
        from ..door.projects import access

        connections = self.built.agent_connections
        connection, token = connections.issue(f"{label} (started by Hooke)", kind, launch=launch)
        try:
            access(self.built).grant(project, connection.actor, "contribute", authority="launch", user_project=True)
        except Exception:
            connections.disconnect(connection.id)
            raise
        self._announce()
        return connection.id, token

    def assign(self, project: str, launch: str, title: str, task: str, budget: float | None) -> tuple[str, str]:
        from ..research.coordination import Coordination
        from ..research.studies import service

        studies = service(self.built)
        made = studies.create(project, actor="studio", label="You", request=f"launch:{launch}", title=title[:240],
                              objective=task, criteria="The agent returns a report the researcher can review.",
                              budget_usd=budget or 0, inputs=[])
        assignment = Coordination(studies).propose(made.id, project, request=f"launch:{launch}", assignment={
            "objective": task, "deliverable": "A report kept in this Project and a claim for the researcher to review",
            "uncertainty": "What the agent can do with this Project and Hooke's tools",
            "decisionImpact": "The researcher decides what to keep", "whyNewWork": "The researcher asked for it in Hooke",
            "evidence": [], "dependsOn": []}, actor="studio", label="You")
        return made.id, assignment["id"]

    def observe(self, project: str, study: str, assignment: str, connection: str) -> dict:
        from ..research.coordination import Coordination
        from ..research.studies import service

        studies = service(self.built)
        read = Coordination(studies).read(study, project)
        row = next((a for a in read.get("assignments", []) if a.get("id") == assignment), {})
        actor = f"agent:{connection}"
        claims = [c for c in read.get("claims", []) if c.get("by") == actor]
        state = row.get("observedState")
        return {"returned": state == "completed" and bool(row.get("report")),
                "claimed": state in ("running", "needs-reconciliation", "completed"), "claims": len(claims)}

    def end(self, project: str, connection: str) -> None:
        """Revoke the launch's connection and withdraw its Project access; done again harmlessly."""
        from ..door.projects import access

        connections = getattr(self.built, "agent_connections", None)
        if connections is None:
            return
        try:
            known = connections.get(connection)
            if known is not None and known.revoked_at is None:
                connections.disconnect(connection)
            access(self.built).grant(project, f"agent:{connection}", None, authority="launch", user_project=True)
        except Exception:
            pass  # the token is already refused in memory; a record that couldn't be written is kept next time
        self._announce()

    def _announce(self) -> None:
        announce = getattr(self.built, "announce_agent_connections", None)
        if announce is not None:
            try:
                announce()
            except Exception:
                pass

    # --- the research allowance -----------------------------------------------------------------------------------
    def _spending(self):
        research = getattr(self.built, "research", None)
        return getattr(research, "spending", None)

    def today(self) -> str:
        spending = self._spending()
        return spending.today() if spending is not None else ""

    def allowance_left(self) -> float | None:
        spending = self._spending()
        cap = self.built.hub.settings.research_cap
        if spending is None or cap is None:
            return None
        return cap - spending.committed()

    def hold(self, launch: str, usd: float) -> None:
        spending = self._spending()
        if spending is not None and usd:
            spending.reserve(f"agent:{launch}", usd)

    def release(self, launch: str) -> None:
        spending = self._spending()
        if spending is not None:
            spending.release(f"agent:{launch}")

    def spent(self, launch: str, usd: float, billing: str | None, model: str | None, budget: float | None) -> None:
        """Claude Code's own count of its spend. It counts against the day's allowance either way (spent_on), and
        its hold shrinks by as much, so nothing counts twice. It goes into Spending, against the monthly cap, only
        when it is billed to an API key (not the user's subscription)."""
        spending = self._spending()
        if spending is not None and budget:
            spending.reserve(f"agent:{launch}", max(0.0, budget - usd))
        if billing in (None, "none"):
            return
        from research_worker import api_usage

        api_usage.record(model or "claude-code", usd, provider="Anthropic", area="Agents",
                         basis="reported by Claude Code", key=f"agent:{launch}")

    # --- telling the user -----------------------------------------------------------------------------------------
    def activity(self, text: str, *, state: str = "done", id: str | None = None, ref: dict | None = None,
                 details: dict | None = None) -> None:
        from ..activity import activity

        activity(self.built.hub, "research", text, state=state, id=id, ref=ref, details=details)

    def broadcast(self, payload: dict) -> None:
        self.built.hub.broadcast(payload)

    def say(self, facts: dict, *, still=None, urgent: bool = False) -> None:
        """Tell the voice, while Talk is on, so it says it once in its own words (the narrator): never when the fact no
        longer holds by the time it would be said (`still`), never as the user's words."""
        import json

        brain = getattr(self.built, "brain", None)
        voice = getattr(brain, "voice", None)
        narrator = getattr(self.built, "narrator", None)
        if narrator is None or voice is None or getattr(voice, "closed", False):
            return
        from ..brain.narrator import Line

        narrator.offer(Line(json.dumps({"outside agent": facts}, ensure_ascii=False), "requested",
                            delivery="interrupt" if urgent else "when_idle", provenance="data", notify=True,
                            still=still, voice_owner=voice))
