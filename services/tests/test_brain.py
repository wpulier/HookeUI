import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.brain.loop import Brain
from orchestrator.brain.registry import ACTIVITY_KEYS, Registry, Tool, ToolFailed, parse_actions, schema
from orchestrator.brain.state import Conversation
from tests.fakes import conversation as day_conversation
from orchestrator.brain.toolbox import Services, build, resolve_object
from orchestrator.options import Settings
from orchestrator.scene import Scene, SceneObject
from tests.fakes import Hub as BaseHub
from tests.test_population import world  # noqa: F401  (the fixture: cells over a textured slide)


FakeHub = BaseHub  # tests/fakes.Hub


def obj(hub, label, x=100.0):
    o = SceneObject(id=hub.scene.new_id(), kind="highlight", polygons=[[[x, 100], [x + 20, 100], [x, 120]]],
                    anchor_frame=7, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="ai:test", label=label)
    return hub.scene.add(o)


class FakeVision:
    """The fast model, scripted: a prompt from notes (the fast pass) gets `fast`; any other
    (the answer rewrite, Brain._reconcile) gets `rewrite`. Without a reply it times out, as an
    unreachable model would, so a test sees that path on purpose, never by a swallowed error."""

    def __init__(self, fast=None, rewrite=None):
        self.fast, self.rewrite = fast, rewrite
        self.prompts = []

    async def ask(self, images, prompt, max_output_tokens=0):
        assert images == []
        self.prompts.append(prompt)
        reply = self.fast if "only your notes" in prompt else self.rewrite
        if reply is None:
            raise asyncio.TimeoutError("no model for this prompt in the test")
        return SimpleNamespace(data=reply, latency_ms=5.0)


class FakeAgent:
    """Scripted vision replies; records the prompts' tool sections and questions."""

    def __init__(self, hub, conversation, replies):
        self.hub, self.conversation, self.replies = hub, conversation, list(replies)
        self.calls = []
        self.logged = []
        self.fast_model = FakeVision()

    def vision(self, _model):
        return self.fast_model

    def available(self, _=None):
        return True

    def snapshot(self, frame_id=None):
        return self.hub.latest_info, None, None

    async def look(self, question, view, *, option, pointing="box", notes=None, mode="json-actions", registry=None):
        from orchestrator.brain.dispatch import dispatch_answer

        async def provider_reply():
            self.calls.append({"question": question, "tools": notes})
            return self.replies.pop(0), {"model": "fake", "latency_ms": 10.0, "marks": None}

        # This stand-in bypasses Agent._ask_model, so its scripted provider belongs
        # at the same boundary. Behavioral assertions remain unchanged.
        return await dispatch_answer(provider_reply, model="fake", effort="synthetic")

    async def point(self, targets, frame_id, crop, marks=None, model_name="", errors=None, trial=None, arm=None,
                    by_index=None):
        made = [obj(self.hub, t["label"]) for t in targets]
        if by_index is not None:
            by_index[:] = [o.id for o in made]
        return made

    def log(self, record):
        self.logged.append(record)


def make(replies, extra_tools=()):
    hub = FakeHub()
    conversation = day_conversation()
    agent = FakeAgent(hub, conversation, replies)
    registry = build(Services(hub=hub, agent=agent))
    for tool in extra_tools:
        registry.add(tool)
    return Brain(hub, agent, registry, conversation), hub, agent


def run(coro):
    return asyncio.run(coro)


def test_one_call_answers_outlines_and_acts():
    import json
    from orchestrator.ticket import current

    brain, hub, agent = make([{
        "answer": "That's likely a stoma. Selecting it.",
        "targets": [{"box_2d": [1, 2, 3, 4], "label": "stoma", "confidence": 0.8}],
        "actions": [{"tool": "select", "args": {"object": "target 1"}}],
    }])
    hub.settings.answer_check = "every-request"  # the rewrite's own mechanics (ANSWER_CHECK; default contradicted)
    final_answer = "The stoma outline was created and selected."
    final_reply = {'answer': final_answer, 'actions': [], 'observation_scope': 'none', 'input_refs': []}
    agent.fast_model.rewrite = final_reply
    actual_ask = agent.fast_model.ask
    finals = []

    async def trace_final(images, prompt, **kwargs):
        reply = await actual_ask(images, prompt, **kwargs)
        finals.append((current(), tuple(images), prompt, reply.data))
        return reply

    agent.fast_model.ask = trace_final  # observe the existing external provider boundary
    result = run(brain.turn("what's that oval?"))
    assert len(agent.calls) == 1 and result["calls"] == 2
    assert '"select"' in agent.calls[0]["tools"][0]  # the tool list is in the prompt
    assert hub.scene.selected == result["objectIds"][0]
    assert len(finals) == 1
    ticket, images, prompt, returned = finals[0]
    budget = ticket.admission
    assert budget.used == result['calls'] == 2
    assert [a.phase for a in budget.attempts] == ['look', 'final']
    assert all(a.status == 'done' for a in budget.attempts)
    action = result['actions'][0]
    assert action['requestId'] == budget.request_id and action['actionId']
    assert result["actions"] == [{"tool": "select", "ok": True, "summary": "selected the stoma",
                                  "ms": action['ms'], 'requestId': budget.request_id, 'actionId': action['actionId']}]
    assert images == () and json.dumps(action) in prompt and "what's that oval?" in prompt
    assert returned == final_reply and result['answer'] == final_answer and result['answerStatus'] == 'model'
    roles = [(t.role, t.channel) for t in brain.conversation.turns]
    assert roles == [("user", "chat"), ("note", "chat"), ("assistant", "chat")]
    user, note, assistant = brain.conversation.turns
    assert note.refs['objectIds'] == result['objectIds']
    assert [row['id'] for row in note.refs['outputRefs']] == result['objectIds']
    assert all(row['turn_id'] == user.id for row in note.refs['outputRefs'])
    assert assistant.text == final_answer and assistant.refs['outputRefs'] == []
    assert agent.logged[-1]["kind"] == "turn" and agent.logged[-1]["calls"] == 2


def test_query_tool_results_go_back_for_a_second_call():
    async def status(ctx, args):
        return "researcher 1 found a trichome"

    tool = Tool("research_status", "What researchers found.", schema({}), status, kind="query")
    brain, _, agent = make([
        {"answer": "Checking.", "targets": [], "actions": [{"tool": "research_status", "args": {}}]},
        {"answer": "Researcher 1 found a trichome.", "targets": [], "actions": []},
    ], [tool])
    agent.fast_model.rewrite = {"answer": "Researcher 1 found a trichome."}
    result = run(brain.turn("what did the researcher find?"))
    assert result["calls"] == 3 and result["answer"] == "Researcher 1 found a trichome."
    assert "researcher 1 found a trichome" in agent.calls[1]["question"]


def test_the_follow_up_is_told_only_returned_actions_execute_and_they_run():
    """Live, 2026-10-07 (flow 5): "Pull up the cells from earlier" got a query, then a final answer that only
    promised ("Let me find the cells we segmented earlier today.") with no action. The follow-up is told the
    fact that prose does not execute work, and the actions it lists run."""
    async def find(ctx, args):
        return "e1: Cell outlines · 12:49 (207 instances)"

    tool = Tool("resources", "What the session holds.", schema({}), find, kind="query")
    brain, hub, agent = make([
        {"answer": "Let me find them.", "targets": [], "actions": [{"tool": "resources", "args": {}}]},
        {"answer": "Here are the cells from earlier.", "targets": [], "actions": [{"tool": "select", "args": {"object": "o9"}}]},
    ], [tool])
    result = run(brain.turn("Pull up the cells from earlier"))
    assert "Only returned actions will run; describing an action does not execute it." in agent.calls[1]["question"]
    assert result["calls"] == 3 and [a["tool"] for a in result["actions"]] == ["resources", "select"]  # both receipts survive


def test_query_loop_is_capped():
    async def status(ctx, args):
        return "nothing yet"

    tool = Tool("research_status", "", schema({}), status, kind="query")
    again = {"answer": "Still checking.", "targets": [], "actions": [{"tool": "research_status", "args": {}}]}
    brain, _, agent = make([again, again, again, again], [tool])
    assert run(brain.turn("status?"))["calls"] == 3


def test_slow_tool_is_reported_late_on_the_channel_asked():
    async def slow(ctx, args):
        await asyncio.sleep(0.2)
        return "dissected the stoma into 3 parts"

    tool = Tool("dissect", "", schema({}), slow, inline_ms=20)
    brain, _, _ = make([{"answer": "Dissecting it.", "targets": [], "actions": [{"tool": "dissect", "args": {}}]}], [tool])
    late = []

    async def report(ctx, run):
        late.append((ctx.channel, run.summary, ctx.delegation_id))

    brain.report_late = report

    async def go():
        result = await brain.turn("break it down", "voice", delegation_id="d9")
        assert result["actions"][0]["ok"] is None  # not done when the answer went out
        await asyncio.sleep(0.4)

    run(go())
    assert late == [("voice", "dissected the stoma into 3 parts", "d9")]


def test_tool_errors_and_unknown_tools_do_not_break_the_turn():
    async def broken(ctx, args):
        raise ToolFailed("the magnifier is closed")

    tool = Tool("hold", "", schema({}), broken)
    brain, _, _ = make([{"answer": "OK.", "targets": [], "actions": [
        {"tool": "hold", "args": {}}, {"tool": "fly", "args": {}}]}], [tool])
    result = run(brain.turn("hold it"))
    assert [(a["tool"], a["ok"], a["summary"]) for a in result["actions"]] == [
        ("hold", False, "the magnifier is closed"), ("fly", False, "there is no fly tool")]


def test_an_unknown_tool_named_as_a_verb_of_present_is_refused_naming_present():
    """Live 2026-10-11 16:36:22: the fast pass called "resize", refused "there is no resize tool", and the answer told
    the user the window could not be resized, though present do="resize" exists (it worked at 16:25:42). A name that is
    exactly one of present's verbs is refused naming present and the verb, so the recovery round can use it; any other
    unknown name is refused as before (no near matches)."""
    from orchestrator.screen import VERBS

    brain, _, _ = make([{"answer": "Resizing it.", "targets": [], "actions": [
        {"tool": "resize", "args": {"size": {"width": 450, "height": 310}}}, {"tool": "resizing", "args": {}}]}])
    result = run(brain.turn("make the image bigger"))
    resize, other = result["actions"]
    assert (resize["ok"], resize["summary"]) == (False, "there is no resize tool; use present with do='resize'")
    assert resize["next"] == "use present with do='resize'"
    assert (other["ok"], other["summary"]) == (False, "there is no resizing tool")  # exact names only
    assert "resize" in VERBS and brain.registry.tools["present"].verbs == VERBS


def test_voice_turn_records_a_note_and_typed_turn_notes_voice():
    brain, _, _ = make([
        {"answer": "A stoma.", "targets": [], "actions": []},
        {"answer": "Guard cells open the pore.", "targets": [], "actions": []},
    ])
    injected = []

    class FakeVoice:
        closed = False

        async def inject(self, text, respond=False, **_log):
            injected.append((text, respond))

    brain.conversation.add("user", "voice", "what's that?")  # recorded from the transcript
    brain.voice = FakeVoice()
    run(brain.turn("what's that?", "voice"))
    run(brain.turn("what do guard cells do?", "chat"))
    turns = [(t.role, t.channel) for t in brain.conversation.turns]
    assert turns == [("user", "voice"), ("note", "voice"), ("user", "chat"), ("assistant", "chat")]
    # Chat notes go out with the next view note (voice/route.py), not as a separate append.
    assert brain.voice_notes == ['Chat log, nothing to say about it: the user typed a request (what do guard cells do?) and the '
                                 'app already answered in writing (Guard cells open the pore.).']
    assert "(spoken)" in brain.conversation.text()


def test_resolve_object_by_id_target_label_and_defaults():
    hub = FakeHub()
    a, b = obj(hub, "stoma"), obj(hub, "guard cell", x=300)
    from orchestrator.brain.registry import ToolContext

    ctx = ToolContext("chat", 1, 7, objects=[b.id])
    assert resolve_object(hub, a.id, ctx) is a
    assert resolve_object(hub, "target 1", ctx) is b
    assert resolve_object(hub, "stoma", ctx) is a
    assert resolve_object(hub, None, ctx) is b  # this answer's first target
    with pytest.raises(ToolFailed):
        resolve_object(hub, "nucleus", ctx)


def test_parse_actions_is_lenient():
    assert parse_actions([{"tool": "zoom", "args": '{"object": "o1"}'}, {"name": "hold"}, "junk", {"args": {}}]) == [
        ("zoom", {"object": "o1"}), ("hold", {})]
    assert parse_actions(None) == []


def test_registry_exports_for_both_providers():
    registry = Registry()

    async def h(ctx, args):
        return ""

    registry.add(Tool("zoom", "Open the magnifier.", schema({"object": {"type": "string", "description": "id"}}, ["object"]), h))
    assert '"zoom"(object: id): Open the magnifier.' in registry.prompt_section()
    assert registry.responses_tools()[0]["parameters"]["required"] == ["object"]
    assert registry.gemini_declarations()[0].name == "zoom"


def test_actions_find_an_object_the_answer_re_outlined():
    from orchestrator.brain.loop import replaced_by

    hub = FakeHub()
    old = obj(hub, "stoma", x=200)
    before = {old.id: old}
    hub.scene.remove(old.id)  # a new answer replaced it...
    new = obj(hub, "stoma", x=205)  # ...with an outline of the same thing
    far = obj(hub, "stoma", x=600)
    aliases = replaced_by(before, [far, new], hub.scene.objects)
    assert aliases == {old.id: new.id}
    from orchestrator.brain.registry import ToolContext

    assert resolve_object(hub, old.id, ToolContext("chat", 1, 7, aliases=aliases)) is new


def test_a_new_answer_keeps_an_outline_of_the_same_thing(monkeypatch):
    from orchestrator import agent as agent_mod
    from orchestrator import tools
    from orchestrator.agent import Agent
    from orchestrator.coords import Crop

    hub = FakeHub()
    hub.infos = {7: hub.latest_info}
    old = hub.scene.add(SceneObject(id="o1", kind="highlight", polygons=[[[100, 100], [200, 100], [200, 200], [100, 200]]],
                                    anchor_frame=7, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="ai:x", label="stoma"))
    other = hub.scene.add(SceneObject(id="o2", kind="highlight", polygons=[[[600, 300], [650, 300], [650, 350]]],
                                      anchor_frame=7, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="ai:x", label="dot"))
    hub.scene.select("o1")
    made = []

    async def fake_outline(hub_, frame_id, x, y, **kw):
        o = SceneObject(id=f"n{len(made)}", kind="highlight", polygons=[[[x, y], [x + 5, y], [x, y + 5]]],
                        anchor_frame=frame_id, anchor_epoch=0, anchor_offset=(0.0, 0.0), source=kw["source"], label=kw["label"])
        made.append(o)
        return hub_.scene.add(o)

    monkeypatch.setattr(tools, "outline_at", fake_outline)
    crop = Crop(0, 0, 1000, 1000)  # box_2d on 0-1000 maps straight to pixels
    agent = Agent(hub)
    objects = asyncio.run(agent.point([
        {"box_2d": [105, 98, 198, 205], "label": "central stoma", "confidence": 0.9},  # same as o1
        {"box_2d": [400, 400, 450, 450], "label": "new thing", "confidence": 0.5},
    ], 7, crop))
    assert objects[0] is old and old.label == "central stoma"  # kept, relabelled
    assert hub.scene.selected == "o1" and "o2" not in hub.scene.objects  # the unmatched old one went
    assert len(made) == 1 and objects[1] is made[0]


def test_a_look_never_replaces_what_its_own_request_outlined(monkeypatch):
    """Acting bench live cases (2026-10-11): "outline my nose and my mouth, then zoom in on the mouth": the fast plan's
    finds outlined both, then the look pointed at the mouth again and its replacing of earlier highlights took the
    nose off. A new answer replaces earlier requests' outlines, never its own request's."""
    from orchestrator import tools
    from orchestrator.agent import Agent
    from orchestrator.coords import Crop

    hub = FakeHub()
    hub.infos = {7: hub.latest_info}
    nose = hub.scene.add(SceneObject(id="o1", kind="highlight", polygons=[[[100, 100], [150, 100], [150, 150]]],
                                     anchor_frame=7, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="ai:x", label="nose"))
    earlier = hub.scene.add(SceneObject(id="o2", kind="highlight", polygons=[[[600, 300], [650, 300], [650, 350]]],
                                        anchor_frame=7, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="ai:x", label="wall"))

    async def fake_outline(hub_, frame_id, x, y, **kw):
        return hub_.scene.add(SceneObject(id="n1", kind="highlight", polygons=[[[x, y], [x + 5, y], [x, y + 5]]],
                                          anchor_frame=frame_id, anchor_epoch=0, anchor_offset=(0.0, 0.0),
                                          source=kw["source"], label=kw["label"]))

    monkeypatch.setattr(tools, "outline_at", fake_outline)
    asyncio.run(Agent(hub).point([{"box_2d": [400, 400, 450, 450], "label": "mouth", "confidence": 0.9}], 7,
                                 Crop(0, 0, 1000, 1000), own={nose.id}))
    assert nose.id in hub.scene.objects and earlier.id not in hub.scene.objects and "n1" in hub.scene.objects


def test_a_follow_up_sees_the_board_its_steps_left():
    """The second call is told what the first one outlined (it was told the board from before)."""
    async def status(ctx, args):
        return "researcher 1 is still working"

    brain, _, agent = make([
        {"answer": "That's a stoma.", "targets": [{"box_2d": [1, 2, 3, 4], "label": "stoma"}],
         "actions": [{"tool": "research_status", "args": {}}]},
        {"answer": "A stoma; the researcher is still working.", "targets": [], "actions": []},
    ], [Tool("research_status", "", schema({}), status, kind="query")])
    run(brain.turn("what is that, and is the researcher done?"))
    first, second = (call["tools"][1] for call in agent.calls)
    assert '"stoma"' not in first and '"stoma"' in second


def test_a_requests_steps_run_in_order_each_on_the_board_the_one_before_left():
    order = []

    async def first(ctx, args):
        order.append("first starts")
        await asyncio.sleep(0.05)
        order.append("first done")
        return "first"

    async def second(ctx, args):
        order.append("second starts")
        return "second"

    brain, _, _ = make([{"answer": "Doing both.", "targets": [], "actions": [
        {"tool": "first", "args": {}}, {"tool": "second", "args": {}}]}],
        [Tool("first", "", schema({}), first), Tool("second", "", schema({}), second)])
    result = run(brain.turn("do the first then the second"))
    assert order == ["first starts", "first done", "second starts"]
    assert [a["ok"] for a in result["actions"]] == [True, True]


def test_a_step_waiting_behind_a_slow_one_is_reported_when_it_has_run():
    async def slow(ctx, args):
        await asyncio.sleep(0.2)
        return "dissected it"

    async def after(ctx, args):
        return "selected the part"

    brain, _, _ = make([{"answer": "Breaking it down, then selecting a part.", "targets": [], "actions": [
        {"tool": "dissect", "args": {}}, {"tool": "pick", "args": {}}]}],
        [Tool("dissect", "", schema({}), slow, inline_ms=20), Tool("pick", "", schema({}), after, inline_ms=20)])
    late = []

    async def report(ctx, r):
        late.append(r.summary)

    brain.report_late = report

    async def go():
        result = await brain.turn("break it down and select a part")
        assert [a["summary"] for a in result["actions"]] == ["still working", "waiting its turn"]
        await asyncio.sleep(0.4)

    run(go())
    assert late == ["dissected it", "selected the part"]


def test_the_answer_is_offered_to_the_voice_before_its_tools_run_without_claims():
    from orchestrator.brain.loop import draft_text

    assert draft_text("That's likely a stoma. Here it is outlined.") == "That's likely a stoma."
    assert draft_text("I've closed it.") == ""
    drafts = []

    async def say(text):
        drafts.append(text)

    brain, _, _ = make([{
        "answer": "That's likely a stoma. Selecting it.",
        "targets": [{"box_2d": [1, 2, 3, 4], "label": "stoma", "confidence": 0.8}],
        "actions": [{"tool": "select", "args": {"object": "target 1"}}],
    }])
    run(brain.turn("what's that oval?", "voice", draft=say))
    assert drafts == ["That's likely a stoma. Selecting it."]


def test_an_answer_waiting_on_query_tools_is_not_offered_early():
    async def status(ctx, args):
        return "researcher 1 found a trichome"

    drafts = []

    async def say(text):
        drafts.append(text)

    tool = Tool("research_status", "What researchers found.", schema({}), status, kind="query")
    brain, _, _ = make([
        {"answer": "Checking.", "targets": [], "actions": [{"tool": "research_status", "args": {}}]},
        {"answer": "Researcher 1 found a trichome.", "targets": [], "actions": []},
    ], [tool])
    run(brain.turn("what did the researcher find?", "voice", draft=say))
    assert drafts == []


def pointing_probe(seen):
    """A tool that records what "that" (or its object) means to it, and the request's saved pointing."""
    async def probe(ctx, args):
        seen.append((ctx.when_asked, resolve_object(ctx.board.hub if ctx.board else None, args.get("object"), ctx).id))
        return "ok"

    return Tool("probe", "", schema({"object": {"type": "string"}}), probe)


def test_a_turns_tools_see_what_was_pointed_at_when_the_request_was_made():
    """The partner's Amendment 1, C2: what was pointed at when the user made the request (by voice, when the words
    were heard) reaches every tool of the turn, and the models are told it beside what is selected now; a typed
    request saves it as the turn starts."""
    from orchestrator.board import Board

    seen = []
    reply = {"answer": "Zooming in.", "targets": [], "actions": [{"tool": "probe", "args": {"object": "that"}}]}
    brain, hub, agent = make([reply, reply], [pointing_probe(seen)])
    brain.board = Board(hub)
    stoma, pore = obj(hub, "stoma"), obj(hub, "pore", 300)
    hub.scene.select(stoma.id)
    heard = brain.board.when_asked()
    hub.scene.select(pore.id)  # after the words, before the hand-off
    run(brain.turn("zoom on that", "voice", when_asked=heard))
    assert seen[0] == (heard, stoma.id)
    assert agent.calls[0]["tools"][2] == f" (selected: {stoma.id} when the user asked, {pore.id} now)"
    run(brain.turn("zoom on that"))  # typed: saved as it starts
    assert seen[1][0].selected == pore.id and seen[1][1] == pore.id
    assert agent.calls[1]["tools"][2] == f" (selected: {pore.id})"


def test_a_requests_own_selection_is_what_it_means_in_its_later_steps():
    """The executor's P3, through the real select tool: "select the stoma and measure it", with the pore selected
    when the user asked: "it" is the stoma."""
    from orchestrator.board import Board

    seen = []
    reply = {"answer": "Selecting the stoma and measuring it.", "targets": [],
             "actions": [{"tool": "select", "args": {"object": "stoma"}}, {"tool": "probe", "args": {"object": "it"}}]}
    brain, hub, agent = make([reply], [pointing_probe(seen)])
    brain.board = Board(hub)
    stoma, pore = obj(hub, "stoma"), obj(hub, "pore", 300)
    hub.scene.select(pore.id)
    run(brain.turn("select the stoma and measure it"))
    assert seen[0][1] == stoma.id


def test_the_fast_pass_acts_on_what_was_pointed_at_when_the_request_was_made():
    """The executor's M12: the fast pass's steps, done before the look, get the request's saved pointing too."""
    from orchestrator.board import Board
    from tests.test_sight import make_brain

    seen = []
    brain, hub, agent = make_brain({"needs_look": False, "intent": "request", "answer": "Zooming in.",
                                    "actions": [{"tool": "probe", "args": {"object": "that"}}]},
                                   {"answer": "slow", "targets": []}, vision_delay=5)
    brain.registry.add(pointing_probe(seen))
    brain.board = Board(hub)
    stoma, pore = obj(hub, "stoma"), obj(hub, "pore", 300)
    hub.scene.select(stoma.id)
    heard = brain.board.when_asked()
    hub.scene.select(pore.id)
    run(brain.turn("zoom on that", "voice", when_asked=heard))
    assert seen == [(heard, stoma.id)]


def test_what_the_fast_pass_selected_is_it_for_the_looks_steps():
    """The executor's survivor L5: "select the stoma and measure it" split between the fast pass (the select, done at
    once) and the look (the rest): the look's "it" is still the stoma the request selected, not the pore selected
    when the user asked."""
    from orchestrator.board import Board
    from tests.test_sight import make_brain

    seen = []
    brain, hub, agent = make_brain({"needs_look": True, "intent": "request", "answer": "Selecting the stoma.",
                                    "actions": [{"tool": "select", "args": {"object": "stoma"}}]},
                                   {"answer": "Measuring it.", "targets": [], "actions": [{"tool": "probe", "args": {"object": "it"}}]})
    brain.registry.add(pointing_probe(seen))
    brain.board = Board(hub)
    stoma, pore = obj(hub, "stoma"), obj(hub, "pore", 300)
    hub.scene.select(pore.id)
    run(brain.turn("select the stoma and measure it"))
    assert [s[1] for s in seen] == [stoma.id]


def test_follow_up_calls_after_a_query_tool_are_told_the_selection_then_and_now():
    """The executor's survivors N21 and PF1: after a query tool, the look's follow-up call and the fast pass's
    follow-up are told what was selected when the user asked, beside what is selected now."""
    from orchestrator.board import Board
    from tests.test_sight import make_brain

    async def status(ctx, args):
        return "nothing new"

    query = Tool("research_status", "", schema({}), status, kind="query")
    reply = {"answer": "Checking.", "targets": [], "actions": [{"tool": "research_status", "args": {}}]}
    brain, hub, agent = make([reply, {"answer": "Done.", "targets": [], "actions": []}], [query])
    brain.board = Board(hub)
    stoma, pore = obj(hub, "stoma"), obj(hub, "pore", 300)
    hub.scene.select(stoma.id)
    heard = brain.board.when_asked()
    hub.scene.select(pore.id)
    run(brain.turn("what is that?", "voice", when_asked=heard))
    told = f" (selected: {stoma.id} when the user asked, {pore.id} now)"
    assert len(agent.calls) == 2 and agent.calls[1]["tools"][2] == told  # the look's follow-up (N21)

    fast, hub2, agent2 = make_brain({"needs_look": False, "intent": "question", "answer": "Checking.",
                                     "actions": [{"tool": "research_status", "args": {}}]},
                                    {"answer": "slow", "targets": []}, vision_delay=5)
    fast.registry.add(query)
    fast.board = Board(hub2)
    a, b = obj(hub2, "stoma"), obj(hub2, "pore", 300)
    hub2.scene.select(a.id)
    heard = fast.board.when_asked()
    hub2.scene.select(b.id)
    run(fast.turn("what is that?", "voice", when_asked=heard))
    follow_ups = agent2.fast_model.prompts[1:]
    assert follow_ups and all(f"(selected: {a.id} when the user asked, {b.id} now)" in p for p in follow_ups)  # PF1


def test_the_models_are_told_the_cell_selected_when_the_user_asked(world):
    """The executor's survivor N25: a cell selected on the layer when the user asked is what the models are told
    was selected then, beside what is selected now."""
    from orchestrator.board import Board
    from orchestrator.brain.context import selected_as_asked
    from orchestrator.population import layer_of
    from tests.test_population import cells

    population, _ = cells(world)
    layer = layer_of(world)
    c1, c2 = population.live()[0], population.live()[1]
    layer.select(c1)
    asked = Board(world).when_asked()
    layer.select(c2)
    told = selected_as_asked(world, asked)
    assert told.startswith(f"{c1.id}, a cell of {population.title} when the user asked, {c2.id}, a cell of ")


@pytest.mark.parametrize("failed", [False, True])
def test_query_continuations_keep_all_prior_effect_and_query_receipts(failed):
    """A follow-up used to repeat a correction because it saw only the query result."""
    async def effect(ctx, args):
        if failed:
            raise ToolFailed("correction declined: member is already excluded")
        return "member excluded in this request; revision 2 saved"

    async def query(ctx, args):
        return "stored source e8 has 11 members" if args.get("part") == "count" else "largest is c29: 500 px²"

    brain, _, agent = make([
        {"answer": "Checking.", "actions": [{"tool": "correct_cells", "args": {"member": "c28"}},
                                               {"tool": "query_population", "args": {"part": "count"}}]},
        {"answer": "Checking the largest.", "actions": [{"tool": "query_population", "args": {"part": "largest"}}]},
    ], [Tool("correct_cells", "", schema({}), effect), Tool("query_population", "", schema({}), query, kind="query")])
    receipt = "correction declined: member is already excluded" if failed else "member excluded in this request; revision 2 saved"
    agent.fast_model.rewrite = {"answer": f"{receipt}; stored source e8 has 11 members; largest is c29: 500 px²"}
    result = run(brain.turn("Exclude c28 and tell me the remaining count and largest area"))
    # Third-round evidence is now sent to the reserved final provider, not another look.
    assert result["calls"] == 3 and len(agent.calls) == 2 and len(agent.fast_model.prompts) == 1
    for prompt in [agent.calls[1]["question"], agent.fast_model.prompts[0]]:
        assert receipt in prompt
        assert "c28" in prompt
        assert "stored source e8 has 11 members" in prompt
    assert "largest is c29: 500 px²" in agent.fast_model.prompts[0]
    assert receipt in result["answer"] and "largest is c29: 500 px²" in result["answer"]


def test_finished_query_receipts_survive_later_failure_into_reconciliation_and_wire():
    contexts = []
    async def read(ctx, args):
        contexts.append(ctx)
        if args.get('bad'):
            raise ToolFailed('invalid step reference; use the saved population id')
        return 'e8: c29 has measured area 500 px²'

    brain, _, agent = make([
        {'answer': 'Reading.', 'actions': [{'tool': 'query_population', 'args': {}}]},
        {'answer': 'Checking.', 'actions': [{'tool': 'query_population', 'args': {'bad': True}}]},
    ], [Tool('query_population', '', schema({}), read, kind='query')])
    # A final timeout retains actual activity, without an application-written answer.
    agent.fast_model.rewrite = None
    result = run(brain.turn('Read the saved area'))
    assert [(a['tool'], a['ok']) for a in result['actions']] == [('query_population', True), ('query_population', False)]
    assert result['calls'] == 3 and len(agent.calls) == 2 and len(agent.fast_model.prompts) == 1
    assert 'e8: c29 has measured area 500 px²' in agent.fast_model.prompts[0]
    assert 'invalid step reference; use the saved population id' in agent.fast_model.prompts[0]
    assert [a['summary'] for a in result['actions']] == [
        'e8: c29 has measured area 500 px²', 'invalid step reference; use the saved population id']
    budget = contexts[0].admission
    assert all(ctx.admission is budget for ctx in contexts) and budget.used == result['calls'] == 3
    assert budget.attempts[-1].status == 'failed'
    assert all(a['requestId'] == budget.request_id for a in result['actions'])
    assert len({a['actionId'] for a in result['actions']}) == 2
    assert all(a['actionId'] in agent.fast_model.prompts[0] for a in result['actions'])
    assert result['answer'] == '' and result['answerStatus'] == 'not-published'
    assert not any(t.role == 'assistant' for t in brain.conversation.turns)


@pytest.mark.parametrize('missing_step', [True, False])
def test_successful_display_can_finish_remaining_request_without_replaying(missing_step):
    """m55: opening the picture succeeded, but removing its overlay was omitted.

    Script the provider, not the executor: the outcome round must retain tools
    and execute its remaining steps before the final answer. A complete first
    plan needs no additional provider call beyond its existing outcome round.
    """
    from tests.test_sight import make_brain

    state = {'image': False, 'overlay': True, 'opacity': 1.0}
    performed = []

    async def present(ctx, args):
        performed.append(args['do'])
        if args['do'] == 'open':
            state['image'] = True
            return 'image window w1 drawn; overlay tracks:m55 is unchanged'
        if args['do'] == 'hide':
            assert state['image']
            state['overlay'] = False
            return 'tracks:m55 hidden'
        assert args['do'] == 'opacity' and not state['overlay']
        state['opacity'] = args['value']
        return 'w1 opacity confirmed at 0.5'

    first = [{'tool': 'display', 'args': {'do': 'open'}}]
    rest = [{'tool': 'display', 'args': {'do': 'hide', 'id': 'tracks:m55'}},
            {'tool': 'display', 'args': {'do': 'opacity', 'instance': 'w1', 'value': 0.5}}]
    brain, _, agent = make_brain(
        {'intent': 'request', 'needs_look': False, 'answer': 'Opening the image.',
         'actions': first if missing_step else first + rest}, {}, vision_delay=5)
    brain.registry.add(Tool('display', 'Change a display', schema({}), present))
    # COMPLETION every-plan (8b838aac): every carried-out plan asked about again; the default is "unfinished".
    brain.hub.settings.completion, brain.hub.settings.answer_check = 'every-plan', 'every-request'
    initial_ask = agent.fast_model.ask
    outcome_prompts = []

    async def provider(images, prompt, **kwargs):
        if 'only your notes' in prompt:
            return await initial_ask(images, prompt, **kwargs)
        outcome_prompts.append(prompt)
        actions = rest if missing_step and 'Available tools for remaining work:' in prompt else []
        return SimpleNamespace(data={'answer': 'The picture is open and the overlay removed.',
                                     'actions': actions, 'observation_scope': 'none', 'input_refs': []}, latency_ms=5)

    agent.fast_model.ask = provider
    result = run(brain.turn('Open it as an image, no longer overlay, then fade the image to 50%'))
    assert state == {'image': True, 'overlay': False, 'opacity': 0.5}
    assert performed == ['open', 'hide', 'opacity']
    assert result['calls'] == (3 if missing_step else 2)
    assert result['answer'] == 'The picture is open and the overlay removed.'
    assert 'tracks:m55 hidden' in outcome_prompts[-1]


def _display_brain(present):
    """A fast plan of one `display` step, with every later model call recorded."""
    from tests.test_sight import make_brain

    brain, _, agent = make_brain({'intent': 'request', 'needs_look': False, 'answer': 'Opening the image.',
                                  'actions': [{'tool': 'display', 'args': {'do': 'open'}}]}, {}, vision_delay=5)
    brain.registry.add(Tool('display', 'Change a display', schema({}), present))
    initial_ask = agent.fast_model.ask
    later = []

    async def provider(images, prompt, **kwargs):
        if 'only your notes' in prompt and not later:
            return await initial_ask(images, prompt, **kwargs)
        later.append(prompt)
        return SimpleNamespace(data={'answer': "I couldn't open the picture.", 'actions': [],
                                     'observation_scope': 'none', 'input_refs': []}, latency_ms=5)

    agent.fast_model.ask = provider
    return brain, later


def test_a_plan_that_worked_is_the_answer_with_completion_unfinished_and_answer_check_contradicted():
    """COMPLETION "unfinished" and ANSWER_CHECK "contradicted" (Phase 2): a fast plan whose step did what was asked
    is not asked about again, and its answer is not written again: the request costs its one call."""
    async def present(ctx, args):
        return 'image window w1 drawn'

    brain, later = _display_brain(present)
    brain.hub.settings.completion, brain.hub.settings.answer_check = 'unfinished', 'contradicted'
    result = run(brain.turn('Open it as an image'))
    assert result['calls'] == 1 and not later
    assert result['answer'] == 'Opening the image.'


def test_answer_check_contradicted_keeps_the_completion_rounds_answer():
    """ANSWER_CHECK "contradicted" with every plan asked about again: the completion round's answer saw the receipts,
    so it is not written a third time."""
    async def present(ctx, args):
        return 'image window w1 drawn'

    brain, later = _display_brain(present)
    brain.hub.settings.completion, brain.hub.settings.answer_check = 'every-plan', 'contradicted'
    result = run(brain.turn('Open it as an image'))
    assert len(later) == 1 and result['calls'] == 2


@pytest.mark.parametrize('completion', ['every-plan', 'unfinished'])
def test_a_failed_step_still_goes_back_to_the_model_whatever_the_settings(completion):
    """A step that failed contradicts the answer written before it ran: it is answered again either way."""
    async def present(ctx, args):
        raise ToolFailed('the picture could not be opened')

    brain, later = _display_brain(present)
    brain.hub.settings.completion, brain.hub.settings.answer_check = completion, 'contradicted'
    result = run(brain.turn('Open it as an image'))
    assert later and result['answer'] == "I couldn't open the picture."


def test_fast_followup_uses_a_continuation_prompt_with_current_facts():
    from orchestrator.brain.fast import decide
    brain, _, _ = make([])
    model = FakeVision(fast={'intent': 'request', 'needs_look': False, 'answer': 'Done.', 'actions': []},
                       rewrite={'intent': 'request', 'needs_look': False, 'answer': 'Done.', 'actions': []})
    from orchestrator.ticket import issue, using
    from orchestrator.brain.dispatch import answer_phase

    request = issue(brain.hub, "turn")
    async def go():
        with using(request), answer_phase("query"):
            return await decide(brain.moment(), 'e8 was corrected; now 11 members', model,
                                'query_population', 'gemini-3.5-flash', followup=True)
    try:
        run(go())
        assert request.admission.used == 1 and request.admission.attempts[0].phase == "query"
    finally:
        request.seal()
    prompt = model.prompts[-1]
    assert 'e8 was corrected; now 11 members' in prompt
    assert "First, what are the user's words" not in prompt
    assert 'query_population' in prompt


def test_exhausted_query_budget_keeps_earlier_failures_and_failed_outlining():
    import json

    contexts = []
    async def read(ctx, args):
        contexts.append(ctx)
        return 'stored result available'
    async def fail(ctx, args):
        contexts.append(ctx)
        raise ToolFailed('the requested old source is missing', next='choose another source')
    brain, hub, agent = make([
        {'answer': 'Outlining it.', 'targets': [{'label': 'candidate'}], 'actions': [
            {'tool': 'failed_operation', 'args': {}}, {'tool': 'read_saved', 'args': {}}]},
        {'answer': 'Checking.', 'actions': [{'tool': 'read_saved', 'args': {}}]},
    ], [Tool('failed_operation', '', schema({}), fail), Tool('read_saved', '', schema({}), read, kind='query')])
    hub.settings.fast = 'off'
    async def cannot_outline(*args, **kwargs):
        kwargs['errors'].append('the target could not be outlined')
        return []
    agent.point = cannot_outline
    result = run(brain.turn('Outline the candidate and retrieve the old source'))
    assert result['answer'] == '' and result['answerStatus'] == 'not-published'
    assert not any(t.role == 'assistant' for t in brain.conversation.turns)
    assert [(a['ok'], a['summary']) for a in result['actions']] == [
        (False, 'the requested old source is missing; choose another source'), (True, 'stored result available'),
        (True, 'stored result available')]
    assert result['actions'][0]['next'] == 'choose another source'
    assert result['calls'] == 3 and len(agent.calls) == 2 and len(agent.fast_model.prompts) == 1
    final_prompt = agent.fast_model.prompts[0]
    assert 'the requested old source is missing' in final_prompt
    assert 'the target could not be outlined' in final_prompt
    assert 'stored result available' in final_prompt
    budget = contexts[0].admission
    assert all(ctx.admission is budget for ctx in contexts) and budget.used == result['calls'] == 3
    assert budget.attempts[-1].status == 'failed'
    assert all(a['requestId'] == budget.request_id for a in result['actions'])
    assert len({a['actionId'] for a in result['actions']}) == 3
    assert all(a['actionId'] in final_prompt for a in result['actions'])
    wrapper = '<answer-facts source="app" untrusted="true">'
    assert final_prompt.count(wrapper) == 1
    body = final_prompt.split(wrapper, 1)[1].split('</answer-facts>', 1)[0]
    facts = json.loads(body)  # independently decode the actual external prompt, not a producer helper
    assert facts['omittedFacts'] == facts['omittedOperations'] == 0
    assert facts['operations'] == [{k: v for k, v in a.items() if k not in ACTIVITY_KEYS} for a in result['actions']]


class SequencedRewrites(FakeVision):
    """The answer rewrite's replies in order (a first writing, then the one more try after an omission)."""

    def __init__(self, rewrites):
        super().__init__()
        self.rewrites = list(rewrites)

    async def ask(self, images, prompt, max_output_tokens=0):
        self.prompts.append(prompt)
        if "only your notes" in prompt or not self.rewrites:
            raise asyncio.TimeoutError("no model for this prompt in the test")
        return SimpleNamespace(data=self.rewrites.pop(0), latency_ms=5.0)


def failing_request(rewrites):
    async def first(ctx, args):
        raise ToolFailed("there is no 'SAM 3.1 (local)' that can do the outlining")

    async def second(ctx, args):
        raise ToolFailed("the highlight could not be drawn: nothing there could be outlined")

    brain, hub, agent = make([{"answer": "Switching to SAM 3.1 and outlining the pith.",
                               "actions": [{"tool": "use_model", "args": {}}, {"tool": "outline_pith", "args": {}}]},
                              {"answer": "Neither worked.", "actions": []}],
                             [Tool("use_model", "", schema({}), first), Tool("outline_pith", "", schema({}), second)])
    hub.settings.answer_check = "every-request"
    agent.fast_model = SequencedRewrites(rewrites)
    return brain, agent


def test_the_ids_an_answer_states_never_reach_its_words():
    """Live 2026-10-11 ("close that"): "...so closing it did not need to run again (p1)." The ids go in `stated`; the
    app's own ones are taken out of the words, any other parenthesis left as written."""
    from orchestrator.brain.loop import without_ids

    brain, _agent = failing_request([
        {"answer": "SAM 3.1 on this Mac isn't a model I have (p1), and the pith could not be outlined [p2].",
         "stated": ["p1", "p2"]},
    ])
    result = run(brain.turn("okay use that"))
    assert result["answer"] == "SAM 3.1 on this Mac isn't a model I have, and the pith could not be outlined."
    ids = {"p1": "", "c1": ""}
    assert without_ids("Drew it (c1) and failed (p1, c1).", ids) == "Drew it and failed."
    assert without_ids("Cell (p10) at 40x (estimated)", ids) == "Cell (p10) at 40x (estimated)"


def test_an_answer_that_leaves_out_a_failure_is_written_once_more_told_what_it_left_out():
    """Live 2026-10-11 02:40: told two steps had failed, the rewrite said "I switched to micro-sam and outlined the
    pith". The reply names the problems it states; a left-out one is a confirmed omission, written once more."""
    brain, agent = failing_request([
        {"answer": "I switched models and outlined the pith.", "stated": ["p1"]},
        {"answer": "SAM 3.1 on this Mac isn't a model I have, and the pith could not be outlined.", "stated": ["p1", "p2"]},
    ])
    result = run(brain.turn("okay use that"))
    assert result["answer"] == "SAM 3.1 on this Mac isn't a model I have, and the pith could not be outlined."
    rewrites = [p for p in agent.fast_model.prompts if "What did not work" in p]
    assert len(rewrites) == 2 and "did not tell the user about: p2" in rewrites[1]
    [record] = [r for r in agent.logged if r.get("rule") == "answer-recheck"]
    assert record["retried"] is True and set(record["missing"]) == {"p2"} and record["stillMissing"] == []


def test_an_answer_that_states_every_failure_or_confirms_nothing_is_not_asked_again():
    """No judge on every request: a reply listing every problem stands; one without the list is kept as unchecked."""
    for reply in ({"answer": "Neither SAM 3.1 here nor the pith outline worked.", "stated": ["p1", "p2"]},
                  {"answer": "Neither SAM 3.1 here nor the pith outline worked."}):
        brain, agent = failing_request([reply])
        result = run(brain.turn("okay use that"))
        assert result["answer"] == reply["answer"]
        assert len([p for p in agent.fast_model.prompts if "What did not work" in p]) == 1


def test_a_step_tried_again_successfully_is_not_a_failure_to_tell_and_a_choice_says_its_scope():
    """claims.unresolved and choice_scopes: a refused `use` settled by a working one is not a problem; a `use` is
    this request only (the receipt said so, and the answer still said "switched")."""
    from orchestrator.brain.claims import answer_facts, choice_scopes, unresolved
    from orchestrator.brain.registry import ToolRun

    refused = ToolRun("use", {"implementation": "SAM 3.1 (local)"}, ok=False, summary="there is no 'SAM 3.1 (local)'")
    worked = ToolRun("use", {"implementation": "micro-sam"}, ok=True,
                     summary="for this request, outlining uses micro-sam (light microscopy), exactly that, with no fallback")
    failed_find = ToolRun("find", {"what": "pith"}, ok=False, summary="nothing there could be outlined")
    assert unresolved([refused, worked, failed_find]) == [failed_find]
    [scope] = choice_scopes([refused, worked])
    assert scope["applies"].startswith("this request only")
    assert "this request only; the default setting is unchanged" in answer_facts([refused, worked])


def choosing_request(rewrites):
    """A request that chose a model for itself and redrew with it (no step failed)."""
    async def use_model(ctx, args):
        return "for this request, outlining uses SAM 2.1 tiny (on this Mac), exactly that, with no fallback"

    async def redraw(ctx, args):
        return "outlined the stoma again with SAM 2.1 tiny"

    brain, hub, agent = make([{"answer": "Switched to SAM 2.1 and redrew the stoma.",
                               "actions": [{"tool": "use", "args": {}}, {"tool": "highlight_again", "args": {}}]}],
                             [Tool("use", "", schema({}), use_model), Tool("highlight_again", "", schema({}), redraw)])
    hub.settings.answer_check = "contradicted"  # no failure here: only the choice brings the rewrite
    agent.fast_model = SequencedRewrites(rewrites)
    return brain, agent


def test_a_model_choice_is_said_with_its_scope_or_the_answer_is_written_once_more():
    """Live 2026-10-11 (journeys "try the other model", "use SAM 2.1 for this"): every answer said "switched to SAM
    2.1" for a choice that held for that request only. A choice is written from its receipt, with an id the reply
    states like a problem's; left out, it is written once more (the user's Q3: the answer says which it did)."""
    brain, agent = choosing_request([
        {"answer": "Switched to SAM 2.1 and redrew the stoma.", "stated": []},
        {"answer": "I redrew the stoma with SAM 2.1 tiny for this request; outlines still use EdgeTAM otherwise.",
         "stated": ["c1"]},
    ])
    result = run(brain.turn("outline it with sam 2.1"))
    assert result["answer"].startswith("I redrew the stoma with SAM 2.1 tiny for this request")
    rewrites = [p for p in agent.fast_model.prompts if "Model choices in this request" in p]
    assert rewrites and "c1: for this request, outlining uses SAM 2.1 tiny" in rewrites[0]
    assert "did not tell the user about: c1" in rewrites[-1]

    stated, agent = choosing_request([{"answer": "Redrawn with SAM 2.1 tiny, for this request only.", "stated": ["c1"]}])
    assert run(stated.turn("outline it with sam 2.1"))["answer"] == "Redrawn with SAM 2.1 tiny, for this request only."
    assert len([p for p in agent.fast_model.prompts if "Model choices in this request" in p]) == 1


def chose_and_redrew(completion: dict, final=None):
    """Journeys round 2 (live 2026-10-11): the fast pass chose SAM 2.1 for the request and redrew, the completion round
    answers `completion`, and an answer written from the receipts answers `final` (or raises it)."""
    from tests.test_sight import make_brain

    async def use_model(ctx, args):
        return "for this request, outlining uses SAM 2.1 tiny (on this Mac), exactly that, with no fallback"

    async def redraw(ctx, args):
        return "outlined the stoma 1 again with SAM 2.1 tiny"

    brain, _, agent = make_brain({"intent": "request", "needs_look": False, "answer": "Trying SAM 2.1 on the stoma.",
                                  "actions": [{"tool": "use", "args": {"operation": "outline"}},
                                              {"tool": "highlight_again", "args": {}}]},
                                 {}, vision_delay=5)
    brain.registry.add(Tool("use", "", schema({}), use_model))
    brain.registry.add(Tool("highlight_again", "", schema({}), redraw))
    brain.hub.settings.completion, brain.hub.settings.answer_check = "every-plan", "contradicted"
    initial_ask = agent.fast_model.ask
    prompts = {"completion": [], "final": []}

    async def provider(images, prompt, **kwargs):
        if "only your notes" in prompt and not prompts["completion"] and not prompts["final"]:
            return await initial_ask(images, prompt, **kwargs)
        if "Your reply (written before the actions ran)" in prompt:  # the answer written from the receipts
            prompts["final"].append(prompt)
            if isinstance(final, BaseException):
                raise final
            return SimpleNamespace(data={**final, "observation_scope": "none", "input_refs": []}, latency_ms=5)
        prompts["completion"].append(prompt)
        return SimpleNamespace(data={**completion, "actions": [], "observation_scope": "none", "input_refs": []},
                               latency_ms=5)

    agent.fast_model.ask = provider
    return brain, agent, prompts


def test_a_completion_round_that_states_the_choice_is_the_answer_with_no_call_after_it():
    """The lead's (a), journeys round 2: the completion round is given the model choices with their ids and the
    `stated` contract (loop.stated_block, the same ids and wording as the answer written from the receipts). An answer
    that states them all stands: the request costs 2 calls, as before the scope was checked."""
    brain, _, prompts = chose_and_redrew({"answer": "Redrew stoma 1 with SAM 2.1 tiny, for this request only.",
                                          "stated": ["c1"]})
    result = run(brain.turn("hmm, try the other model"))
    assert result["answer"] == "Redrew stoma 1 with SAM 2.1 tiny, for this request only." and result["calls"] == 2
    [completion] = prompts["completion"]
    assert "Model choices in this request, each said with what it applies to:\n- c1: for this request, outlining uses " \
           "SAM 2.1 tiny" in completion and 'include in the JSON stated: the ids under "What did not work"' in completion
    assert not prompts["final"]


def test_a_completion_rounds_answer_that_leaves_the_choice_out_is_written_again_with_it():
    """Journeys round 2 (live 2026-10-11, journey 17 typed 0 of 3): the completion round answered "Redrew stoma 1 using
    SAM 2.1 tiny" from the same settled receipts and that answer stood (reuse_completed), the choice's scope never
    checked. One whose `stated` leaves a choice out is written again from the receipts, given the same c1."""
    brain, _, prompts = chose_and_redrew({"answer": "Redrew stoma 1 using SAM 2.1 tiny.", "stated": []},
                                         {"answer": "I redrew stoma 1 with SAM 2.1 tiny for this request only; outlines "
                                                    "still use EdgeTAM otherwise.", "stated": ["c1"]})
    result = run(brain.turn("hmm, try the other model"))
    assert result["answer"].startswith("I redrew stoma 1 with SAM 2.1 tiny for this request only") and result["calls"] == 3
    [final] = prompts["final"]
    line = "- c1: for this request, outlining uses SAM 2.1 tiny"
    assert line in final and line in prompts["completion"][0]  # one helper: the same id and words in both


@pytest.mark.parametrize("failure", [asyncio.TimeoutError(), RuntimeError("provider down")])
def test_a_completion_rounds_answer_stands_when_its_rewrite_fails(failure):
    """The lead's (b), journeys round 2 (live: 2 of 18 choice turns ended with no answer when the final call timed out
    at 15 s). Only after a completion round that answered these same settled receipts: the final call failing keeps
    its answer, the model's grounded words, logged as unchecked; everywhere else no prose stays the rule."""
    brain, agent, prompts = chose_and_redrew({"answer": "Redrew stoma 1 using SAM 2.1 tiny.", "stated": []}, failure)
    result = run(brain.turn("hmm, try the other model"))
    assert result["answer"] == "Redrew stoma 1 using SAM 2.1 tiny." and prompts["final"]
    [kept] = [r for r in agent.logged if r.get("kind") == "override" and r.get("rule") == "answer-recheck"]
    assert kept["unchecked"] == ["c1"] and kept["kept"] is True
    assert kept["reason"] == ("timeout" if isinstance(failure, asyncio.TimeoutError) else "provider-error")


def looked_and_one_step_failed(completion: dict, final=None):
    """Live 2026-10-11 16:36:22: hands' fast pass needed a look, the look planned two steps and one failed ("I can't find
    'o20'"), so the third call was the outcome round over the receipts; it answers `completion`, and an answer written
    from the receipts answers `final` (or raises it)."""
    from tests.test_sight import make_brain

    async def place_photo(ctx, args):
        return "the reference photo is beside the close-up"

    async def highlight_bundle(ctx, args):
        raise ToolFailed("I can't find 'o20' on screen", next="read its retained source, or name another outline")

    brain, _, agent = make_brain({"intent": "request", "needs_look": True, "answer": "", "actions": []},
                                 {"answer": "Placing the photo and highlighting the vascular bundle again.", "targets": [],
                                  "actions": [{"tool": "place_photo", "args": {}}, {"tool": "highlight_bundle", "args": {}}]})
    brain.registry.add(Tool("place_photo", "", schema({}), place_photo))
    brain.registry.add(Tool("highlight_bundle", "", schema({}), highlight_bundle))
    brain.hub.settings.completion, brain.hub.settings.answer_check = "every-plan", "every-request"
    initial_ask = agent.fast_model.ask
    prompts = {"completion": [], "final": []}

    async def provider(images, prompt, **kwargs):
        if "only your notes" in prompt and not prompts["completion"] and not prompts["final"]:
            return await initial_ask(images, prompt, **kwargs)
        if "Your reply (written before the actions ran)" in prompt:  # the answer written from the receipts
            prompts["final"].append(prompt)
            if isinstance(final, BaseException):
                raise final
            return SimpleNamespace(data={**final, "observation_scope": "none", "input_refs": []}, latency_ms=5)
        prompts["completion"].append(prompt)
        return SimpleNamespace(data={**completion, "actions": [], "observation_scope": "none", "input_refs": []},
                               latency_ms=5)

    agent.fast_model.ask = provider
    return brain, agent, prompts


def test_an_outcome_rounds_answer_that_leaves_out_a_failure_is_written_again_after_the_three_calls():
    """Live 2026-10-11 16:36:22 (T1): the outcome round, the third call, answered "re-highlighted the vascular bundle"
    though its highlight had failed, and that answer stood with no rewrite and no record: with the three calls spent,
    a completion round's answer was taken as it was. It stands only when it states every problem it was given; one that
    leaves a failure out is written again from the receipts on the answer's own last call."""
    brain, agent, prompts = looked_and_one_step_failed(
        {"answer": "I put the photo beside the close-up and re-highlighted the vascular bundle.", "stated": []},
        {"answer": "The photo is beside the close-up, but the vascular bundle's outline was gone, so it could not be "
                   "highlighted again.", "stated": ["p1"]})
    result = run(brain.turn("put the image next to the zoom and re-highlight the vascular bundle"))
    assert len(prompts["completion"]) == 1 and len(prompts["final"]) == 1
    assert "p1: highlight_bundle: I can't find 'o20' on screen" in prompts["final"][0]
    assert result["answer"].startswith("The photo is beside the close-up, but the vascular bundle's outline was gone")


def test_an_outcome_rounds_answer_that_states_every_failure_stands_after_the_three_calls():
    """No judge on every request: the outcome round's answer that states its problems stands, with no call after it."""
    brain, agent, prompts = looked_and_one_step_failed(
        {"answer": "The photo is beside the close-up; the vascular bundle's outline was gone, so I could not highlight "
                   "it again.", "stated": ["p1"]})
    result = run(brain.turn("put the image next to the zoom and re-highlight the vascular bundle"))
    assert not prompts["final"] and result["calls"] == 3
    assert result["answer"].startswith("The photo is beside the close-up; the vascular bundle's outline was gone")


def test_the_models_an_answer_names_that_no_receipt_names():
    """T4 (live 2026-10-11 16:28:55, "use the dino model to dissect"): no step used DINO, yet the answer said
    "dissecting the stoma with the DINO model". The app's own model names (names.py's keys of the model roles), matched
    exactly, are the models an answer names; one no receipt of the request names is a problem for the rewrite."""
    from orchestrator.brain.claims import unreceipted_models
    from orchestrator.brain.registry import ToolRun

    dissect = ToolRun("dissect", {"object": "o16"}, ok=True, summary="no distinct parts are visible in the stoma",
                      models=[{"role": "seen by", "name": "Gemini 3.5 Flash-Lite", "said": "Gemini 3.5 Flash-Lite (cloud)"}])
    assert unreceipted_models("I am dissecting the stoma with the DINO model now.", [dissect]) == ["DINO"]
    assert unreceipted_models("Gemini 3.5 Flash-Lite saw no distinct parts.", [dissect]) == []
    use = ToolRun("use", {}, ok=True, summary="for this request, outlining uses SAM 2.1 tiny, exactly that, with no fallback")
    assert unreceipted_models("Redrawn with SAM 2.1 tiny, for this request only.", [use]) == []
    # A family named alone is any of its members: SAM 3.1 drew it on this Mac.
    local = ToolRun("highlight", {}, ok=True, summary="outlined the stoma",
                    models=[{"role": "drawn by", "name": "SAM 3.1 (this Mac)", "said": "SAM 3.1 (this Mac)"}])
    assert unreceipted_models("I outlined it with SAM 3.1.", [local]) == []
    assert unreceipted_models("I outlined it with EdgeTAM and Cellpose.", [local]) == ["EdgeTAM", "Cellpose-SAM"]
    # Words that are options but no model ("off", "auto", "box") and a model the request's own answer used (`also`).
    assert unreceipted_models("Auto is off; the box is drawn.", []) == []
    assert unreceipted_models("Gemini 3.8 Flash looked at it.", [], also=["Gemini 3.8 Flash"]) == []


def slow_step_request(rewrites):
    """Live 2026-10-11 16:28:55, 16:42:22, 16:42:57: the fast pass's dissect was still running when the answer was
    written (dissect's inline wait passed), so the answer was written as progress ("still in progress ... will finish
    shortly") and the step finished while that call ran. `rewrites`: the answers written from the receipts, in order."""
    from tests.test_sight import make_brain

    async def slow_dissect(ctx, args):
        await asyncio.sleep(0.15)
        return "no distinct parts are visible in the stoma at this magnification; zooming in further would help"

    brain, _, agent = make_brain({"intent": "request", "needs_look": False, "answer": "Finding the parts of the stoma.",
                                  "actions": [{"tool": "slow_dissect", "args": {}}]}, {}, vision_delay=5)
    brain.registry.add(Tool("slow_dissect", "", schema({}), slow_dissect, inline_ms=20))
    brain.hub.settings.completion, brain.hub.settings.answer_check = "every-plan", "every-request"
    initial_ask = agent.fast_model.ask
    prompts = []

    async def provider(images, prompt, **kwargs):
        if "only your notes" in prompt and not prompts:
            return await initial_ask(images, prompt, **kwargs)
        prompts.append(prompt)
        reply = rewrites.pop(0)
        await asyncio.sleep(reply.pop("_takes", 0))
        return SimpleNamespace(data={**reply, "actions": [], "observation_scope": "none", "input_refs": []}, latency_ms=5)

    agent.fast_model.ask = provider
    return brain, agent, prompts


def test_an_answer_written_while_a_step_ran_is_written_again_once_it_finished():
    """T4: "still in progress" is never said of a step that has finished. The progress answer was asked for while the
    dissect ran; it finished during that call, so the answer is written again from the settled receipts (the call the
    late answer would have used), told of the model the progress answer named that no step used."""
    brain, agent, prompts = slow_step_request([
        {"answer": "I am dissecting the stoma with the DINO model now, but it is still in progress.", "_takes": 0.4},
        {"answer": "No distinct parts were visible in the stoma at this magnification; DINO was not used for it.",
         "stated": ["p1"]}])

    async def go():
        result = await brain.turn("use the dino model to dissect")
        await asyncio.sleep(0.3)
        return result

    result = run(go())
    assert result["answer"] == "No distinct parts were visible in the stoma at this magnification; DINO was not used for it."
    assert len(prompts) == 2 and "slow_dissect: still in progress" in prompts[0]
    assert "no distinct parts are visible" in prompts[1] and "still in progress" not in prompts[1].split("What did not work")[1]
    assert "p1: the answer names DINO, which no step of this request used" in prompts[1]
    assert not [r for r in agent.logged if r.get("kind") == "query-answer"]  # no second, late answer


def test_an_answer_still_right_about_a_running_step_stands():
    """A step still running when the progress answer comes back: that answer stands, and the late answer follows."""
    brain, agent, prompts = slow_step_request([
        {"answer": "I'm finding the parts of the stoma; it is still in progress."},
        {"answer": "No distinct parts were visible in the stoma at this magnification."}])

    async def go():
        result = await brain.turn("dissect it")
        await asyncio.sleep(0.4)
        return result

    result = run(go())
    assert result["answer"] == "I'm finding the parts of the stoma; it is still in progress."
    assert len(prompts) == 2  # the progress answer, then the late answer from the settled receipts


def test_use_for_something_that_is_no_model_choice_says_what_the_model_named_is_for():
    """T4 (live 2026-10-11 16:28:55, "use the dino model to dissect"): dissecting takes no model choice, and DINO is a
    picture model for grouping by look. A `use` for an operation with no choice is refused with where the model named
    is a choice, so the answer can say so, never that the work was done with it."""
    from orchestrator.brain.registry import ToolContext
    from orchestrator.ticket import issue, using

    brain, hub, _ = make([])

    async def use(args):
        with using(issue(hub, "turn")):
            return await brain.registry.tools["use"].handler(ToolContext("chat", 1, None, "use the dino model to dissect"),
                                                             args)

    for args, start in (({"operation": "dissect", "implementation": "dino", "by": "user"}, "there is no 'dissect' to choose"),
                        ({"operation": "motion", "implementation": "dino", "by": "user"}, "there is no choice to make for")):
        with pytest.raises(ToolFailed) as refused:
            run(use(args))
        assert str(refused.value).startswith(start) and "DINO is a choice for looks" in str(refused.value), str(refused.value)


@pytest.mark.parametrize("failure", ["unsupported-input", asyncio.TimeoutError()])
def test_a_request_whose_steps_ran_never_ends_with_no_words(failure):
    """T5 (live 2026-10-11 16:49:38, "can you highlight my eye"): the eye was outlined and shown (the outline check:
    fits), but the answer written from the receipts declared an input it was not given and was dropped
    (unsupported-input), so the request ended with no words. With nothing to tell beyond what it said (no problem, no
    choice, nothing still running, no limitation), the request's own answer stands when that call can't be had, logged
    as unchecked with the reason; with a problem to tell, no prose stays the rule."""
    from tests.test_sight import make_brain

    brain, hub, agent = make_brain({"intent": "request", "needs_look": True, "answer": "", "actions": []},
                                   {"answer": "Here is your eye on the screen!", "actions": [],
                                    "targets": [{"box_2d": [400, 400, 500, 520], "label": "eye", "confidence": 0.9}]})
    brain.hub.settings.answer_check = "every-request"
    initial_ask = agent.fast_model.ask
    finals = []

    async def provider(images, prompt, **kwargs):
        if "only your notes" in prompt and not finals:
            return await initial_ask(images, prompt, **kwargs)
        finals.append(prompt)
        if isinstance(failure, BaseException):
            raise failure
        # A reply that says it saw camera pixels it was not given (the scope check drops it).
        return SimpleNamespace(data={"answer": "Your eye is outlined.", "observation_scope": "specimen-camera",
                                     "input_refs": ["camera"]}, latency_ms=5)

    agent.fast_model.ask = provider
    result = run(brain.turn("can you highlight my eye"))
    assert finals and result["objectIds"]  # the eye was outlined, and its answer was written again from the receipts
    assert result["answer"] == "Here is your eye on the screen!"
    [kept] = [r for r in agent.logged if r.get("kind") == "override" and r.get("rule") == "answer-recheck"]
    assert kept["kept"] is True and kept["unchecked"] == []
    assert kept["reason"] == ("unsupported-input" if failure == "unsupported-input" else "timeout")
