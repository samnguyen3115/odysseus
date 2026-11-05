from __future__ import annotations

import asyncio, json, re, uuid
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.database import PackageTrackerSettings, SessionLocal, TrackedPackage
from services.package_tracking import normalize_tracking, track_package
from src.auth_helpers import require_user, storage_owner_for_request
from src.secret_storage import decrypt, encrypt

TRACKING_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 -]{5,59}$")

class PackageCreate(BaseModel):
    tracking_number: str = Field(min_length=6, max_length=60)
    label: str = Field(default="", max_length=300)
    carrier_code: int | None = None

class SettingsPayload(BaseModel):
    api_key: str | None = Field(default=None, max_length=1000)
    remove_key: bool = False

def _owner(request):
    require_user(request); return storage_owner_for_request(request) or ""

def _serial(row):
    return {"id":row.id,"tracking_number":row.tracking_number,"label":row.label or "","carrier_code":row.carrier_code,"carrier_name":row.carrier_name or "","status":row.status,"status_detail":row.status_detail or "","location":row.location or "","estimated_from":row.estimated_from or "","estimated_to":row.estimated_to or "","latest_event_at":row.latest_event_at or "","last_checked_at":row.last_checked_at or "","tracking_url":f"https://www.ship24.com/tracking?p={row.tracking_number}"}

def _key(owner):
    db=SessionLocal()
    try:
        row=db.query(PackageTrackerSettings).filter(PackageTrackerSettings.owner==owner).first()
        return decrypt(row.api_key or "") if row and row.provider == "ship24" else ""
    finally: db.close()

def setup_package_routes():
    router=APIRouter(prefix="/api/packages",tags=["packages"])

    @router.get("")
    def list_packages(request:Request, archived:bool=False):
        owner=_owner(request); db=SessionLocal()
        try:
            rows=db.query(TrackedPackage).filter(TrackedPackage.owner==owner,TrackedPackage.is_archived==archived).order_by(TrackedPackage.updated_at.desc()).all()
            return {"packages":[_serial(x) for x in rows]}
        finally: db.close()

    @router.get("/settings")
    def settings(request:Request): return {"provider":"ship24","has_api_key":bool(_key(_owner(request)))}

    @router.put("/settings")
    def save_settings(payload:SettingsPayload,request:Request):
        owner=_owner(request); db=SessionLocal()
        try:
            row=db.query(PackageTrackerSettings).filter(PackageTrackerSettings.owner==owner).first()
            if not row: row=PackageTrackerSettings(id=str(uuid.uuid4()),owner=owner); db.add(row)
            row.provider="ship24"
            if payload.remove_key: row.api_key=""
            elif payload.api_key and payload.api_key.strip(): row.api_key=encrypt(payload.api_key.strip())
            db.commit(); return {"provider":"ship24","has_api_key":bool(decrypt(row.api_key or ""))}
        finally: db.close()

    @router.post("")
    async def add_package(payload:PackageCreate,request:Request):
        owner=_owner(request); number=re.sub(r"[ -]","",payload.tracking_number).upper()
        if not TRACKING_RE.fullmatch(payload.tracking_number.strip()): raise HTTPException(400,"Invalid tracking number")
        key=_key(owner)
        if not key: raise HTTPException(400,"Save a Ship24 API key first")
        try: result=await asyncio.to_thread(track_package,number,key)
        except Exception as exc: raise HTTPException(422,str(exc)[:300]) from exc
        tracking=normalize_tracking(result)
        db=SessionLocal()
        try:
            if db.query(TrackedPackage).filter(TrackedPackage.owner==owner,TrackedPackage.tracking_number==number).first(): raise HTTPException(409,"This package is already tracked")
            row=TrackedPackage(id=str(uuid.uuid4()),owner=owner,tracking_number=number,label=payload.label.strip(),status=tracking.get("status") or "pending")
            for field,value in tracking.items():
                if field!="tracking_number" and value is not None: setattr(row,field,value)
            row.raw_tracking=json.dumps(result)
            db.add(row); db.commit(); db.refresh(row); return {"package":_serial(row)}
        finally: db.close()

    @router.post("/refresh")
    async def refresh_all(request:Request):
        owner=_owner(request); key=_key(owner)
        if not key: raise HTTPException(400,"Save a Ship24 API key first")
        db=SessionLocal()
        try: rows=db.query(TrackedPackage).filter(TrackedPackage.owner==owner,TrackedPackage.is_archived==False).all() # noqa
        finally: db.close()
        updated=0; errors=[]
        for original in rows:
            try:
                result=await asyncio.to_thread(track_package,original.tracking_number,key)
                data=normalize_tracking(result)
            except Exception as exc:
                errors.append(f"{original.tracking_number}: {str(exc)[:160]}"); continue
            db=SessionLocal()
            try:
                row=db.query(TrackedPackage).filter(TrackedPackage.id==original.id,TrackedPackage.owner==owner).first()
                for field,value in data.items():
                    if field!="tracking_number" and value is not None: setattr(row,field,value)
                row.raw_tracking=json.dumps(result); updated+=1
                db.commit()
            finally: db.close()
        db=SessionLocal()
        try:
            final=db.query(TrackedPackage).filter(TrackedPackage.owner==owner,TrackedPackage.is_archived==False).order_by(TrackedPackage.updated_at.desc()).all() # noqa
            return {"updated":updated,"errors":errors,"packages":[_serial(x) for x in final]}
        finally: db.close()

    @router.post("/{package_id}/archive")
    def archive(package_id:str,request:Request):
        owner=_owner(request); db=SessionLocal()
        try:
            row=db.query(TrackedPackage).filter(TrackedPackage.id==package_id,TrackedPackage.owner==owner).first()
            if not row: raise HTTPException(404,"Package not found")
            row.is_archived=True; db.commit(); return {"ok":True}
        finally: db.close()
    return router
