"""Read contacts (name + phone) from almost any file, and validate one contact typed by hand.

Supported: .csv .tsv .txt .tab .xlsx .xlsm .xltx .xls .ods .json .jsonl .vcf .docx .pdf .html .htm .xml .md
(anything else is tried as plain text). Header names are matched loosely (name / full name / contact,
phone / mobile / whatsapp / number ...); with no header the first phone-looking cell on each row is the
phone and the nearest text cell is the name.

Phones are normalised to E.164 (+919876543210) because Sarvam requires that format. A 10 digit number
gets DEFAULT_COUNTRY_CODE (default 91) in front; 00xx.. becomes +xx..
"""
import csv
import io
import json
import os
import re
import zipfile

DEFAULT_COUNTRY_CODE = re.sub(r"\D", "", os.getenv("DEFAULT_COUNTRY_CODE", "91")) or "91"

NAME_HEADERS = {
    "name", "full name", "fullname", "contact", "contact name", "guest", "guest name", "invitee",
    "first name", "firstname", "first_name", "person", "customer", "student", "member", "attendee",
}
PHONE_HEADERS = {
    "phone", "phone number", "phonenumber", "phone_number", "mobile", "mobile number", "mobile no",
    "mobile no.", "mob", "cell", "cell phone", "cellphone", "contact number", "contact no", "number",
    "whatsapp", "whatsapp number", "tel", "telephone", "ph", "ph no", "ph. no", "phone no", "phone no.",
}
LAST_NAME_HEADERS = {"last name", "lastname", "last_name", "surname", "family name"}

PHONE_IN_TEXT = re.compile(r"(?<![\w.])(\+?\d[\d\s().-]{6,17}\d)(?![\w])")


class ImportError_(ValueError):
    """Raised with a message that is safe to show to the user."""


def normalize_phone(value):
    """Return (+E164, "") or ("", reason)."""
    raw = str(value if value is not None else "").strip()
    if not raw:
        return "", "phone is empty"
    # Excel turns long numbers into floats such as 9.19876543210E+11 or 919876543210.0
    if re.fullmatch(r"\d+(\.\d+)?[eE]\+?\d+", raw):
        try:
            raw = str(int(float(raw)))
        except (ValueError, OverflowError):
            pass
    elif re.fullmatch(r"\d+\.0+", raw):
        raw = raw.split(".")[0]
    plus = raw.lstrip().startswith("+")
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("00") and not plus:
        digits, plus = digits[2:], True
    if not digits:
        return "", "phone has no digits"
    if plus:
        pass
    elif len(digits) == 10:
        digits = DEFAULT_COUNTRY_CODE + digits
    elif len(digits) == 11 and digits.startswith("0"):
        digits = DEFAULT_COUNTRY_CODE + digits[1:]
    elif len(digits) in (12, 13) and digits.startswith(DEFAULT_COUNTRY_CODE):
        pass
    elif 8 <= len(digits) <= 15:
        pass
    else:
        return "", "phone must have 8-15 digits"
    value = "+" + digits
    if not re.fullmatch(r"\+[1-9]\d{7,14}", value):
        return "", "phone is not a valid international number"
    return value, ""


def clean_name(value):
    text = re.sub(r"\s+", " ", str(value if value is not None else "")).strip(" \t\r\n,;:|-")
    return text[:120]


def validate_contact(name, phone):
    """Used by the manual Add form. Returns (name, e164) or raises ImportError_."""
    name = clean_name(name)
    if not name:
        raise ImportError_("Enter a name.")
    if len(re.sub(r"\D", "", name)) >= 7:
        raise ImportError_("The name looks like a phone number - put the number in the Phone box.")
    e164, why = normalize_phone(phone)
    if not e164:
        raise ImportError_(f"Phone: {why}. Example: +919876543210 (a 10 digit Indian number also works).")
    return name, e164


def _norm_header(value):
    return re.sub(r"[\s_]+", " ", str(value or "").strip().lower())


def _looks_like_phone(value):
    text = str(value if value is not None else "").strip()
    if not text or len(text) > 25:
        return False
    if re.search(r"[A-Za-z]{2,}", text) and not re.fullmatch(r"\d+(\.\d+)?[eE]\+?\d+", text):
        return False
    digits = re.sub(r"\D", "", text)
    return 8 <= len(digits) <= 15 and len(re.sub(r"[\d\s+().\-eE]", "", text)) == 0


def _rows_to_contacts(rows):
    """rows: list of lists of cell values. Returns (contacts, skipped)."""
    rows = [[("" if c is None else c) for c in r] for r in rows]
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        return [], []

    # --- find a header row among the first few rows -------------------------------------------
    header_idx, name_cols, phone_cols, last_cols = None, [], [], []
    for i, row in enumerate(rows[:10]):
        heads = [_norm_header(c) for c in row]
        n = [j for j, h in enumerate(heads) if h in NAME_HEADERS]
        p = [j for j, h in enumerate(heads) if h in PHONE_HEADERS]
        if not p:  # looser match: "Mobile (primary)" etc.
            p = [j for j, h in enumerate(heads) if re.search(r"\b(phone|mobile|whatsapp|cell|tel)\b", h)]
        if not n:
            n = [j for j, h in enumerate(heads) if re.search(r"\bname\b", h) and j not in p and h not in LAST_NAME_HEADERS]
        if p and (n or len(row) >= 1):
            header_idx, name_cols, phone_cols = i, n, p
            last_cols = [j for j, h in enumerate(heads) if h in LAST_NAME_HEADERS]
            break

    contacts, skipped = [], []

    if header_idx is not None:
        for row in rows[header_idx + 1:]:
            def cell(j):
                return row[j] if j < len(row) else ""
            phone_raw = next((cell(j) for j in phone_cols if str(cell(j)).strip()), "")
            name_parts = [clean_name(cell(j)) for j in name_cols[:1]] if name_cols else []
            if name_cols and last_cols and name_parts and "first" in _norm_header(rows[header_idx][name_cols[0]]):
                name_parts += [clean_name(cell(j)) for j in last_cols[:1]]
            name = clean_name(" ".join(x for x in name_parts if x))
            if not name and not name_cols:
                # no name column: take the first text cell that isn't the phone
                name = next((clean_name(c) for c in row if str(c).strip() and not _looks_like_phone(c)), "")
            if not str(phone_raw).strip() and not name:
                continue
            _add(contacts, skipped, name, phone_raw)
        return contacts, skipped

    # --- no header: look at each row for a phone-like cell ------------------------------------
    for row in rows:
        cells = [str(c).strip() for c in row if str(c).strip()]
        phone_cell = next((c for c in cells if _looks_like_phone(c)), None)
        if phone_cell is None:
            joined = " ".join(cells)
            m = PHONE_IN_TEXT.search(joined)
            if not m:
                continue
            phone_cell = m.group(1)
            rest = (joined[:m.start()] + " " + joined[m.end():])
            name = clean_name(rest)
        else:
            name = clean_name(next((c for c in cells if c != phone_cell and not _looks_like_phone(c)), ""))
        _add(contacts, skipped, name, phone_cell)
    return contacts, skipped


def _add(contacts, skipped, name, phone_raw):
    e164, why = normalize_phone(phone_raw)
    if not e164:
        skipped.append(f"{name or '(no name)'}: {why}")
        return
    if not name:
        skipped.append(f"{e164}: name is missing")
        return
    contacts.append({"name": name, "phone": e164})


# ------------------------------------------------------------------ readers per file type

def _decode(raw):
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            if enc == "utf-16" and "\x00" in text:
                continue
            return text
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="ignore")


def _delimited_rows(text):
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
        if sample.count("\t") > sample.count(","):
            dialect = csv.excel_tab
    return list(csv.reader(io.StringIO(text), dialect))


def _read_xlsx(raw):
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    rows = []
    for ws in wb.worksheets:
        for r in ws.iter_rows(values_only=True):
            rows.append(list(r))
    return rows


def _read_xls(raw):
    try:
        import xlrd  # optional dependency
    except ImportError:
        raise ImportError_("Old .xls files need the 'xlrd' package (pip install xlrd). "
                           "Or open the file in Excel and Save As .xlsx / .csv.")
    wb = xlrd.open_workbook(file_contents=raw)
    rows = []
    for sh in wb.sheets():
        for i in range(sh.nrows):
            rows.append([sh.cell_value(i, j) for j in range(sh.ncols)])
    return rows


def _read_ods(raw):
    try:
        from odf.opendocument import load
        from odf.table import Table, TableRow, TableCell
        from odf import teletype
    except ImportError:
        raise ImportError_("OpenDocument (.ods) files need the 'odfpy' package (pip install odfpy).")
    doc = load(io.BytesIO(raw))
    rows = []
    for table in doc.spreadsheet.getElementsByType(Table):
        for tr in table.getElementsByType(TableRow):
            cells = []
            for tc in tr.getElementsByType(TableCell):
                repeat = int(tc.getAttribute("numbercolumnsrepeated") or 1)
                text = teletype.extractText(tc)
                cells.extend([text] * min(repeat, 50))
            rows.append(cells)
    return rows


def _flatten_json(obj):
    if isinstance(obj, dict):
        for key in ("contacts", "data", "rows", "items", "guests", "records", "people"):
            if isinstance(obj.get(key), list):
                return _flatten_json(obj[key])
        # {"Raja": "+91...", ...}
        if obj and all(isinstance(v, (str, int)) for v in obj.values()):
            if all(_looks_like_phone(v) for v in obj.values()):
                return [["name", "phone"]] + [[k, v] for k, v in obj.items()]
        return _flatten_json([obj])
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            keys = []
            for x in obj:
                for k in x:
                    if k not in keys:
                        keys.append(k)
            return [keys] + [[x.get(k, "") if not isinstance(x.get(k), (dict, list)) else "" for k in keys] for x in obj]
        if obj and all(isinstance(x, (list, tuple)) for x in obj):
            return [list(x) for x in obj]
        return [[str(x)] for x in obj]
    return [[str(obj)]]


def _read_json(raw):
    text = _decode(raw).strip()
    try:
        return _flatten_json(json.loads(text))
    except json.JSONDecodeError:
        items = []
        for line in text.splitlines():  # JSON Lines
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    raise ImportError_("The JSON file could not be read.")
        return _flatten_json(items)


def _read_vcf(raw):
    text = _decode(raw)
    rows = []
    for card in re.split(r"BEGIN:VCARD", text, flags=re.I)[1:]:
        fn = re.search(r"^FN[^:]*:(.*)$", card, re.M | re.I)
        n = re.search(r"^N[^:]*:(.*)$", card, re.M | re.I)
        name = fn.group(1).strip() if fn else " ".join(p for p in reversed((n.group(1) if n else "").split(";")[:2]) if p).strip()
        for t in re.findall(r"^TEL[^:]*:(.*)$", card, re.M | re.I):
            rows.append([name, t.strip()])
    return [["name", "phone"]] + rows


def _read_docx(raw):
    try:
        import docx
    except ImportError:
        docx = None
    rows = []
    if docx is not None:
        d = docx.Document(io.BytesIO(raw))
        for t in d.tables:
            for r in t.rows:
                rows.append([c.text.strip() for c in r.cells])
        for p in d.paragraphs:
            if p.text.strip():
                rows.append(re.split(r"\t|\s{2,}|\s[-–:,|]\s", p.text.strip()))
        return rows
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
    for para in re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S):
        text = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", para, flags=re.S))
        if text.strip():
            rows.append(re.split(r"\s{2,}|\s[-–:,|]\s", text.strip()))
    return rows


def _read_pdf(raw):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(raw))
    rows = []
    for page in reader.pages:
        for line in (page.extract_text() or "").splitlines():
            if line.strip():
                rows.append(re.split(r"\t|\s{2,}|\s[-–:,|]\s", line.strip()))
    if not rows:
        raise ImportError_("This PDF has no selectable text (it may be a scan). Export it as CSV/Excel instead.")
    return rows


def _read_markup(raw):
    text = _decode(raw)
    rows = []
    # HTML tables first
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.S | re.I):
        cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, flags=re.S | re.I)
        rows.append([re.sub(r"<[^>]+>", "", c).strip() for c in cells])
    if rows:
        return rows
    plain = re.sub(r"<[^>]+>", "\n", text)
    for line in plain.splitlines():
        if line.strip():
            rows.append(re.split(r"\t|\s{2,}|\s[-–:,|]\s", line.strip()))
    return rows


def _read_text(raw):
    text = _decode(raw)
    # "Raja, +919876543210" lines, tab/semicolon separated, or a real CSV with a header
    return _delimited_rows(text)


def parse_contacts(filename, raw):
    """Return {"contacts": [...], "skipped": [...], "format": str}. Raises ImportError_ on failure."""
    name = (filename or "").lower().strip()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""

    if raw[:4] == b"PK\x03\x04" and ext not in {"docx", "xlsx", "xlsm", "xltx", "ods"}:
        # wrong/missing extension: look inside the zip
        try:
            names = zipfile.ZipFile(io.BytesIO(raw)).namelist()
        except zipfile.BadZipFile:
            names = []
        ext = "docx" if "word/document.xml" in names else "xlsx" if any(n.startswith("xl/") for n in names) \
            else "ods" if "content.xml" in names else ext
    elif raw[:5] == b"%PDF-":
        ext = "pdf"
    elif raw[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" and ext not in {"xls"}:
        ext = "xls"

    try:
        if ext in {"xlsx", "xlsm", "xltx", "xltm"}:
            rows, fmt = _read_xlsx(raw), "Excel"
        elif ext == "xls":
            rows, fmt = _read_xls(raw), "Excel (.xls)"
        elif ext in {"ods", "fods"}:
            rows, fmt = _read_ods(raw), "OpenDocument spreadsheet"
        elif ext in {"json", "jsonl", "ndjson"}:
            rows, fmt = _read_json(raw), "JSON"
        elif ext in {"vcf", "vcard"}:
            rows, fmt = _read_vcf(raw), "vCard"
        elif ext == "docx":
            rows, fmt = _read_docx(raw), "Word"
        elif ext == "pdf":
            rows, fmt = _read_pdf(raw), "PDF"
        elif ext in {"html", "htm", "xml"}:
            rows, fmt = _read_markup(raw), ext.upper()
        else:
            rows, fmt = _read_text(raw), "text/CSV"
    except ImportError_:
        raise
    except zipfile.BadZipFile:
        raise ImportError_("The file looks damaged or is not a real spreadsheet/document.")
    except Exception as exc:  # unreadable file: say so plainly
        raise ImportError_(f"Could not read this {ext or 'file'}: {type(exc).__name__}.")

    contacts, skipped = _rows_to_contacts(rows)
    if not contacts:
        hint = "I could not find any name + phone pairs. Make sure the file has a phone column (e.g. 'phone' or 'mobile') and a name column."
        if skipped:
            hint += " Problems found: " + "; ".join(skipped[:3])
        raise ImportError_(hint)
    return {"contacts": contacts, "skipped": skipped, "format": fmt}
