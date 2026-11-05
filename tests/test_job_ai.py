import json

from services import job_ai


class _Response:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        fields = {key: "" for key in job_ai.JOB_SCHEMA["properties"]}
        fields.update({
            "external_job_id": "REQ-123",
            "company": "Example Corp", "title": "Data Analyst", "location": "Remote",
        })
        return {
            "steps": [{
                "type": "model_output",
                "content": [{"type": "text", "text": json.dumps(fields)}],
            }],
        }


def test_gemini_structured_extraction_uses_header_and_fixed_endpoint(monkeypatch):
    seen = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen.update(kwargs)
        return _Response()

    monkeypatch.setattr(job_ai.httpx, "post", fake_post)
    result = job_ai.extract_job_with_gemini(
        "Example Corp is hiring a Data Analyst.", {}, api_key="secret-key",
    )

    assert result["company"] == "Example Corp"
    assert result["external_job_id"] == "REQ-123"
    assert seen["url"] == job_ai.GEMINI_INTERACTIONS_URL
    assert seen["headers"]["x-goog-api-key"] == "secret-key"
    assert "secret-key" not in seen["url"]
    assert seen["json"]["response_format"]["mime_type"] == "application/json"


def test_ai_only_fills_missing_or_short_fields():
    draft = {"company": "Trusted Corp", "title": "", "description": "short"}
    merged = job_ai.merge_ai_draft(
        draft,
        {"company": "Wrong Corp", "title": "Engineer", "description": "Detailed role " * 30},
    )
    assert merged["company"] == "Trusted Corp"
    assert merged["title"] == "Engineer"
    assert merged["description"].startswith("Detailed role")
    assert merged["ai_enhanced"] is True


def test_fallback_runs_only_when_core_fields_are_incomplete():
    complete = {"company": "A", "title": "B", "description": "x" * 200}
    assert job_ai.needs_ai_fallback(complete) is False
    assert job_ai.needs_ai_fallback({**complete, "company": ""}) is True
