#!/usr/bin/env python3
"""
scrub.py -- remove residual personal details from FDA free text before release.

FDA redacts MAUDE narratives (the "(b)(6)" marks), but a spot-check of 69,521
published narratives (2026-10-10) still found patients' dates of birth, medical
record / patient ID numbers, and named contacts with phone numbers, mostly in older
reports. scrub_text() replaces each with a bracketed marker that says what was
removed, so the narrative stays readable and the removal stays visible:

    "PT DOB 3/4/1950, MRN 1234567. CALL JANE DOE AT 555-123-4567."
 -> "PT DOB [DOB REMOVED], MRN [ID REMOVED]. CALL [NAME REMOVED] AT [PHONE REMOVED]."

Rules (deliberately conservative; device facts are never touched on purpose):
  email           any address                                  -> [EMAIL REMOVED]
  phone           North-American numbers with separators, not  -> [PHONE REMOVED]
                  preceded by LOT/REF/CAT/PART/MODEL/SN/SERIAL
  SSN             ddd-dd-dddd                                   -> [ID REMOVED]
  date of birth   DOB / D.O.B. / DATE OF BIRTH / BIRTH DATE      -> <label> [DOB REMOVED]
                  followed by a date or a year
  record / ID no. MR / MRN / MEDICAL RECORD (NUMBER|NO|#) /      -> <label> [ID REMOVED]
                  PT ID / PATIENT ID / PATIENT # / ACCOUNT (NUMBER|#)
                  followed by a token containing a digit
  contact name    "CONTACT|CALL|REACH <Name ...> AT|ON|VIA"      -> CONTACT [NAME REMOVED] AT
Returns (clean_text, Counter of rule hits).
"""
import collections
import re

_DATE = r"(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2}|\d{4}|" \
        r"(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\.?\s+\d{1,2},?\s+\d{2,4}|#{2,}[/#-]*)"
RULES = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.I), lambda m: "[EMAIL REMOVED]"),
    ("ssn", re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])"), lambda m: "[ID REMOVED]"),
    ("phone", re.compile(
        r"(?<![\w-])(?:\+?1[-. ]?)?(?:\(\d{3}\)\s?|\b\d{3}[-. ])\d{3}[-. ]\d{4}\b(?:\s*(?:EXT|X)\.?\s*\d{1,5})?", re.I),
     lambda m: "[PHONE REMOVED]"),
    ("dob", re.compile(r"\b(D\.?O\.?B\.?|DATE\s+OF\s+BIRTH|BIRTH\s*DATE)(\s*(?:IS|OF|WAS)?\s*[:#-]?\s*)" + _DATE, re.I),
     lambda m: f"{m.group(1)}{m.group(2) or ' '}[DOB REMOVED]"),
    ("record_id", re.compile(
        r"\b(MRN|MR|MEDICAL\s+RECORD(?:\s+(?:NUMBER|NO\.?|#))?|PT\.?\s+ID|PATIENT\s+(?:ID|#|NUMBER)|"
        r"ACCOUNT\s+(?:NUMBER|NO\.?|#))(\s*[:#]?\s*)"
        r"(?:(?=[A-Z0-9-]*\d)[A-Z0-9-]{4,}|\d{1,3}\s+\d{3,})(?:\s+\d{3,})*\b", re.I),
     lambda m: f"{m.group(1)}{m.group(2) or ' '}[ID REMOVED]"),
    ("contact_name", re.compile(
        r"\b(CONTACT|CALL|REACH)\s+((?:(?:DR|MR|MS|MRS)\.?\s+)?[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,2})\s+(AT|ON|VIA)\b"),
     lambda m: f"{m.group(1)} [NAME REMOVED] {m.group(3)}"),
]
_DEVICE_LABEL = re.compile(r"\b(LOT|REF|CAT|CATALOG|PART|P/N|PN|MODEL|SN|S/N|SERIAL|UDI|DI)\b\W{0,4}$", re.I)
_NOT_NAMES = {"ME", "US", "THE", "OUR", "YOUR", "CUSTOMER", "TECHNICAL", "SUPPORT", "SERVICE", "FDA", "MANUFACTURER"}


def scrub_text(text):
    hits = collections.Counter()
    if not text:
        return text, hits
    for name, rx, repl in RULES:
        def _sub(m, name=name, repl=repl):
            if name == "contact_name" and m.group(2).split()[0].upper().strip(".") in _NOT_NAMES:
                return m.group(0)
            if name == "phone" and _DEVICE_LABEL.search(m.string[max(0, m.start() - 12):m.start()]):
                return m.group(0)                     # a lot / catalog / model number, not a phone
            hits[name] += 1
            return repl(m)
        text = rx.sub(_sub, text)
    return text, hits


def _selftest():
    cases = [
        ("PT DOB 3/4/1950, MRN 1234567. CALL JANE DOE AT 555-123-4567.",
         "PT DOB [DOB REMOVED], MRN [ID REMOVED]. CALL [NAME REMOVED] AT [PHONE REMOVED]."),
        ("PLEASE CONTACT CATHY JOHNSON AT 1-800-555-0199. THANK YOU",
         "PLEASE CONTACT [NAME REMOVED] AT [PHONE REMOVED]. THANK YOU"),
        ("PATIENT'S DATE OF BIRTH: 12/1/45 AND WEIGHS 180 LBS.", "PATIENT'S DATE OF BIRTH: [DOB REMOVED] AND WEIGHS 180 LBS."),
        ("DAUGHTER, (DOB 1987) WAS DIAGNOSED", "DAUGHTER, (DOB [DOB REMOVED]) WAS DIAGNOSED"),
        ("QUESTIONS, PLEASE CONTACT ME DIRECTLY AT: (555) 123-4567.",
         "QUESTIONS, PLEASE CONTACT ME DIRECTLY AT: [PHONE REMOVED]."),
        ("EMAIL jdoe@example.org FOR INFO", "EMAIL [EMAIL REMOVED] FOR INFO"),
        ("PT ID 4 123456, DOB 5/12/60, FEMALE", "PT ID [ID REMOVED], DOB [DOB REMOVED], FEMALE"),
        ("EAB, MR 1234567, DOB 1-02-1950, FEMALE", "EAB, MR [ID REMOVED], DOB [DOB REMOVED], FEMALE"),
    ]
    for raw, want in cases:
        got, _ = scrub_text(raw)
        assert got == want, f"\n raw: {raw}\n got: {got}\nwant: {want}"
    # device facts must survive
    keep = ["LOT 123-456-7890 WAS RECALLED", "REF 555-123-4567", "MODEL 100-200-3000 PUMP",
            "PATIENT WEIGHT, AGE AND DATE OF BIRTH NOT PROVIDED", "THE PATIENT'S WEIGHT AND DATE OF BIRTH. THE PATIENT",
            "CALL THE MANUFACTURER AT ONCE", "MR SCAN AT 1.5T", "MR 3T SCANNER", "PATIENT # 2 OF 3", "K123456 CLEARED 2019", "UDI 00812345678901"]
    for s in keep:
        got, hits = scrub_text(s)
        assert got == s, f"over-scrubbed: {s!r} -> {got!r} {dict(hits)}"
    print(f"scrub.py self-test PASSED ({len(cases)} removals, {len(keep)} keeps).")


if __name__ == "__main__":
    _selftest()
