"""Stand-ins shared by the tests: real modules over a temporary day, with what they say and
show recorded."""
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from orchestrator.announcer import Announcer
from orchestrator.brain.state import Conversation
from orchestrator.day import DayStore
from orchestrator.dialogue import Dialogue
from orchestrator.options import Settings
from orchestrator.scene import Scene


def announcer(tmp_path, research, *, presenter=None, scene=None, voice=None, day=None):
    """A real Announcer over a real Dialogue: `lines` are what the chat was shown, `spoken`
    what the narrator was given, `quiet` what the voice was told as context."""
    day = day or DayStore(root=tmp_path / "sessions")
    conversation = Conversation(store=day)
    lines, spoken, quiet, logged = [], [], [], []

    async def note(text):
        quiet.append(text)

    made = Announcer(Dialogue(day, lines.append), conversation, SimpleNamespace(offer=spoken.append), presenter, research,
                     scene if scene is not None else Scene(), voice=lambda: voice, note_to_voice=note, log=logged.append)
    return SimpleNamespace(announcer=made, lines=lines, spoken=spoken, quiet=quiet, logged=logged, day=day,
                           conversation=conversation)


def desk(manager, presenter=None):
    """The desk over a manager, with what the presenter shows (or nothing on screen)."""
    from orchestrator.research.desk import Desk

    return Desk(manager, shown=lambda: presenter.rid if presenter is not None and presenter.open else None)


def research_tools(hub, manager, presenter=None, agent=None, **more):
    """The registry a brain gets for these researchers (toolbox.build)."""
    from orchestrator.brain.toolbox import Services, build

    return build(Services(hub=hub, agent=agent, research=manager, desk=desk(manager, presenter), presenter=presenter, **more))


def researchers(*sessions):
    """A stand-in research manager holding these researchers (what the brain and the day read)."""
    by_rid = {s.rid: s for s in sessions}
    return SimpleNamespace(sessions=by_rid, get=by_rid.get, lines=lambda limit=None: "\n".join(s.line() for s in sessions),
                           researchers=lambda day=None, session=None: [
                               s for s in sessions if s.purpose == "research" and (day is None or getattr(s, "day", day) == day)
                               and (session is None or (getattr(s, "sitting", None) or session) == session)])


def fast(brain, question, model="gemini-3.8-flash", **kw):
    """The fast pass for this brain now (brain/fast.decide on its Moment)."""
    from orchestrator.brain.fast import decide

    return decide(brain.moment(), question, brain.agent.vision(model), brain.registry.prompt_section(), model, **kw)


def conversation(root=None):
    """A conversation over a day of its own (a fresh temporary folder unless given)."""
    return Conversation(store=DayStore(root=Path(root) if root else Path(tempfile.mkdtemp(prefix="micromind-day-"))))


class Hub:
    """The hub as the tests need it: a scene, the settings, the latest frame (none with
    frame=False), a camera source that has no pictures, and what was broadcast (`sent`)."""

    def __init__(self, *, frame: bool = True, specimen: str = "", research: str = "gpt-6.1-sol",
                 image: str = "gpt-image-2.5-flare") -> None:
        self.scene = Scene()
        if specimen:
            self.scene.context["specimen"] = specimen
        self.settings = Settings(presentation='off')  # synthetic researchers never dispatch optional providers
        self.settings.research, self.settings.image = research, image  # "fake": a scripted researcher's engine
        self.latest_info = (SimpleNamespace(id=7, dx=0.0, dy=0.0, width=1280, height=720, epoch=0, fov=None)
                            if frame else None)
        self.source = SimpleNamespace(get=lambda _id: None, is_fresh=lambda: True)
        self.focused = None
        self.clients = set()
        self.sent = []

    def broadcast(self, payload):
        self.sent.append(json.loads(json.dumps(payload)))
