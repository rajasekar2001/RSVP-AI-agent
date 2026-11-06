"""Inbound call-back handling, call-time limit text and report export.

Pure functions: they take the saved state (a dict) and return data, so they are easy to test
and have no dependency on FastAPI.
"""
import csv
import io
import json
import re
from datetime import datetime

DEFAULT_MAX_CALL_SECONDS = 50

DEFAULT_INBOUND_PROMPT = (
    "This is an INCOMING call: the person is calling us back. They missed or could not take our earlier call.\n"
    "The greeting has already been spoken (it asks whether they are free now); do not repeat it. Wait for the answer.\n"
    "1. If they say they are free / yes / okay: say thank you, give the event details from the EVENT FACTS (one short sentence), "
    "then ask: Will you be attending the event?\n"
    "2. If they say they are busy or not free: say No problem, we will call you again later. Thank you. Then end the call.\n"
    "3. If they ask a question about the event, answer ONLY from the EVENT FACTS; if it is not listed say you do not have it.\n"
    "4. RSVP Yes: ask How many additional guests will you bring? and, if more than zero, Could you tell me their names? Collect every name.\n"
    "5. RSVP No: thank them politely and end the call.\n"
    "6. If their RSVP is already recorded, say what is recorded and ask only whether they want to change it.\n"
    "7. If you hear nothing for a few seconds, say once: Hello, can you hear me? Then wait. Never end the call just because of a short silence.\n"
    "8. Close with one short sentence and end the call."
)
LEGACY_INBOUND_PROMPT_V1 = (
    "This is an INCOMING call: the person is calling us back (they missed or cut our earlier call).\n"
    "The greeting has already been spoken; do not repeat it.\n"
    "1. Answer questions about the event using ONLY the event facts above. If something is not listed, say you do not have it.\n"
    "2. Then, if their RSVP is not recorded yet, ask: Will you be attending the event?\n"
    "3. If Yes: ask How many additional guests will you bring? and, if more than zero, Could you tell me their names? Collect every name.\n"
    "4. If No: thank them politely.\n"
    "5. If their RSVP is already recorded, say what is recorded and ask only whether they want to change it.\n"
    "6. Close with one short sentence and end the call."
)



LEGACY_INBOUND_PROMPTS = {LEGACY_INBOUND_PROMPT_V1}


def time_limit_text(seconds):
    seconds = clamp_seconds(seconds)
    wrap = max(10, seconds - 15)
    return (
        f"HARD TIME LIMIT: the whole call must finish within {seconds} seconds. "
        "Use very short sentences (under 12 words), one question at a time, no small talk, and do not repeat yourself. "
        f"If the RSVP is still not settled after about {wrap} seconds, thank the person and end the call. "
        "End the call as soon as the RSVP and guest names are confirmed."
    )


def clamp_seconds(value):
    try:
        value = int(float(value))
    except (TypeError, ValueError):
        value = DEFAULT_MAX_CALL_SECONDS
    return max(20, min(300, value))


def digits(phone):
    return re.sub(r"\D", "", str(phone or ""))


def phones_match(a, b):
    """Compare two phone numbers by their last 10 digits (ignores +91 / spaces / dashes)."""
    da, db = digits(a), digits(b)
    return len(da) >= 7 and len(db) >= 7 and da[-10:] == db[-10:]


def find_contact_by_phone(state, phone):
    return next((c for c in state.get("contacts", []) if phones_match(c.get("phone"), phone)), None)


def over_limit(duration, limit):
    try:
        return float(duration) > float(limit)
    except (TypeError, ValueError):
        return False


# ----------------------------------------------------------------------------- report
COLUMNS = [
    ("name", "Name"), ("phone", "Phone"), ("direction", "Direction"), ("status", "Status"),
    ("call_status", "Call result"), ("rsvp", "RSVP"), ("guests", "Guests"), ("guest_count", "Guest count"),
    ("duration", "Duration (s)"), ("over_limit", "Over time limit"), ("call_time", "Call time"),
    ("attempts", "Call attempts"), ("rsvp_source", "RSVP source"), ("failure_reason", "Failure reason"),
    ("attempt_id", "Attempt ID"), ("interaction_id", "Interaction ID"), ("transcript", "Transcript"),
]
COLUMN_LABELS = dict(COLUMNS)
DEFAULT_COLUMNS = ["name", "phone", "direction", "status", "rsvp", "guests", "guest_count", "duration", "call_time"]


def _guest_list(value):
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [x.strip() for x in str(value or "").split(",") if x.strip()]


def _transcript_text(rows):
    lines = []
    for row in rows or []:
        if isinstance(row, dict):
            who = "AI" if str(row.get("role", "")).lower() in {"agent", "assistant", "bot", "ai"} else "Guest"
            lines.append(f"{who}: {row.get('indic_text') or row.get('en_text') or row.get('text') or ''}")
    return "\n".join(lines)


def collect_rows(state):
    """One flat row per outbound contact and per inbound call."""
    limit = clamp_seconds((state.get("agent") or {}).get("max_call_seconds", DEFAULT_MAX_CALL_SECONDS))
    rows = []
    for c in state.get("contacts", []):
        guests = _guest_list(c.get("guests"))
        rows.append({
            "name": c.get("name", ""), "phone": c.get("phone", ""), "direction": "Outbound",
            "status": c.get("status", ""), "call_status": c.get("call_status", ""),
            "rsvp": c.get("rsvp", ""), "guests": ", ".join(guests), "guest_count": len(guests),
            "duration": c.get("duration"), "over_limit": "Yes" if over_limit(c.get("duration"), limit) else "No",
            "call_time": c.get("call_ended_at", ""), "rsvp_source": c.get("rsvp_source", "Sarvam variables" if c.get("rsvp") else ""),
            "attempts": c.get("attempts") or (1 if c.get("attempt_id") else 0),
            "failure_reason": c.get("failure_reason", ""), "attempt_id": c.get("attempt_id", ""),
            "interaction_id": c.get("interaction_id", "") or "", "transcript": _transcript_text(c.get("transcript")),
        })
    for call in state.get("inbound_calls", []):
        guests = _guest_list(call.get("guests"))
        rows.append({
            "name": call.get("name") or "Unknown caller", "phone": call.get("phone", ""), "direction": "Inbound",
            "status": "Inbound call", "call_status": "connected",
            "rsvp": call.get("rsvp", ""), "guests": ", ".join(guests), "guest_count": len(guests),
            "duration": call.get("duration"), "over_limit": "Yes" if over_limit(call.get("duration"), limit) else "No",
            "call_time": call.get("start_datetime", ""), "rsvp_source": "inbound call" if call.get("rsvp") else "",
            "attempts": "", "failure_reason": "", "attempt_id": call.get("interaction_id", ""), "interaction_id": call.get("interaction_id", ""),
            "transcript": _transcript_text(call.get("transcript")),
        })
    return rows


def _as_list(value):
    if value in (None, ""):
        return []
    return value if isinstance(value, list) else [value]


def apply_filters(rows, filters):
    f = filters or {}
    statuses = {str(x).lower() for x in _as_list(f.get("status"))}
    rsvps = {str(x).lower() for x in _as_list(f.get("rsvp"))}
    directions = {str(x).lower() for x in _as_list(f.get("direction"))}
    search = str(f.get("search") or "").strip().lower()
    date_from, date_to = str(f.get("date_from") or ""), str(f.get("date_to") or "")
    out = []
    for r in rows:
        if statuses and str(r["status"]).lower() not in statuses:
            continue
        if rsvps:
            key = str(r["rsvp"] or "none").lower()
            if key not in rsvps:
                continue
        if directions and r["direction"].lower() not in directions:
            continue
        if f.get("has_guests") is True and not r["guest_count"]:
            continue
        if f.get("has_guests") is False and r["guest_count"]:
            continue
        if search and search not in " ".join(str(r[k]) for k in ("name", "phone", "guests")).lower():
            continue
        day = str(r["call_time"] or "")[:10]
        if date_from and (not day or day < date_from):
            continue
        if date_to and (not day or day > date_to):
            continue
        dur = r["duration"]
        try:
            if f.get("min_duration") not in (None, "") and (dur is None or float(dur) < float(f["min_duration"])):
                continue
            if f.get("max_duration") not in (None, "") and (dur is None or float(dur) > float(f["max_duration"])):
                continue
        except (TypeError, ValueError):
            pass
        if f.get("over_limit_only") and r["over_limit"] != "Yes":
            continue
        out.append(r)
    return out


def choose_columns(columns):
    keys = [k for k in _as_list(columns) if k in COLUMN_LABELS]
    return keys or list(DEFAULT_COLUMNS)


def _safe_cell(value):
    """Stop spreadsheet formula injection from names/guests spoken or typed by callers."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return "" if value is None else value


def describe_filters(filters):
    f = filters or {}
    parts = []
    for key, label in (("direction", "Call type"), ("status", "Status"), ("rsvp", "RSVP")):
        if _as_list(f.get(key)):
            parts.append(f"{label}: {', '.join(map(str, _as_list(f[key])))}")
    for key, label in (("search", "Search"), ("date_from", "From"), ("date_to", "To"), ("min_duration", "Min s"), ("max_duration", "Max s")):
        if f.get(key) not in (None, ""):
            parts.append(f"{label}: {f[key]}")
    if f.get("has_guests") is True:
        parts.append("Only with guests")
    if f.get("has_guests") is False:
        parts.append("Only without guests")
    if f.get("over_limit_only"):
        parts.append("Only over time limit")
    return "; ".join(parts) or "none (all rows)"


def export_report(rows, columns, fmt, note=""):
    """Return (bytes, content_type, extension)."""
    fmt = str(fmt or "csv").lower()
    keys = choose_columns(columns)
    headers = [COLUMN_LABELS[k] for k in keys]
    table = [[r[k] for k in keys] for r in rows]
    if fmt == "json":
        data = [dict(zip(keys, row)) for row in table]
        return json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"), "application/json", "json"
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(headers)
        for row in table:
            w.writerow([_safe_cell(v) for v in row])
        return ("﻿" + buf.getvalue()).encode("utf-8"), "text/csv; charset=utf-8", "csv"
    if fmt in {"xlsx", "excel"}:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font
        wb = Workbook()
        ws = wb.active
        ws.title = "RSVP report"
        ws.append(headers)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for row in table:
            ws.append([_safe_cell(v) for v in row])
        ws.freeze_panes = "A2"
        for i, key in enumerate(keys, start=1):
            width = max([len(headers[i - 1])] + [min(len(str(r[i - 1] or "")), 60) for r in table]) + 2
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(max(width, 10), 60)
            if key == "transcript":
                for cell in ws[ws.cell(row=1, column=i).column_letter][1:]:
                    cell.alignment = Alignment(wrap_text=True, vertical="top")
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    if fmt == "pdf":
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        styles = getSampleStyleSheet()
        small = styles["BodyText"].clone("small")
        small.fontSize, small.leading = 7, 9
        def cell(v):
            text = "" if v is None else str(v)
            text = (text[:600] + "…") if len(text) > 600 else text
            return Paragraph(text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>"), small)
        data = [[cell(h) for h in headers]] + [[cell(v) for v in row] for row in table]
        out = io.BytesIO()
        doc = SimpleDocTemplate(out, pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)
        t = Table(data, repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ececf6")),
                               ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c8c8d0")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        doc.build([Paragraph("RSVP call report", styles["Title"]),
                   Paragraph(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} &nbsp;|&nbsp; {len(table)} rows"
                             + (f" &nbsp;|&nbsp; Filters: {note.replace('&', '&amp;').replace('<', '&lt;')}" if note else ""), styles["Normal"]),
                   Spacer(1, 8), t])
        return out.getvalue(), "application/pdf", "pdf"
    raise ValueError("Unsupported format. Use csv, xlsx, json or pdf.")


# ---------------------------------------------------------------------------- inbound
def inbound_default_greeting():
    """Caller we tried to reach but could not (busy / no answer / not finished)."""
    return ("Hello {name}! This is the AI assistant for {event_intro}. "
            "I called you earlier but you were not able to take the call. Are you free to talk now?")


def inbound_answered_greeting():
    """Caller who already gave an RSVP."""
    return ("Hello {name}! Thanks for calling back. I'm the AI assistant for {event_intro}. "
            "I already have your RSVP noted. Would you like to change anything, or ask about the event?")


def inbound_unknown_greeting():
    return "Hello! Thanks for calling. I'm the AI assistant for {event_intro}. {event_details} How can I help you?"


# --------------------------------------------------------------------- automatic retries
RETRY_DEFAULTS = {
    "retry_enabled": True,
    "retry_interval_minutes": 60,
    "retry_max_attempts": 5,
    "retry_window_start": "09:00",
    "retry_window_end": "20:00",
}
RETRYABLE_CALL_STATUSES = {"no_answer", "busy", "failed"}  # NOT "connected", NOT "launch_failed" (our own setup errors)
RETRYABLE_STATUSES = {"No answer", "Busy", "Failed"}


def _hhmm(value, default):
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return default
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def retry_settings(agent):
    a = agent or {}
    try:
        minutes = int(float(a.get("retry_interval_minutes", RETRY_DEFAULTS["retry_interval_minutes"])))
    except (TypeError, ValueError):
        minutes = RETRY_DEFAULTS["retry_interval_minutes"]
    try:
        attempts = int(float(a.get("retry_max_attempts", RETRY_DEFAULTS["retry_max_attempts"])))
    except (TypeError, ValueError):
        attempts = RETRY_DEFAULTS["retry_max_attempts"]
    return {
        "retry_enabled": bool(a.get("retry_enabled", RETRY_DEFAULTS["retry_enabled"])),
        "retry_interval_minutes": max(5, min(1440, minutes)),
        "retry_max_attempts": max(1, min(10, attempts)),
        "retry_window_start": _hhmm(a.get("retry_window_start"), RETRY_DEFAULTS["retry_window_start"]),
        "retry_window_end": _hhmm(a.get("retry_window_end"), RETRY_DEFAULTS["retry_window_end"]),
    }


def in_call_window(now, start, end):
    """True when local time `now` is inside [start, end). Supports windows that cross midnight."""
    cur = now.strftime("%H:%M")
    return start <= cur < end if start <= end else (cur >= start or cur < end)


def should_retry(contact, settings):
    """Would this finished call earn another attempt? (call not answered, attempts left, not stopped)"""
    if not settings["retry_enabled"] or contact.get("do_not_call"):
        return False
    if contact.get("rsvp") or contact.get("status") not in RETRYABLE_STATUSES:
        return False
    if contact.get("call_status") not in RETRYABLE_CALL_STATUSES:
        return False
    return int(contact.get("attempts") or 1) < settings["retry_max_attempts"]


def due_for_retry(contact, settings, now):
    """now is a naive local datetime. Returns True when the next attempt should be placed right now."""
    if not should_retry(contact, settings) or not contact.get("next_retry_at"):
        return False
    try:
        due = datetime.fromisoformat(contact["next_retry_at"])
    except ValueError:
        return False
    return now >= due and in_call_window(now, settings["retry_window_start"], settings["retry_window_end"])
