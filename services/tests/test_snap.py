from orchestrator.candidates import Candidate
from orchestrator.fov import FieldOfView
from orchestrator.snap import pick_spot

FOV = FieldOfView(cx=640, cy=360, radius=300, confidence=1.0)


def cand(i, x, y, radius=5.0, contrast=-20.0):
    return Candidate(i, x, y, radius, contrast, 1.0, 1)


SPOTS = [
    cand(1, 640, 120, 4, -30),   # small dark, top
    cand(2, 640, 600, 4, -30),   # small dark, bottom
    cand(3, 420, 360, 12, 25),   # big bright, left
    cand(4, 860, 150, 4, 25),    # small bright, top right
]


def test_picks_by_position_size_and_tone():
    assert pick_spot("what's the little dark dot near the top?", SPOTS, FOV).id == 1
    assert pick_spot("the tiny black speck at the bottom", SPOTS, FOV).id == 2
    assert pick_spot("that small bright spot top right", SPOTS, FOV).id == 4


def test_no_guess_without_a_small_thing_or_any_description():
    assert pick_spot("what's the oval on the left?", SPOTS, FOV) is None  # shapes wait for the model
    assert pick_spot("what's that dot?", SPOTS, FOV) is None  # nothing says which dot
    assert pick_spot("what am I looking at?", SPOTS, FOV) is None
    assert pick_spot("the dark dot at the top", [], FOV) is None
