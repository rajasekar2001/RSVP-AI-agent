"""Derive RSVP + guest names from a call transcript.

Used when Sarvam's webhook has no `rsvp` / `guest_names` output variables, so the
dashboard works without configuring extraction inside the Sarvam dashboard.

Transcript rows look like {"role": "agent"|"user", "en_text": "..."}.
Rule-based on purpose: predictable, free, offline. A "Yes" to the identity
question ("Am I speaking with Raja?") is never treated as attendance.
"""
import re

NUMBER_WORDS = {
    "zero": 0, "no": 0, "none": 0, "nobody": 0, "nil": 0,
    "one": 1, "a": 1, "single": 1, "two": 2, "couple": 2, "both": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

ATTEND_Q = re.compile(
    r"(will you (be )?(attend|coming|come|join|make it)|are you (attending|coming|able to|planning)|"
    r"can you (attend|come|make it|join)|be attending|attending\?|confirm your (attendance|rsvp)|"
    r"coming (to|for) the)", re.I)
GUEST_COUNT_Q = re.compile(
    r"(how many|any|additional|extra|bring(ing)?|plus[- ]?one|accompan)[^.?!]*\b(guest|people|person|plus|friend|famil)", re.I)
GUEST_NAME_Q = re.compile(r"\b(name|names|called)\b", re.I)
CONFIRM_Q = re.compile(r"(sound correct|is that correct|is that right|all correct|correct\?|right\?)", re.I)

YES = re.compile(r"\b(yes|yeah|yep|yup|sure|definitely|absolutely|of course|ok|okay|i will|i'll be|i am coming|we will|count me in|attending|coming)\b", re.I)
NO = re.compile(r"\b(no|nope|nah|not|can't|cannot|won't|unable|sorry|decline)\b", re.I)
MAYBE = re.compile(r"\b(maybe|not sure|unsure|might|perhaps|tentative|let me check|i'll try|will try)\b", re.I)

NAME_LEAD = re.compile(
    r"^(?:(?:the\s+)?(?:first|second|third|1st|2nd|3rd)\s+guest(?:'s)?\s+(?:name\s+)?(?:is|are)\s+|"
    r"(?:my\s+)?(?:guests|guest|friends|friend|plus[- ]?ones?)(?:'s)?\s*(?:name\s*)?(?:is|are|names?\s+(?:is|are))?\s*|"
    r"(?:their|his|her|the)\s+names?\s+(?:is|are)\s+|"
    r"(?:it'?s|it is|this is|that is|that's|they are|they're|he is|she is|name is|names are|i am bringing|i'll bring|i will bring|bringing)\s+)",
    re.I)
STOP_NAME_WORDS = {"yes", "no", "sorry", "okay", "ok", "hmm", "please", "thanks", "thank", "you", "none", "nobody",
                   "myself", "me", "i", "dont", "don't", "not", "sure", "repeat", "what", "again"}


def _text(row):
    return str(row.get("en_text") or row.get("text") or row.get("content") or "").strip()


def _is_agent(row):
    return str(row.get("role", "")).lower() in {"agent", "assistant", "bot", "ai"}


def _yes_no(answer):
    """Return 'Yes', 'No', 'Maybe' or ''."""
    if MAYBE.search(answer):
        return "Maybe"
    has_yes, has_no = bool(YES.search(answer)), bool(NO.search(answer))
    if has_no and not has_yes:
        return "No"
    if has_yes and not has_no:
        return "Yes"
    if has_yes and has_no:  # e.g. "yes, I can't wait" vs "no, I can't" -> first keyword wins
        return "Yes" if YES.search(answer).start() < NO.search(answer).start() else "No"
    return ""


def _count(answer):
    m = re.search(r"\b(\d{1,2})\b", answer)
    if m:
        return int(m.group(1))
    low = answer.lower()
    if re.search(r"\b(just me|only me|myself|alone|by myself|no guests?|no one|nobody|none)\b", low):
        return 0
    for word in re.findall(r"[a-z]+", low):
        if word in NUMBER_WORDS and word not in {"a", "no"}:
            return NUMBER_WORDS[word]
    if re.search(r"\b(a|one)\s+(guest|friend|person)\b", low):
        return 1
    return None


def _names(answer):
    text = re.sub(r"[.!?]+$", "", answer.strip())
    text = NAME_LEAD.sub("", text).strip()
    parts = re.split(r",|\band\b|&|\bplus\b", text, flags=re.I)
    names = []
    for part in parts:
        words = [w for w in re.findall(r"[A-Za-z\u0B80-\u0BFF\u0900-\u097F][A-Za-z'\u0B80-\u0BFF\u0900-\u097F.-]*", part)]
        if not words or len(words) > 3:
            continue
        if any(w.lower() in STOP_NAME_WORDS for w in words):
            continue
        names.append(" ".join(w.capitalize() if w.islower() else w for w in words))
    return names


def extract_rsvp_from_transcript(transcript):
    """Return {"rsvp": "Yes"|"No"|"Maybe"|"", "guests": [names], "guest_count": int|None, "confident": bool}."""
    rows = [r for r in (transcript or []) if isinstance(r, dict) and _text(r)]
    rsvp, count, guests = "", None, []
    state = None  # what the agent last asked: attend | count | names | confirm
    for row in rows:
        text = _text(row)
        if _is_agent(row):
            if CONFIRM_Q.search(text) and not ATTEND_Q.search(text):
                state = "confirm"
            elif ATTEND_Q.search(text) and not rsvp:
                state = "attend"
            elif GUEST_NAME_Q.search(text) and re.search(r"guest|friend|first|second|third|their|his|her|next", text, re.I) \
                    and "?" in text:
                state = "names"
            elif GUEST_COUNT_Q.search(text) and "?" in text:
                state = "count"
            continue
        # user turn
        if state == "attend" and not rsvp:
            rsvp = _yes_no(text) or rsvp
            if rsvp in {"No", "Maybe"}:
                break
            # "Yes, and I'm bringing two guests" in one breath
            c = _count(text) if re.search(r"guest|friend|bring", text, re.I) else None
            if c is not None:
                count = c
            state = None
        elif state == "count" and rsvp == "Yes":
            c = _count(text)
            if c is not None:
                count = c
                if c == 0:
                    guests = []
            inline = _names(text) if re.search(r"[A-Z][a-z]+", text) and c is None else []
            guests.extend(n for n in inline if n not in guests)
        elif state == "names" and rsvp == "Yes":
            for n in _names(text):
                if n not in guests:
                    guests.append(n)
        # "confirm" replies ("Yes, that's correct") change nothing
    if rsvp == "Yes" and count is not None and count > 0:
        guests = guests[:count]
    if rsvp != "Yes":
        guests = []
    confident = bool(rsvp) and (rsvp != "Yes" or count is not None)
    return {"rsvp": rsvp, "guests": guests, "guest_count": count, "confident": confident}
