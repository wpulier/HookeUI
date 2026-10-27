"""Real retained evidence, synthetic Fiji transport: artifact content must not collapse to the source image."""
import asyncio
import copy

import pytest

from tests.test_fiji_particle_journey import journey  # noqa: F401
from tests.test_particle_corrections import analyzed


def test_mask_artifact_returns_retained_geometry_and_rejects_another_revision(journey):
    from orchestrator.artifact_overlay import read_overlay, OverlayRefused
    built, aid, _ = journey
    async def go():
        result = await analyzed(built, aid)
        data = await read_overlay(built, result.id, result.session, result.revision)
        assert data['members'] == result.wire()['members']
        assert len(data['members']) == 3
        assert all(member['polygons'] for member in data['members'])
        assert (data['width'], data['height']) == (96, 96)
        assert data['session'] == result.session and data['revision'] == result.revision
        before = copy.deepcopy(result.columns)
        with pytest.raises(OverlayRefused):
            await read_overlay(built, result.id, 'wrong-session', result.revision)
        with pytest.raises(OverlayRefused):
            await read_overlay(built, result.id, result.session, result.revision + 1)
        assert result.columns == before
        (result.path / 'masks.npz').write_bytes(b'broken')
        with pytest.raises(OverlayRefused):
            await read_overlay(built, result.id, result.session, result.revision)
    asyncio.run(go())
