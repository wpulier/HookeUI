"""Synthetic contention, real reservation ownership; no resource-performance claim."""
import threading
import pytest
from tests.waiting import HANG_S
from scientific_compute.reservation import heavy_work


def test_waiting_admission_acquires_same_slot_after_owner_releases(tmp_path,monkeypatch):
    slot=tmp_path/'slot';monkeypatch.setenv('MICROMIND_HEAVY_SLOT',str(slot))
    waiting=threading.Event();entered=threading.Event();states=[];errors=[]
    def observe(value):
        states.append(value)
        if value:waiting.set()
    def waiter():
        try:
            with heavy_work('second',wait_seconds=HANG_S,waiting=observe):entered.set()
        except BaseException as error:errors.append(error)
    with heavy_work('first'):
        original=(slot/'owner.json').read_bytes()
        thread=threading.Thread(target=waiter);thread.start()
        assert waiting.wait(HANG_S) and not entered.is_set()
        assert (slot/'owner.json').read_bytes()==original
    thread.join(HANG_S)
    assert not thread.is_alive() and not errors and entered.is_set()
    assert states==[True,False] and not slot.exists()


def test_cancel_waiting_never_reclaims_another_owner(tmp_path,monkeypatch):
    slot=tmp_path/'slot';monkeypatch.setenv('MICROMIND_HEAVY_SLOT',str(slot))
    stopped=False
    def check():
        if stopped:raise ValueError('stopped')
    def waiting(value):
        nonlocal stopped
        stopped=True
    with heavy_work('owner'):
        original=(slot/'owner.json').read_bytes()
        with pytest.raises(ValueError,match='stopped'):
            with heavy_work('waiting',wait_seconds=HANG_S,check=check,waiting=waiting):pytest.fail('ran after stop')
        assert (slot/'owner.json').read_bytes()==original
