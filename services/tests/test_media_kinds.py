"""Kinds of media are said and shown as what they are (the partner's §1e; kind (f) of the end of the effort): on
2026-10-07 the result viewer opened on an AI illustration titled "Real close-up of a stoma", with the real photo
the researcher had found a tab away under "What it found". Each item now says what it is from what was recorded
when it was made (never its caption), a real photo found is an item of its own, and what the researcher was asked
to make opens first; an illustration it made on its own never opens ahead of the written result."""
from __future__ import annotations

from types import SimpleNamespace

from orchestrator import media
from orchestrator.presenter import Presenter, first_shown, items
from orchestrator.research.manager import Session

ILLUSTRATION = {"kind": "image", "path": "out/illustration.png", "caption": "Generated illustration (gpt-image-2), not the specimen",
                "generated": True, "id": "o1", "version": 1}
LABELLED = {"kind": "image", "path": "out/annotated.png", "caption": "Labels by gpt-image-2 on the microscope photo: guard cell",
            "generated": False, "id": "o2", "version": 1}
LESSON = {"kind": "html", "path": "out/lesson.html", "caption": "Interactive lesson: Stomata", "generated": True, "id": "o3",
          "version": 1}
VIDEO = {"kind": "video", "path": "out/video.mp4", "caption": "Generated video", "generated": True, "id": "o4", "version": 1}
REF = {"path": "sources/abc.jpg", "page": "https://commons.wikimedia.org/wiki/File:Stoma.jpg", "title": "Stoma in a leaf",
       "credit": "A. Botanist, CC BY-SA 4.0, commons.wikimedia.org"}


def researcher(deliverable="research", outputs=(), refs=(), result=True):
    s = Session("r3", 3, "a real close-up of a stoma", deliverable, "quick", "fake", "research", "stoma", None, {}, "2026-10-07")
    s.outputs = [dict(o) for o in outputs]
    s.result = {"title": "Real close-up of a stoma", "summary": "A stoma is a pore.", "refs": [dict(r) for r in refs]} \
        if result else None
    s.status = "done"
    return s


def test_each_kind_is_said_from_what_was_recorded_never_from_the_caption():
    assert media.of_output(ILLUSTRATION) == media.GENERATED and media.of_output(LABELLED) == media.LABELLED
    assert media.of_output(VIDEO) == media.GENERATED and media.of_output(LESSON) == media.LESSON
    # A picture with no record of how it was made is never called real, whatever its caption says.
    assert media.of_output({"kind": "image", "path": "x.png", "caption": "A real photo of the slide"}) == media.UNKNOWN
    assert media.label(media.GENERATED, "video") == "AI video" and media.noun(media.GENERATED) == "AI illustration"
    assert media.line(media.GENERATED) == "AI illustration, not microscope footage"  # as burnt into the file
    # A capability's picture is computed from this slide's picture (a Fiji filter, a model enlarging it): derived, never
    # an AI illustration (live 2026-10-15: a Gaussian blur of the view read "AI illustration, not microscope footage").
    assert media.label(media.DERIVED) == "Processed picture" and "not the camera's own pixels" in media.line(media.DERIVED)
    assert media.line(media.REFERENCE, credit="C") == "A reference photo from the web, not this slide · C"


def test_items_say_their_kind_and_a_found_photo_is_its_own_item():
    s = researcher("image", [ILLUSTRATION, LABELLED], [REF])
    listed = items(s)
    assert [(it["kind"], it["label"], it["media"]) for it in listed] == [
        ("image", "Labelled photo", media.LABELLED), ("image", "AI illustration", media.GENERATED),
        ("reference", "Reference photo", media.REFERENCE), ("summary", "What it found", media.WRITTEN)]
    assert listed[2]["ref"] == 0 and listed[2]["line"].startswith("A reference photo from the web, not this slide")
    assert listed[1]["line"] == "AI illustration, not microscope footage"
    # The numbering of what it made is unchanged (read_result and show_result count the outputs first).
    assert [it.get("id") for it in listed[:2]] == ["o2", "o1"]
    two = items(researcher("research", [], [REF, {**REF, "path": "sources/def.jpg"}]))
    assert [it["label"] for it in two if it["kind"] == "reference"] == ["Reference photo 1", "Reference photo 2"]
    # The studio gets the same, on the session's wire.
    wire = s.wire()["items"]
    assert wire[1]["media"] == media.GENERATED and wire[2] == {"kind": "reference", "label": "Reference photo", "id": None,
                                                               "media": media.REFERENCE, "line": listed[2]["line"], "ref": 0,
                                                               "work":{"researcherId":s.rid, "resource":{"day":s.day,"id":"ref-0","version":None}}}


def test_what_it_was_asked_to_make_opens_first():
    # The 2026-10-07 case: an illustration was asked for (deliverable "image"): it opens, and says it is one.
    asked_image = researcher("image", [ILLUSTRATION], [REF])
    assert items(asked_image)[first_shown(asked_image)]["label"] == "AI illustration"
    labelled = researcher("annotated_image", [ILLUSTRATION, LABELLED])
    assert items(labelled)[first_shown(labelled)]["media"] == media.LABELLED
    lesson = researcher("lesson", [ILLUSTRATION, LESSON])
    assert items(lesson)[first_shown(lesson)]["kind"] == "html"
    video = researcher("video", [ILLUSTRATION, VIDEO])
    assert items(video)[first_shown(video)]["kind"] == "video"
    # Research was asked for: the written result (it shows the photos it found), never an illustration it made on
    # its own ahead of it.
    research = researcher("research", [ILLUSTRATION], [REF])
    assert items(research)[first_shown(research)]["kind"] == "summary"
    assert first_shown(researcher("research", [ILLUSTRATION], result=False)) == 0  # nothing else to open


def test_the_viewer_opens_on_it_and_an_asked_index_is_kept():
    s = researcher("research", [ILLUSTRATION], [REF])
    hub = SimpleNamespace(sent=[], broadcast=lambda m: hub.sent.append(m))
    presenter = Presenter(hub, SimpleNamespace(get=lambda rid: s if rid == "r3" else None))
    assert presenter.show("r3").startswith("Researcher 3's written result")
    assert items(s)[presenter.index]["kind"] == "summary"
    assert presenter.show("r3", 0) == "Researcher 3's AI illustration (Generated illustration (gpt-image-2), not the specimen)"


def test_a_researchers_file_says_its_kind_from_its_record():
    s = researcher("image", [ILLUSTRATION, LABELLED], [REF])
    research = SimpleNamespace(get=lambda rid: s if rid == "r3" else None)
    assert media.of_research_file(research, "/research/2026-10-07/r3/sources/abc.jpg") == (media.REFERENCE, REF["credit"])
    assert media.of_research_file(research, "/research/2026-10-07/r3/out/illustration.png?v=2") == (media.GENERATED, "")
    assert media.of_research_file(research, "/research/2026-10-07/r3/out/annotated.png") == (media.LABELLED, "")
    assert media.of_research_file(research, "/research/2026-10-07/r3/elsewhere.png") == (media.UNKNOWN, "")
    assert media.of_research_file(research, "/research/2026-10-07/r9/out/illustration.png") == (media.UNKNOWN, "")
    # IDs and filenames can repeat on another day; that file is not this session's evidence.
    assert media.of_research_file(research, "/research/2026-10-06/r3/sources/abc.jpg") == (media.UNKNOWN, "")
    assert media.of_research_file(research, "/research/2026-10-06/r3/out/annotated.png") == (media.UNKNOWN, "")
    assert media.of_research_file(research, "/api/picture/abc") == (media.UNKNOWN, "")


def test_show_in_magnifier_puts_the_real_photo_first_unless_an_illustration_was_asked(tmp_path):
    import asyncio

    from orchestrator.brain.registry import ToolContext
    from orchestrator.brain.toolbox import Services, build
    from orchestrator.magnifier import Magnifier
    from orchestrator.research import ResearchManager
    from orchestrator.research.desk import Desk
    from tests.test_magnifier import FakeHub

    hub = FakeHub([0])
    m = Magnifier(hub)
    manager = ResearchManager(hub, root=tmp_path, today=lambda: "2026-10-07")
    registry = build(Services(hub=hub, agent=None, magnifier=m, research=manager, desk=Desk(manager)))

    def show(s, picture=None):
        manager.sessions = {s.rid: s}

        async def go():
            r = registry.start("show_in_magnifier", {"researcher": "Researcher 3", **({"picture": picture} if picture else {})},
                               ToolContext("chat", 1, 0))
            if r.task is not None:
                await r.task
            return r

        return asyncio.run(go())

    r = show(researcher("research", [ILLUSTRATION], [REF]))
    assert r.ok, r.error
    assert m.sources[m.index]["media"] == media.REFERENCE and "reference photo" in r.summary
    assert m.sources[m.index]["label"].startswith("Researcher 3's reference photo: Stoma in a leaf")
    r = show(researcher("research", [ILLUSTRATION], [REF]), picture=2)
    assert m.sources[m.index]["media"] == media.GENERATED and "AI illustration, not microscope footage" in r.summary
    show(researcher("image", [ILLUSTRATION], [REF]))
    assert m.sources[m.index]["media"] == media.GENERATED  # asked for: first


def test_the_ready_line_says_what_opened_as_what_it_is():
    from orchestrator.media import noun as media_noun

    assert media_noun(media.GENERATED, "image") == "AI illustration" and media_noun(media.REFERENCE, "reference") == "reference photo"
    assert media_noun(media.GENERATED, "video") == "AI video" and media_noun(None, "image") == "picture"
