"""A researcher's work edited without the network (research_worker/work.py): lesson pages kept
self-contained, revise edits held to what the change names, plain label changes made with no
model, and the text of a result. Called directly, with no researcher running."""
import base64
import json

import cv2
import numpy as np

from research_worker.work import (bounded_json, clean_lesson, draw_labels, edited_lesson, inline_photo, json_reply,
                                  named_steps, offline_lesson, slug, trim)

OLD = {"title": "Guard cells", "key_ideas": ["Two guard cells surround each pore"],
       "steps": [{"id": "q1", "prompt": "One?", "answer": "1", "hint": "h1"},
                 {"id": "q2", "prompt": "Hard two?", "answer": "2", "hint": "h2"},
                 {"id": "q3", "prompt": "Three?", "answer": "3", "hint": "h3"}]}


def jpeg(width: int, height: int) -> bytes:
    image = np.full((height, width, 3), 90, np.uint8)
    cv2.circle(image, (width // 2, height // 2), min(width, height) // 4, (40, 160, 60), -1)
    return cv2.imencode(".jpg", image)[1].tobytes()


def test_a_lesson_page_loads_nothing_from_elsewhere():
    page = clean_lesson('```html\n<!DOCTYPE html><html><head><script src="https://cdn.example/a.js"></script>'
                        '<link rel=stylesheet href="//fonts.example/c.css"><script>let a = 1</script></head>'
                        '<body><iframe src="http://example.com/"></iframe><p>Guard cells</p></body></html>\n```')
    assert page.startswith("<!DOCTYPE html>") and "```" not in page
    assert "https://" not in page and "//fonts" not in page and "<iframe" not in page
    assert "<script>let a = 1</script>" in page and "<p>Guard cells</p>" in page  # its own script and text stay
    assert clean_lesson("Here you go:\n<html><body>x</body></html>") == "<html><body>x</body></html>"
    assert clean_lesson("Sorry, I cannot write that.") == ""


def test_the_photo_goes_inside_the_page():
    html = '<img src="../inputs/crop.jpg"><img src="../inputs/frame.jpg">'
    assert inline_photo(html, None) == html
    page = inline_photo(html, jpeg(1600, 1200))
    assert "../inputs/" not in page and page.count('src="data:image/jpeg;base64,') == 2
    data = base64.b64decode(page.split("base64,", 1)[1].split('"', 1)[0])
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert image.shape[:2] == (600, 800)  # at most 800 px, never stretched


def test_bounded_json_stays_valid_under_the_limit():
    small = {"title": "short", "steps": [{"prompt": "a"}]}
    assert bounded_json(small, 2000) == json.dumps(small)
    big = {"title": "t", "steps": [{"prompt": "x" * 5000, "answer": "y" * 3000}, {"prompt": "é" * 4000}]}
    text = bounded_json(big, 2000)
    assert len(text) <= 2000
    value = json.loads(text)  # cut inside the strings, not through the JSON
    assert value["title"] == "t" and len(value["steps"]) == 2 and value["steps"][0]["prompt"].endswith("…")


def test_the_json_in_a_reply():
    assert json_reply('Sure, here it is: {"labels": [{"name": "pore"}]} Hope that helps.') == {"labels": [{"name": "pore"}]}
    assert json_reply("[1, 2]") == {} and json_reply("{not json}") == {} and json_reply(None) == {}


def test_steps_named_in_a_change_and_kept_ids():
    assert named_steps("make question 2 easier", 3) == ["q2"]
    assert named_steps("questions 1 and 3 are too long", 3) == ["q1", "q3"]
    assert named_steps("the last question is wrong", 4) == ["q4"] and named_steps("the second question", 3) == ["q2"]
    assert named_steps("make it shorter", 3) == [] and named_steps("question 9", 3) == []
    old = {"title": "T", "key_ideas": ["k"], "steps": [{"id": "q1", "prompt": "a", "answer": "", "hint": ""},
                                                        {"id": "q2", "prompt": "b", "answer": "", "hint": ""}]}
    content, changed = edited_lesson(old, {"title": "New title", "steps": [{"prompt": "A!"}, {"prompt": "B!"}]}, "change question 2")
    assert [s["prompt"] for s in content["steps"]] == ["a", "B!"] and content["title"] == "T" and changed == ["q2"]
    whole, changed = edited_lesson(old, {"title": "Shorter", "steps": [{"prompt": "only one"}]}, "make it one question")
    assert whole["title"] == "Shorter" and [s["id"] for s in whole["steps"]] == ["q1"] and changed == ["q1", "q2"]


def test_more_ways_to_name_a_step():
    assert named_steps("the second one is confusing", 3) == ["q2"]
    assert named_steps("q3 needs a hint", 3) == ["q3"] and named_steps("step #1", 3) == ["q1"]
    assert named_steps("questions 1, 2 and 3", 3) == ["q1", "q2", "q3"]
    assert named_steps("q2, I mean question 2", 3) == ["q2"]  # once each


def test_an_edit_that_names_a_step_copies_every_other_step():
    # The model rewrote everything; only question 2 was asked for, so only it changes.
    new = {"title": "Stomata!", "key_ideas": ["Something else"],
           "steps": [{"id": "q1", "prompt": "Rewritten one?", "answer": "x", "hint": "y"},
                     {"id": "q2", "prompt": "Easier two?", "answer": "two"},
                     {"id": "q3", "prompt": "Rewritten three?", "answer": "z", "hint": "w"}]}
    content, changed = edited_lesson(OLD, new, "make question 2 easier")
    assert changed == ["q2"]
    assert content["title"] == "Guard cells" and content["key_ideas"] == OLD["key_ideas"]
    assert content["steps"][0] == OLD["steps"][0] and content["steps"][2] == OLD["steps"][2]
    assert content["steps"][1] == {"id": "q2", "prompt": "Easier two?", "answer": "two", "hint": "h2"}  # its hint kept
    same, changed = edited_lesson(OLD, {"steps": [dict(s) for s in OLD["steps"]]}, "make question 2 easier")
    assert changed == [] and same["steps"] == OLD["steps"]


def test_labels_are_drawn_only_when_each_has_a_box():
    photo = jpeg(320, 240)
    assert draw_labels(photo, [{"name": "pore", "box": None}]) is None and draw_labels(photo, []) is None
    png = draw_labels(photo, [{"name": "pore", "box": [0.4, 0.4, 0.6, 0.6]}, {"name": "cell", "box": [0.1, 0.7, 0.2, 0.8]}])
    image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    assert image.shape[:2] == (240, 320)  # the photo's own size


def test_the_offline_lesson_has_the_shape_of_a_real_one():
    content = {"title": "Cells & <walls>", "key_ideas": ["Walls are rigid"],
               "steps": [{"id": "q1", "prompt": "What is <b>?", "answer": "A wall"}, {"id": "q2", "prompt": "Two?", "answer": "2"}]}
    page = offline_lesson(content)
    assert page.startswith("<!doctype html>") and clean_lesson(page) == page
    assert '<section id="q1"><h3>Question 1</h3>' in page and '<section id="q2"><h3>Question 2</h3>' in page
    assert "<title>Cells &amp; &lt;walls&gt;</title>" in page and "What is &lt;b&gt;?" in page and "<b>" not in page
    assert '<p class="answer">A wall</p>' in page


def test_slug_and_trim():
    assert slug("Lesson: Guard cells!") == "lesson-guard-cells" and slug("!!!") == "output" and len(slug("a" * 80)) == 32
    assert trim("Guard cells citeturn1view0 open the pore , by day.", 100) == "Guard cells open the pore, by day."
    assert trim("one two three four five six", 12) == "one two…"
    assert trim(None, 10) == "" and trim("short", 10) == "short"
