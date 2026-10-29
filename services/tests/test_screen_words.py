"""What a request is told when the screen report cannot confirm something (docs/architecture/on-screen.md).

The voice log of 2026-10-28 ended: "I can't see your screen right now ... If things look stuck, try refreshing the page",
then "Quit and reopen the app for a clean refresh." A look past its capture deadline, and failures whose next step was
"check the studio" or "open the studio", gave the model nothing true to say but that. The studio is the app's own window:
these failures say only what did not happen, that saved work is kept, and that asking again reads the next report."""
import asyncio

from orchestrator.app import App
from orchestrator.brain.registry import ToolContext
from orchestrator.commands import COMMANDS
from orchestrator.paths import Paths
from tests.test_installed_workspace_capture import failure, scenario

NEVER = ("reopen", "quit", "restart", "refresh", "reload", "can't see", "cannot see", "can’t see", "open the studio",
         "check the studio", "check the originating studio")


def _honest(words: str) -> None:
    said = words.lower()
    for phrase in NEVER:
        assert phrase not in said, f"{phrase!r} in {words!r}"


def test_a_look_refused_by_the_report_path_claims_nothing_about_the_users_screen(tmp_path):
    async def work(i):
        _request, _pin, _ctx, run, look = await i.start()
        await COMMANDS['screen-image'](i.app, i.studio, failure(look, 'share-required'))
        for _ in range(8):
            await asyncio.sleep(0)
        assert run.task.done() and run.ok is False
        _honest(run.summary)
        assert 'no picture of the workspace was taken' in run.summary or 'not looked at' in run.summary
        assert 'nothing about what the user sees' in run.summary
    scenario(tmp_path, work)


def test_no_studio_reporting_says_what_did_not_happen_never_to_open_or_restart_anything(tmp_path):
    async def go():
        app = App.build('http://127.0.0.1:1', Paths.under(tmp_path), use_gpu=False)
        ctx = ToolContext('chat', 1, None, 'what is on my screen?', asked=app.day.here())
        for tool, args in (('look_at_screen', {}), ('present', {'what': 'magnifier', 'do': 'close'})):
            run = app.brain.registry.start(tool, args, ctx)
            await run.task
            assert run.ok is False, (tool, run.summary)
            _honest(run.summary)
    asyncio.run(go())


def test_no_failure_on_the_report_path_tells_the_user_to_open_refresh_or_restart_the_app():
    """The wording itself, wherever a report-path failure is raised (present, the reader, the table window)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / 'orchestrator'
    for name in ('brain/toolbox/present.py', 'data_reader.py', 'table_window.py', 'brain/toolbox/tables.py',
                 'workspace_tabs.py', 'screen.py'):
        text = (root / name).read_text()
        for phrase in ('open the studio', 'check the studio', 'check the originating studio', 'reopen the studio'):
            assert phrase not in text, (name, phrase)
