"""Owner-scoped job application tracker and safe posting scraper."""

from __future__ import annotations

import asyncio
import json
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.database import EmailAccount, JobAiSettings, JobApplication, JobEmailMatch, SessionLocal
from services.job_ai import extract_job_with_gemini
from services.job_email import scan_mailbox
from src.auth_helpers import require_user, storage_owner_for_request
from src.constants import WEB_FETCH_SOFT_MAX_BYTES, WEB_FETCH_USER_AGENT
from src.secret_storage import decrypt, encrypt


STATUSES = {"saved", "applied", "interviewing", "offer", "rejected", "withdrawn", "archived"}


class ScrapeRequest(BaseModel):
    url: str = Field(min_length=4, max_length=4000)


class JobPayload(BaseModel):
    external_job_id: str = Field(default="", max_length=500)
    company: str = Field(default="", max_length=500)
    title: str = Field(default="", max_length=500)
    location: str = Field(default="", max_length=1000)
    employment_type: str = Field(default="", max_length=500)
    salary: str = Field(default="", max_length=500)
    job_url: str = Field(min_length=4, max_length=4000)
    apply_url: str = Field(default="", max_length=4000)
    source_site: str = Field(default="", max_length=255)
    description: str = Field(default="", max_length=100_000)
    posted_at: str = Field(default="", max_length=100)
    valid_through: str = Field(default="", max_length=100)
    applied_at: str = Field(default="", max_length=100)
    status: str = Field(default="saved", max_length=40)
    notes: str = Field(default="", max_length=20_000)
    raw_metadata: dict = Field(default_factory=dict)


class JobAiSettingsPayload(BaseModel):
    enabled: bool = False
    model: str = Field(default="gemini-2.5-flash-lite", min_length=1, max_length=128)
    api_key: str | None = Field(default=None, max_length=1000)
    remove_key: bool = False


def _owner(request: Request) -> str:
    require_user(request)
    return storage_owner_for_request(request) or ""


def _serialize(row: JobApplication) -> dict:
    return {
        "id": row.id, "external_job_id": row.external_job_id or "",
        "company": row.company or "", "title": row.title or "",
        "location": row.location or "", "employment_type": row.employment_type or "",
        "salary": row.salary or "", "job_url": row.job_url or "",
        "apply_url": row.apply_url or "", "source_site": row.source_site or "",
        "description": row.description or "", "posted_at": row.posted_at or "",
        "valid_through": row.valid_through or "", "applied_at": row.applied_at or "",
        "status": row.status or "saved", "notes": row.notes or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _serialize_email_match(row: JobEmailMatch) -> dict:
    return {
        "id": row.id,
        "job_id": row.job_application_id,
        "company": row.job.company if row.job else "",
        "title": row.job.title if row.job else "",
        "current_status": row.job.status if row.job else "",
        "sender": row.sender or "",
        "subject": row.subject or "",
        "received_at": row.received_at or "",
        "proposed_status": row.proposed_status,
        "confidence": row.confidence,
        "reason": row.reason or "",
        "evidence": row.evidence or "",
        "review_state": row.review_state,
    }


def _normalize_url(raw: str) -> str:
    value = (raw or "").strip()
    if "://" not in value:
        value = "https://" + value
    try:
        parsed = urlparse(value)
    except ValueError as exc:
        raise HTTPException(400, "Invalid job URL") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(400, "Enter a public http(s) job URL without embedded credentials")
    return value


def _ai_config(owner: str) -> tuple[bool, str, str]:
    db = SessionLocal()
    try:
        row = db.query(JobAiSettings).filter(JobAiSettings.owner == owner).first()
        if row is None:
            return False, "gemini-2.5-flash-lite", ""
        return bool(row.enabled), row.model or "gemini-2.5-flash-lite", decrypt(row.api_key or "")
    finally:
        db.close()


def setup_job_routes() -> APIRouter:
    router = APIRouter(prefix="/api/jobs", tags=["jobs"])

    @router.post("/scrape")
    async def scrape_job(payload: ScrapeRequest, request: Request):
        owner = _owner(request)
        url = _normalize_url(payload.url)

        def fetch_html():
            # Download only: Gemini, rather than local parsing heuristics,
            # performs every field extraction from the untrusted HTML.
            from services.search.content import _get_public_url

            headers = {
                "User-Agent": WEB_FETCH_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.7",
            }
            response = _get_public_url(
                url, headers=headers, timeout=12, max_bytes=WEB_FETCH_SOFT_MAX_BYTES,
            )
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if "html" not in content_type:
                raise ValueError("The URL did not return an HTML job page")
            return response.text

        try:
            page_text = await asyncio.wait_for(asyncio.to_thread(fetch_html), timeout=35)
        except asyncio.TimeoutError as exc:
            raise HTTPException(504, "The job site took too long to respond") from exc
        except Exception as exc:
            message = str(exc).strip() or "The job page could not be read"
            raise HTTPException(422, message[:500]) from exc

        enabled, model, api_key = _ai_config(owner)
        if not enabled or not api_key:
            raise HTTPException(400, "Enable Gemini and save an API key before importing jobs")
        base = {
            "job_url": url,
            "apply_url": url,
            "source_site": (urlparse(url).hostname or "").removeprefix("www.")[:255],
        }
        try:
            ai_fields = await asyncio.wait_for(
                asyncio.to_thread(
                    extract_job_with_gemini, page_text, base, api_key=api_key, model=model,
                ),
                timeout=30,
            )
        except Exception as exc:
            raise HTTPException(
                422, (str(exc).strip() or "Gemini extraction failed")[:300],
            ) from exc
        draft = {**base, **ai_fields, "ai_enhanced": True}
        if not draft.get("apply_url", "").startswith(("http://", "https://")):
            draft["apply_url"] = url
        return {
            "draft": draft,
            "review_required": True,
            "ai_used": True,
            "ai_warning": "",
        }

    @router.get("/ai-settings")
    def get_ai_settings(request: Request):
        owner = _owner(request)
        db = SessionLocal()
        try:
            row = db.query(JobAiSettings).filter(JobAiSettings.owner == owner).first()
            return {
                "enabled": bool(row.enabled) if row else False,
                "provider": "gemini",
                "model": (row.model if row else "gemini-2.5-flash-lite"),
                "has_api_key": bool(decrypt(row.api_key or "")) if row else False,
            }
        finally:
            db.close()

    @router.put("/ai-settings")
    def update_ai_settings(payload: JobAiSettingsPayload, request: Request):
        owner = _owner(request)
        model = payload.model.strip()
        # Validation also prevents model text from altering the fixed API host.
        from services.job_ai import MODEL_RE
        if not MODEL_RE.fullmatch(model):
            raise HTTPException(400, "Invalid Gemini model name")
        db = SessionLocal()
        try:
            row = db.query(JobAiSettings).filter(JobAiSettings.owner == owner).first()
            if row is None:
                row = JobAiSettings(id=str(uuid.uuid4()), owner=owner)
                db.add(row)
            if payload.remove_key:
                row.api_key = ""
            elif payload.api_key is not None and payload.api_key.strip():
                row.api_key = encrypt(payload.api_key.strip())
            row.enabled = bool(payload.enabled)
            row.provider = "gemini"
            row.model = model
            db.commit()
            return {
                "enabled": bool(row.enabled), "provider": "gemini", "model": row.model,
                "has_api_key": bool(decrypt(row.api_key or "")),
            }
        finally:
            db.close()

    @router.post("/ai-settings/test")
    async def test_ai_settings(request: Request):
        owner = _owner(request)
        _enabled, model, api_key = _ai_config(owner)
        if not api_key:
            raise HTTPException(400, "Save a Gemini API key first")
        sample = "Example Corp is hiring a Remote Data Analyst. This is a full-time role."
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    extract_job_with_gemini, sample, {}, api_key=api_key, model=model, timeout=20,
                ),
                timeout=25,
            )
        except Exception as exc:
            raise HTTPException(422, (str(exc).strip() or "Gemini connection failed")[:300]) from exc
        return {"ok": bool(result.get("title")), "model": model}

    @router.get("")
    def list_jobs(request: Request, status: str | None = None):
        owner = _owner(request)
        db = SessionLocal()
        try:
            query = db.query(JobApplication).filter(JobApplication.owner == owner)
            if status:
                query = query.filter(JobApplication.status == status)
            rows = query.order_by(JobApplication.updated_at.desc()).all()
            return {"jobs": [_serialize(row) for row in rows]}
        finally:
            db.close()

    @router.post("")
    def create_job(payload: JobPayload, request: Request):
        owner = _owner(request)
        if payload.status not in STATUSES:
            raise HTTPException(400, "Invalid job status")
        url = _normalize_url(payload.job_url)
        db = SessionLocal()
        try:
            row = JobApplication(
                id=str(uuid.uuid4()), owner=owner, **payload.model_dump(exclude={"job_url", "raw_metadata"}),
                job_url=url, raw_metadata=json.dumps(payload.raw_metadata, ensure_ascii=False),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return {"job": _serialize(row)}
        finally:
            db.close()

    @router.post("/scan-email")
    async def scan_job_email(request: Request):
        owner = _owner(request)
        db = SessionLocal()
        try:
            account = db.query(EmailAccount).filter(
                EmailAccount.owner == owner, EmailAccount.enabled == True,  # noqa: E712
            ).order_by(EmailAccount.is_default.desc(), EmailAccount.created_at.asc()).first()
            if account is None:
                raise HTTPException(400, "Add and enable an email account in Settings first")
            jobs = db.query(JobApplication).filter(
                JobApplication.owner == owner,
                JobApplication.status.notin_(["withdrawn", "archived"]),
            ).all()
            if not jobs:
                raise HTTPException(400, "Save at least one job application before scanning email")
            account_id = account.id
        finally:
            db.close()

        try:
            findings = await asyncio.wait_for(
                asyncio.to_thread(scan_mailbox, owner, account_id, jobs), timeout=90,
            )
        except asyncio.TimeoutError as exc:
            raise HTTPException(504, "The mailbox scan took too long. Please try again.") from exc
        except Exception as exc:
            raise HTTPException(422, (str(exc).strip() or "Could not scan the mailbox")[:300]) from exc

        db = SessionLocal()
        try:
            for finding in findings:
                job = finding.pop("job")
                row = db.query(JobEmailMatch).filter(
                    JobEmailMatch.owner == owner,
                    JobEmailMatch.email_account_id == account_id,
                    JobEmailMatch.folder == "INBOX",
                    JobEmailMatch.email_uid == finding["uid"],
                    JobEmailMatch.job_application_id == job.id,
                ).first()
                if row is None:
                    row = JobEmailMatch(
                        id=str(uuid.uuid4()), owner=owner, email_account_id=account_id,
                        folder="INBOX", email_uid=finding["uid"], job_application_id=job.id,
                    )
                    db.add(row)
                if row.review_state != "accepted":
                    row.message_id = finding["message_id"]
                    row.sender = finding["sender"]
                    row.subject = finding["subject"]
                    row.received_at = finding["received_at"]
                    row.proposed_status = finding["proposed_status"]
                    row.confidence = finding["confidence"]
                    row.reason = finding["reason"]
                    row.evidence = finding["evidence"]
                    if row.review_state != "dismissed":
                        row.review_state = "pending"
            db.commit()
            pending = db.query(JobEmailMatch).filter(
                JobEmailMatch.owner == owner, JobEmailMatch.review_state == "pending",
            ).order_by(JobEmailMatch.created_at.desc()).all()
            return {"found": len(findings), "suggestions": [_serialize_email_match(row) for row in pending]}
        finally:
            db.close()

    @router.get("/email-suggestions")
    def list_email_suggestions(request: Request):
        owner = _owner(request)
        db = SessionLocal()
        try:
            rows = db.query(JobEmailMatch).filter(
                JobEmailMatch.owner == owner, JobEmailMatch.review_state == "pending",
            ).order_by(JobEmailMatch.created_at.desc()).all()
            return {"suggestions": [_serialize_email_match(row) for row in rows]}
        finally:
            db.close()

    @router.post("/email-suggestions/{suggestion_id}/accept")
    def accept_email_suggestion(suggestion_id: str, request: Request):
        owner = _owner(request)
        db = SessionLocal()
        try:
            row = db.query(JobEmailMatch).filter(
                JobEmailMatch.id == suggestion_id, JobEmailMatch.owner == owner,
            ).first()
            if row is None or row.job is None:
                raise HTTPException(404, "Email suggestion not found")
            if row.proposed_status not in STATUSES:
                raise HTTPException(400, "Invalid proposed job status")
            row.job.status = row.proposed_status
            row.review_state = "accepted"
            db.commit()
            return {"job": _serialize(row.job), "suggestion": _serialize_email_match(row)}
        finally:
            db.close()

    @router.post("/email-suggestions/{suggestion_id}/dismiss")
    def dismiss_email_suggestion(suggestion_id: str, request: Request):
        owner = _owner(request)
        db = SessionLocal()
        try:
            row = db.query(JobEmailMatch).filter(
                JobEmailMatch.id == suggestion_id, JobEmailMatch.owner == owner,
            ).first()
            if row is None:
                raise HTTPException(404, "Email suggestion not found")
            row.review_state = "dismissed"
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.put("/{job_id}")
    def update_job(job_id: str, payload: JobPayload, request: Request):
        owner = _owner(request)
        if payload.status not in STATUSES:
            raise HTTPException(400, "Invalid job status")
        db = SessionLocal()
        try:
            row = db.query(JobApplication).filter(
                JobApplication.id == job_id, JobApplication.owner == owner,
            ).first()
            if row is None:
                raise HTTPException(404, "Job application not found")
            values = payload.model_dump(exclude={"raw_metadata"})
            values["job_url"] = _normalize_url(values["job_url"])
            for key, value in values.items():
                setattr(row, key, value)
            row.raw_metadata = json.dumps(payload.raw_metadata, ensure_ascii=False)
            db.commit()
            db.refresh(row)
            return {"job": _serialize(row)}
        finally:
            db.close()

    return router
