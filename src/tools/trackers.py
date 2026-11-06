"""Owner-scoped chat access to the Jobs and Parcels trackers."""

from __future__ import annotations

import json
import uuid
from typing import Optional

from pydantic import ValidationError
from sqlalchemy import or_
from fastapi import HTTPException

from core.database import JobApplication, SessionLocal, TrackedPackage
from routes.job_routes import JobPayload, STATUSES, _normalize_url, _serialize
from routes.package_routes import _serial


async def do_manage_trackers(content: str, owner: Optional[str] = None) -> dict:
    """Read or update only the caller's saved job and parcel records."""
    if not owner:
        return {"error": "An authenticated owner is required", "exit_code": 1}
    try:
        args = json.loads(content or "{}")
        if not isinstance(args, dict):
            raise ValueError("Arguments must be an object")
    except (ValueError, TypeError):
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action") or "list").lower()
    dataset = str(args.get("dataset") or "all").lower()
    if dataset not in {"all", "jobs", "packages"}:
        return {"error": "dataset must be all, jobs, or packages", "exit_code": 1}
    if action not in {"list", "get", "create", "update", "archive", "refresh"}:
        return {"error": "Unsupported tracker action", "exit_code": 1}
    if action != "list" and dataset == "all":
        return {"error": "Choose jobs or packages for this action", "exit_code": 1}

    if action in {"create", "refresh"} and dataset == "packages":
        # Package creation and refresh use the existing tracking provider flow.
        from src.tools.system import do_app_api
        path = "/api/packages" if action == "create" else "/api/packages/refresh"
        body = args.get("fields") or {}
        if action == "create" and not isinstance(body, dict):
            return {"error": "fields must be an object", "exit_code": 1}
        return await do_app_api(json.dumps({"path": path, "method": "POST", "body": body}), owner=owner)

    record_id = str(args.get("id") or "").strip()
    if action in {"get", "update", "archive"} and not record_id:
        return {"error": "id is required", "exit_code": 1}
    fields = args.get("fields") or {}
    if action in {"create", "update"} and not isinstance(fields, dict):
        return {"error": "fields must be an object", "exit_code": 1}
    try:
        limit = max(1, min(int(args.get("limit") or 25), 50))
    except (TypeError, ValueError):
        return {"error": "limit must be an integer", "exit_code": 1}
    search = str(args.get("query") or "").strip()[:200]
    status = str(args.get("status") or "").strip()

    db = SessionLocal()
    try:
        if action == "list":
            result = {}
            if dataset in {"all", "jobs"}:
                q = db.query(JobApplication).filter(JobApplication.owner == owner)
                if status:
                    q = q.filter(JobApplication.status == status)
                if search:
                    pat = f"%{search}%"
                    q = q.filter(or_(JobApplication.company.ilike(pat), JobApplication.title.ilike(pat), JobApplication.notes.ilike(pat)))
                rows = q.order_by(JobApplication.updated_at.desc()).limit(limit).all()
                result["jobs"] = [{
                    "id": row.id, "company": row.company, "title": row.title,
                    "status": row.status, "location": row.location,
                    "applied_at": row.applied_at, "job_url": row.job_url,
                    "notes": (row.notes or "")[:500],
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                } for row in rows]
            if dataset in {"all", "packages"}:
                q = db.query(TrackedPackage).filter(TrackedPackage.owner == owner)
                if not args.get("include_archived"):
                    q = q.filter(TrackedPackage.is_archived.is_(False))
                if status:
                    q = q.filter(TrackedPackage.status == status)
                if search:
                    pat = f"%{search}%"
                    q = q.filter(or_(TrackedPackage.label.ilike(pat), TrackedPackage.tracking_number.ilike(pat), TrackedPackage.carrier_name.ilike(pat)))
                rows = q.order_by(TrackedPackage.updated_at.desc()).limit(limit).all()
                result["packages"] = [_serial(row) | {"is_archived": bool(row.is_archived)} for row in rows]
            return {"output": json.dumps(result, ensure_ascii=False, default=str), "untrusted_content": True, "exit_code": 0}

        if dataset == "jobs":
            row = db.query(JobApplication).filter(JobApplication.owner == owner, JobApplication.id == record_id).first() if action != "create" else None
            if action != "create" and row is None:
                return {"error": "Job application not found", "exit_code": 1}
            if action == "get":
                result = _serialize(row)
                result["description"] = result["description"][:8000]
            elif action == "archive":
                row.status = "archived"
                db.commit()
                result = _serialize(row)
            else:
                allowed = set(JobPayload.model_fields) - {"raw_metadata"}
                unknown = set(fields) - allowed
                if unknown:
                    return {"error": f"Unsupported job fields: {', '.join(sorted(unknown))}", "exit_code": 1}
                payload = {k: getattr(row, k) for k in allowed} if row else {}
                payload.update(fields)
                try:
                    validated = JobPayload.model_validate(payload)
                    if validated.status not in STATUSES:
                        return {"error": "Invalid job status", "exit_code": 1}
                    values = validated.model_dump(exclude={"raw_metadata"})
                    values["job_url"] = _normalize_url(values["job_url"])
                except (ValidationError, ValueError, HTTPException) as exc:
                    return {"error": str(exc)[:500], "exit_code": 1}
                if row is None:
                    row = JobApplication(id=str(uuid.uuid4()), owner=owner, raw_metadata="{}")
                    db.add(row)
                for key, value in values.items():
                    setattr(row, key, value)
                db.commit()
                db.refresh(row)
                result = _serialize(row)
            return {"output": json.dumps(result, ensure_ascii=False, default=str), "untrusted_content": True, "exit_code": 0}

        row = db.query(TrackedPackage).filter(TrackedPackage.owner == owner, TrackedPackage.id == record_id).first()
        if row is None:
            return {"error": "Package not found", "exit_code": 1}
        if action == "archive":
            row.is_archived = True
            db.commit()
        elif action == "update":
            if set(fields) - {"label", "is_archived"}:
                return {"error": "Only label and is_archived can be changed; tracking status comes from the carrier", "exit_code": 1}
            if "label" in fields:
                row.label = str(fields["label"])[:300]
            if "is_archived" in fields:
                row.is_archived = bool(fields["is_archived"])
            db.commit()
        result = _serial(row) | {"is_archived": bool(row.is_archived)}
        return {"output": json.dumps(result, ensure_ascii=False, default=str), "untrusted_content": True, "exit_code": 0}
    finally:
        db.close()
