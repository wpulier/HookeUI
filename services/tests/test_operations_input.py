"""The admitted input check (operations.same_input): exact for saved resources, one input for the camera feed."""
from orchestrator.operations import same_input


def test_the_camera_feed_is_one_input_whatever_plays_into_it():
    assert same_input({"source": None}, {"source": "live"}), "nothing named on stage is the live feed"
    assert same_input({"source": "a7"}, {"source": "live"}), "a recording played into the feed is the feed"


def test_a_saved_resource_must_be_the_same_one():
    one, two = {"resource": {"id": "d1", "day": "x"}}, {"resource": {"id": "d2", "day": "x"}}
    assert same_input(one, one) and not same_input(one, two)
    assert not same_input(one, {"source": "live"}) and not same_input({"source": "live"}, one)
