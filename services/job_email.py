"""Read-only, local job-status detection for mailbox messages."""

from __future__ import annotations

import email
import re
from datetime import datetime, timedelta, timezone

WORD_RE = re.compile(r"[a-z0-9]+")
NOISE = {"the", "and", "for", "with", "from", "your", "our", "inc", "llc", "corp", "company", "position", "role", "job"}
STATUS_SUBJECT = re.compile(r"application|interview|candidate|assessment|next step|offer|update|position|recruit", re.I)
IGNORE = re.compile(r"job alert|jobs you may|recommended jobs|new jobs|career newsletter|unsubscribe", re.I)

PATTERNS = (
    ("offer", re.compile(r"\b(pleased to (?:extend|make) (?:you )?an offer|offer of employment|job offer|offer letter)\b", re.I)),
    ("rejected", re.compile(r"\b(not (?:be )?moving forward|decided not to (?:move forward|proceed)|not selected|other candidates|another candidate|unable to offer|cannot offer|position (?:has been|was) filled|will not be proceeding|regret to inform)\b", re.I)),
    ("interviewing", re.compile(r"\b(interview|phone screen|screening call|technical assessment|coding assessment|next (?:step|stage)|schedule (?:a |your )?(?:call|conversation)|meet (?:the|our) team|share your availability)\b", re.I)),
    ("applied", re.compile(r"\b(application (?:has been )?received|thank you for applying|application confirmation|successfully (?:submitted|received))\b", re.I)),
)


def _tokens(value: str) -> set[str]:
    return {word for word in WORD_RE.findall((value or "").casefold()) if len(word) > 2 and word not in NOISE}


def classify_status(text: str):
    compact = re.sub(r"\s+", " ", text or "").strip()
    if IGNORE.search(compact[:400]):
        return None
    for status, pattern in PATTERNS:
        match = pattern.search(compact)
        if match:
            start = max(0, match.start() - 90)
            end = min(len(compact), match.end() + 150)
            return status, compact[start:end][:420]
    return None


def match_job(message_text: str, jobs) -> tuple[object | None, float, str]:
    folded = (message_text or "").casefold()
    msg_tokens = _tokens(folded)
    best = (None, 0.0, "")
    for job in jobs:
        score = 0.0
        reasons = []
        external_id = (job.external_job_id or "").strip().casefold()
        if external_id and len(external_id) >= 4 and external_id in folded:
            score += 0.65
            reasons.append("job ID")
        company_tokens = _tokens(job.company or "")
        company_overlap = len(company_tokens & msg_tokens) / max(1, len(company_tokens))
        if company_tokens and company_overlap >= 0.6:
            score += 0.45 * company_overlap
            reasons.append("company")
        title_tokens = _tokens(job.title or "")
        title_overlap = len(title_tokens & msg_tokens) / max(1, len(title_tokens))
        if title_tokens and title_overlap >= 0.5:
            score += 0.35 * title_overlap
            reasons.append("position")
        if score > best[1]:
            best = (job, min(score, 0.99), " and ".join(reasons))
    if best[1] < 0.38:
        return None, best[1], ""
    return best


def _literal(rows):
    for item in rows or []:
        if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes):
            return item[1]
    return b""


def _headers_by_uid(rows):
    out = []
    for item in rows or []:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        meta = item[0] if isinstance(item[0], bytes) else str(item[0]).encode()
        found = re.search(rb"\bUID\s+(\d+)\b", meta, re.I)
        if found and isinstance(item[1], bytes):
            out.append((found.group(1).decode(), email.message_from_bytes(item[1])))
    return out


def scan_mailbox(owner: str, account_id: str, jobs, *, days: int = 120, limit: int = 250):
    """Return local suggestions without changing provider flags or job rows."""
    # Keep mailbox infrastructure lazy so the pure classifier can be tested
    # without initializing account credential storage.
    from routes.email.email_helpers import _decode_header, _extract_text, _imap

    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
    findings = []
    with _imap(account_id, owner=owner) as conn:
        status, _ = conn.select('"INBOX"', readonly=True)
        if status != "OK":
            raise RuntimeError("Could not open the inbox")
        status, rows = conn.uid("SEARCH", None, f"SINCE {since}")
        if status != "OK":
            raise RuntimeError("Could not search the inbox")
        uids = (rows[0] or b"").split()[-limit:]
        if not uids:
            return []
        uid_set = b",".join(uids)
        status, header_rows = conn.uid(
            "FETCH", uid_set,
            "(UID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID SUBJECT FROM DATE)])",
        )
        if status != "OK":
            raise RuntimeError("Could not read inbox headers")
        for uid, header in reversed(_headers_by_uid(header_rows)):
            subject = _decode_header(header.get("Subject", ""))
            sender = _decode_header(header.get("From", ""))
            header_text = f"{subject}\n{sender}"
            header_job, _, _ = match_job(header_text, jobs)
            if not header_job and not STATUS_SUBJECT.search(subject):
                continue
            # A bounded partial fetch avoids downloading large attachments.
            # BODY.PEEK keeps unread messages unread.
            fetch_status, body_rows = conn.uid("FETCH", uid, "(BODY.PEEK[]<0.262144>)")
            if fetch_status != "OK":
                continue
            raw = _literal(body_rows)
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            body = _extract_text(msg)[:120_000]
            combined = f"{subject}\n{sender}\n{body}"
            classification = classify_status(combined)
            if not classification:
                continue
            job, confidence, matched_by = match_job(combined, jobs)
            if not job:
                continue
            proposed_status, evidence = classification
            findings.append({
                "job": job, "uid": uid,
                "message_id": (msg.get("Message-ID") or "")[:500],
                "sender": sender[:500], "subject": subject[:1000],
                "received_at": (msg.get("Date") or "")[:200],
                "proposed_status": proposed_status,
                "confidence": confidence,
                "reason": f"Matched by {matched_by}", "evidence": evidence,
            })
    return findings
