"""Who is told which models made an outline (Slice 2, 2026-10-11; brain/context.py, known.chain).

Live 2026-10-11: asked "which model did you use?", the answer invented "our built-in vision model". The voice's rows
always carry the chain; the brain's own rows carry it only with option ROW_PROVENANCE "on" (off by default: context
earns its place, docs/design/2026-10-07-session-knowledge.md); the fast pass's rows never do. The brain can always read
it with resources(id=...)."""
from orchestrator.brain import context
from orchestrator.scene import SceneObject
from tests.test_brain import make

CHAIN = "found by Gemini 3.8 Flash, drawn by SAM 2.1 tiny (on this Mac) as chosen for that request"


def world(row_provenance: str):
    brain, hub, agent = make([])
    hub.settings.row_provenance = row_provenance
    hub.scene.add(SceneObject(id=hub.scene.new_id(), kind="highlight", polygons=[[[600, 300], [640, 300], [620, 340]]],
                              anchor_frame=7, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="ai:gemini-3.8-flash",
                              label="stoma", status="likely",
                              made_by={"operation": "outline", "implementation": "sam2.1-tiny", "policy": "raw",
                                       "origin": "user-chosen"}))
    return brain


def told(brain) -> dict[str, str]:
    brains = brain.moment(pushes=True)  # what the brain's own prompt is made from
    fast = brain.moment()  # what the fast pass and the voice are told
    return {"brain": context.scene_table(brains)[0], "fast": "\n".join(str(v) for v in context.fast_fields(fast).values()),
            "voice": context.view_note(fast)}


def test_the_brains_rows_carry_which_models_only_with_the_option():
    off, on = told(world("off")), told(world("on"))
    assert CHAIN not in off["brain"] and f"made: {CHAIN}" in on["brain"]
    assert CHAIN not in off["fast"] and CHAIN not in on["fast"]  # the fast pass's rows never
    assert CHAIN in off["voice"] and CHAIN in on["voice"]  # the voice's always
    assert "yours" in off["brain"]  # "yours" is the model's own outline: the rows address the model (unchanged)
