"""Small, fixed-host Ship24 tracking client."""
from __future__ import annotations

from datetime import datetime, timezone
import requests

BASE = "https://api.ship24.com/public/v1"

def _error_message(response: requests.Response) -> str:
    try:
        data = response.json()
        errors = data.get("errors") or []
        if errors:
            return str(errors[0].get("message") or errors[0].get("code") or "Ship24 request failed")
    except ValueError:
        pass
    return f"Ship24 request failed ({response.status_code})"

def track_package(tracking_number: str, api_key: str, timeout: int = 70) -> dict:
    """Create an idempotent tracker and return its current tracking result."""
    response = requests.post(
        BASE + "/trackers/track", json={"trackingNumber": tracking_number},
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=timeout,
    )
    if not response.ok:
        raise RuntimeError(_error_message(response))
    trackings = ((response.json().get("data") or {}).get("trackings") or [])
    return trackings[0] if trackings else {"tracker": {"trackingNumber": tracking_number}, "shipment": {}, "events": []}

def normalize_tracking(item: dict) -> dict:
    tracker = item.get("tracker") or {}
    shipment = item.get("shipment") or {}
    events = item.get("events") or []
    latest = events[0] if events else {}
    delivery = shipment.get("delivery") or {}
    courier_eta = delivery.get("courierEstimatedDeliveryDate") or {}
    raw_status = shipment.get("statusMilestone") or latest.get("statusMilestone") or "pending"
    status = str(raw_status).casefold().replace(" ", "_")
    if status in {"info_received", "data_received"}:
        status = "label_created"
    courier_code = latest.get("courierCode") or latest.get("sourceCode")
    if not courier_code:
        codes = tracker.get("courierCode") or []
        courier_code = codes[0] if isinstance(codes, list) and codes else codes
    carrier_name = tracker.get("courierName") or (str(courier_code).replace("-", " ").title() if courier_code else "")
    eta = delivery.get("estimatedDeliveryDate") or ""
    return {
        "tracking_number": str(tracker.get("trackingNumber") or latest.get("trackingNumber") or ""),
        "carrier_name": carrier_name,
        "status": status or "pending",
        "status_detail": latest.get("status") or shipment.get("statusCode") or "Waiting for the first carrier update",
        "location": latest.get("location") or "",
        "estimated_from": courier_eta.get("from") or eta,
        "estimated_to": courier_eta.get("to") or eta,
        "latest_event_at": latest.get("occurrenceDatetime") or "",
        "last_checked_at": datetime.now(timezone.utc).isoformat(),
    }
