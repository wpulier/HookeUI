"""A launched agent, as Hooke's real door and Study storage see it (ADR 0029), with no agent program: the test does what
the launched agent does, through the door, with the token the conductor gives it.

It claims its assignment, keeps a report in the Project, returns the assignment and publishes a claim; the Study then
says the report is back and the claim is the agent's own, awaiting the researcher's review. Ending the launch revokes
its connection and its Project access.
"""
from fastapi.testclient import TestClient

from orchestrator.door.projects import access
from tests.test_agent_door import BASE, bearer, window_key  # noqa: F401  (its key, autouse)
from tests.test_agent_projects import run
from tests.test_resources import app_at

LAUNCH = "ag_" + "1" * 16


def test_a_launched_agent_works_through_the_door_and_its_report_awaits_review(tmp_path):
    web = app_at(tmp_path)
    built = web.state.app
    side = built.conductor.side
    with TestClient(web, base_url=BASE) as client:
        sid, _ = side.project(None)
        cid, token = side.connect("Codex", "codex", LAUNCH, sid)
        connection = built.agent_connections.get(cid)
        assert connection.launch == LAUNCH and connection.label == "Codex (started by Hooke)"
        actor = "agent:" + cid
        assert access(built).role(sid, actor) == "contribute"
        study, assignment = side.assign(sid, LAUNCH, "Codex: count the stomata", "count the stomata", None)
        h = bearer(token)
        assert client.get("/api/agent/v1/status", headers=h).status_code == 200

        base = f"/api/agent/v1/studies/{study}/coordination"
        before = client.get(base, params={"session": sid}, headers=h).json()
        row = next(a for a in before["assignments"] if a["id"] == assignment)
        assert row["proposedBy"] == {"by": "studio", "byLabel": "You"}  # the researcher asked for it in Hooke
        claimed = client.post(base + "/claim", json={"session": sid, "assignment": assignment, "harness": "Codex",
                                                     "model": "a model"}, headers=h)
        assert claimed.status_code == 200, claimed.text

        report = tmp_path / "report.md"
        report.write_text("Counted 42 stomata in the open frame.\n")
        artifact = run(client, h, "project.import", {"file": str(report)}, "report-1", sid)["outcome"]["artifact"]
        returned = client.post(base + "/complete", json={
            "session": sid, "assignment": assignment, "statement": "Counted 42 stomata.", "evidence": [artifact],
            "limitations": "One frame only."}, headers=h)
        assert returned.status_code == 200, returned.text
        published = client.post(f"/api/agent/v1/studies/{study}/claims", json={
            "session": sid, "requestKey": "claim-1", "statement": "The open frame shows 42 stomata.",
            "evidence": [{"artifact": artifact, "relation": "supports"}], "dependsOn": [],
            "limitations": "One frame only."}, headers=h)
        assert published.status_code in (200, 201), published.text

        assert side.observe(sid, study, assignment, cid) == {"returned": True, "claimed": True, "claims": 1}
        after = client.get(base, params={"session": sid}, headers=h).json()
        claim = next(c for c in after["claims"] if c["request"] == "claim-1")
        assert claim["by"] == actor and claim["byLabel"] == "Codex (started by Hooke)"  # the agent's, not the user's

        side.end(sid, cid)
        refused = client.get("/api/agent/v1/status", headers=h)
        assert refused.status_code == 401 and refused.json()["code"] == "disconnected"
        assert access(built).role(sid, actor) is None
        side.end(sid, cid)  # again, harmlessly


def test_only_the_conductor_makes_a_launch_connection(tmp_path):
    from orchestrator.door.connections import Connections

    connections = Connections(tmp_path)
    import pytest
    with pytest.raises(ValueError):
        connections.issue("Codex", "codex", launch="not-a-launch")
    c, token = connections.issue("Grok Build (started by Hooke)", "grok", launch=LAUNCH)
    assert c.kind == "other" and token.startswith("hk_local_")  # a kind the door doesn't know is "other"
    assert "launch" not in c.wire()  # the shared connection contract is unchanged
    again = Connections(tmp_path)
    assert again.get(c.id).launch == LAUNCH and again.authenticate(f"Bearer {token}").id == c.id
    with pytest.raises(ValueError):
        access_of = __import__("orchestrator.door.projects", fromlist=["ProjectAccess"]).ProjectAccess(tmp_path)
        access_of.grant("2026-11-03", c.actor, "contribute", authority="a-route")
