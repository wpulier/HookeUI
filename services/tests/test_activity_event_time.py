"""Local journal/restart evidence; no browser or model quality claim."""
from orchestrator.day import DayStore
from orchestrator.dialogue import Dialogue


def test_event_time_survives_restart_without_retiming_history(tmp_path):
    now = 1791633600.125
    store = DayStore(root=tmp_path, clock=lambda: now)
    published = []
    dialogue = Dialogue(store, published.append)
    line = dialogue.show({"type": "chat", "role": "assistant", "text": "Saved"})
    assert line["at"] == now
    store.add_line({"type": "chat", "role": "assistant", "text": "Legacy, no time"})
    again = DayStore(root=tmp_path, clock=lambda: now + 30)
    history = Dialogue(again, lambda _: None).history()["lines"]
    assert history[0]["at"] == now
    assert "at" not in history[1]


def test_export_import_preserves_recorded_numeric_time(tmp_path):
    now = 1791633600.125
    store = DayStore(root=tmp_path / "source", clock=lambda: now)
    dialogue = Dialogue(store, lambda _: None, log=tmp_path / "conversation.jsonl")
    dialogue.show({"type": "chat-action", "turnId": 1, "tool": "zoom", "ok": True,
                   "summary": "Displayed", "ms": 12, "at": now - 2})
    target = DayStore(root=tmp_path / "destination", clock=lambda: now)
    imported = Dialogue(target, lambda _: None, log=tmp_path / "conversation.jsonl")
    assert imported.import_log(imported.today_in_log()) == 1
    assert imported.history()["lines"][0]["at"] == now - 2
