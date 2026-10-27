"""The release must reject the decoder shipped without SRT in the first native candidate."""
import importlib.util
from pathlib import Path
import pytest

path = Path(__file__).resolve().parents[2] / 'packaging/macos/verify_receiver.py'
spec = importlib.util.spec_from_file_location('verify_receiver', path)
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)

@pytest.mark.parametrize('protocols', [
    'Supported file protocols:\nInput:\n  file\nOutput:\n  file\n',
    'Input:\n  file\nOutput:\n  srt\n',
    'Input:\n  srt\nOutput:\n  file\n',
])
def test_incomplete_receiver_refuses_release(protocols):
    with pytest.raises(ValueError, match='SRT support'):
        receiver.require_protocols(protocols)


def test_both_stream_directions_are_supported():
    receiver.require_protocols('Supported file protocols:\nInput:\n  file\n  srt\nOutput:\n  file\n  srt\n')
