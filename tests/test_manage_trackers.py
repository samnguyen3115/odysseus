import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import JobApplication, TrackedPackage
from src.action_intents import classify_tool_intent
from src.agent_loop import _classify_agent_request
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.tool_types import TOOL_TAGS
from src.tools import trackers


def test_tracker_tool_is_owner_scoped_and_updates_only_selected_record(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    JobApplication.__table__.create(engine)
    TrackedPackage.__table__.create(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(trackers, "SessionLocal", sessions)

    with sessions() as db:
        db.add_all([
            JobApplication(id="alice-job", owner="alice", company="Alpha", title="Engineer", job_url="https://example.com/a", status="saved", raw_metadata='{"keep":true}'),
            JobApplication(id="bob-job", owner="bob", company="Beta", title="Manager", job_url="https://example.com/b", status="saved", raw_metadata="{}"),
            TrackedPackage(id="alice-package", owner="alice", tracking_number="ABC123456", label="Keyboard", status="in_transit"),
            TrackedPackage(id="bob-package", owner="bob", tracking_number="XYZ987654", label="Private", status="delivered"),
        ])
        db.commit()

    def call(args, owner="alice"):
        return asyncio.run(trackers.do_manage_trackers(json.dumps(args), owner=owner))

    listed = call({"dataset": "all", "action": "list"})
    assert listed["exit_code"] == 0
    assert "alice-job" in listed["output"] and "alice-package" in listed["output"]
    assert "bob-job" not in listed["output"] and "bob-package" not in listed["output"]
    assert call({"dataset": "jobs", "action": "update", "id": "bob-job", "fields": {"status": "applied"}})["exit_code"] == 1
    assert call({"dataset": "packages", "action": "update", "id": "bob-package", "fields": {"label": "Changed"}})["exit_code"] == 1

    updated = call({"dataset": "jobs", "action": "update", "id": "alice-job", "fields": {"status": "applied"}})
    assert updated["exit_code"] == 0
    relabeled = call({"dataset": "packages", "action": "update", "id": "alice-package", "fields": {"label": "New keyboard"}})
    assert relabeled["exit_code"] == 0
    with sessions() as db:
        alice_job = db.get(JobApplication, "alice-job")
        assert alice_job.status == "applied"
        assert alice_job.raw_metadata == '{"keep":true}'
        assert db.get(JobApplication, "bob-job").status == "saved"
        assert db.get(TrackedPackage, "alice-package").label == "New keyboard"
        assert db.get(TrackedPackage, "bob-package").label == "Private"


def test_tracker_questions_reach_the_registered_tool():
    for question in ("Which of my job applications need follow-up?", "Where are my packages?", "Archive package ABC", "Mark that job as applied"):
        assert classify_tool_intent(question).category == "trackers"
    assert "manage_trackers" in TOOL_TAGS
    assert any(schema["function"]["name"] == "manage_trackers" for schema in FUNCTION_TOOL_SCHEMAS)
    assert "trackers" in _classify_agent_request([], "Which of my job applications need follow-up?")["domains"]
