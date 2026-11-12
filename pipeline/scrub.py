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
                  followed by a phone or e-mail (any letter case)
Added 2026-11-03 (checked on a 200,000-narrative MAUDE sample):
  labelled phone  PHONE / TEL / CELL / FAX ... followed by >= 7   -> <label> [PHONE REMOVED]
                  digits in any layout (unlabelled numbers keep the rule above)
  record / ID no. also MED REC #, CHART #, HOSPITAL ID / NUMBER
  date of birth   also "BORN (ON|IN) <date or year>"
  labelled name   PATIENT / PT / REPORTER / PHYSICIAN ... NAME     -> <label> [NAME REMOVED]
                  (IS|:|-) followed by a name
  title + name    DR / DOCTOR / MR / MRS / MS / MISS / NURSE /      -> DR. [NAME REMOVED]
                  PROF followed by up to three name tokens (or
                  initials); a token in _STOP (common words that
                  follow these titles in MAUDE: STATED, WAS,
                  PRACTITIONER, CONDITIONAL, SCORE ...) ends the
                  name, so "DR. STATED" and "MR CONDITIONAL" stay
  initials        PT / PATIENT / REPORTER INITIALS (ARE|:) J.D.   -> <label> [INITIALS REMOVED]
  age over 89     "92 YEAR OLD", "92 YO", "AGE: 92", "AGED 92"     -> [AGE OVER 89] YEAR OLD
                  (90 to 129; HIPAA safe harbor groups these)
  street address  number + 1-3 words + STREET/ST/AVENUE/AVE/ROAD/  -> [ADDRESS REMOVED]
                  RD/BLVD/LANE/DRIVE/DR/HIGHWAY/PARKWAY/...
                  ending the phrase (comma, period, SUITE, APT ...)
Added 2026-11-04 (after the leak check over all 59,190,550 MAUDE narratives still found
dates after DOB wording, unlabelled phone- and SSN-shaped numbers, and names after titles):
  birth date      any date (or a year 1800-2099) after DOB / D.O.B. /  -> <label> [DOB REMOVED]
                  DATE OF BIRTH / BIRTH DATE / BIRTHDATE / BIRTHDAY /
                  BORN / BIRTH, with up to 8 non-word characters and up
                  to two of IS / WAS / ON / IN / OF / ABOUT ... between
  number shape    any US-phone-shaped (ddd-ddd-dddd, (ddd) ddd-dddd,  -> [NUMBER REMOVED]
                  mixed separators) or SSN-shaped (ddd-dd-dddd) number
                  left by the rules above, unless it is glued to a letter
                  ("D123-45-6789") or follows an explicit device / part /
                  report label (LOT, REF, CAT, CATALOG, PART, P/N, MODEL,
                  SN, SERIAL, UDI, DI, REPORT, MDR, COMPLAINT, PRODUCT,
                  ITEM, BATCH, CODE, K-number, optionally + NUMBER/NO/#).
                  Deliberately over-removes: a second number in a list
                  ("LOT 123-45-6789, 123-45-6790") is removed too.
  title + name    a word after a title is now a name unless it is in _STOP or
                  in scrub_verbs_1..4.txt (dictionary words ending in ED / ING / LY);
                  before, ANY 6+ letter word with those endings was taken for a
                  verb, which let surnames such as BRADLEY or KEATING through.
                  Also: a dotted title before a digit-preceded MR ("3 MR. X")
                  is no longer skipped, and English words built from a prefix
                  and a stop word (REINSERTED, UNHOOKED) are not names.
Added 2026-11-08:
  email (all MAUDE text)  scrub_email(): any address (superset of the  -> [EMAIL REMOVED]
                  rule above and of the release check's pattern), for
                  every MAUDE text literal (event.py structured fields
                  too, not only the narratives); scrub_text() starts with it
Returns (clean_text, Counter of rule hits).
"""
import collections
import re

_MON = r"(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\.?"
_DATE = r"(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2}|" \
        r"\d{1,2}\s*[/-]?\s*" + _MON + r"\s*[/-]?\s*\d{2,4}|" + _MON + r"\s+\d{1,2},?\s+\d{2,4}|" \
        r"\d{1,2}/\d{4}|\d{4}|#{2,}[/#-]*)"
RULES = [
    # domain labels must contain a letter and end in a letter TLD ("5V@0.5MS" is a pacing threshold)
    ("email", re.compile(r"[\w.+-]+@(?:[\w-]+\.)+[A-Za-z]{2,}(?![A-Za-z])"), lambda m: "[EMAIL REMOVED]"),
    # SSN only when labelled: unlabelled ddd-dd-dddd in MAUDE are part / REF numbers
    ("ssn", re.compile(r"\b(SSN|SOCIAL\s+SECURITY(?:\s+(?:NUMBER|NO\.?|#))?)(\W{0,4})\d{3}-\d{2}-\d{4}\b", re.I),
     lambda m: f"{m.group(1)}{m.group(2) or ' '}[ID REMOVED]"),
    # (ddd) ddd-dddd, or ddd-ddd-dddd / ddd.ddd.dddd with the SAME separator twice
    # (rejects journal citations such as "12(3):456-789 2020")
    ("phone", re.compile(
        r"(?<![\w:-])(?:\+?1[-. ]?)?(?:\(\d{3}\)\s?\d{3}[-. ]\d{4}|\b\d{3}([-. ])\d{3}\1\d{4})\b"
        r"(?:\s*(?:EXT|X)\.?\s*\d{1,5})?", re.I),
     lambda m: "[PHONE REMOVED]"),
    ("dob", re.compile(r"\b(D\.?O\.?B\.?|DATE\s+OF\s+BIRTH|BIRTH\s*DATE)(\s*(?:IS|OF|WAS)?\s*[^\w\s]{0,2}\s*)" + _DATE
                       + r"(?![\d.]*\s*(?:\+/-|±))", re.I),
     lambda m: f"{m.group(1)}{m.group(2) or ' '}[DOB REMOVED]"),
    ("record_id", re.compile(
        r"\b(MRN|MR|MEDICAL\s+RECORD(?:\s+(?:NUMBER|NO\.?|#))?|PT\.?\s+ID|PATIENT\s+(?:ID|#|NUMBER)|"
        r"ACCOUNT\s+(?:NUMBER|NO\.?|#))(\s*[:#]\s*|\s+)"
        r"(?!\d{7,10}-\d{4}-\d{5})"          # an MDR report number (public), not a medical record
        r"(?:(?=[A-Z0-9-]*\d)[A-Z0-9-]{4,}|\d{1,3}\s+\d{3,})(?:\s+\d{3,})*\b", re.I),
     lambda m: f"{m.group(1)}{m.group(2) or ' '}[ID REMOVED]"),
    # a name only when it is followed by the contact detail itself (phone or e-mail):
    # "CONTACT JANE DOE AT 555-..." -- in upper-case MAUDE text a bare "CONTACT X AT"
    # is usually ordinary prose ("CONTACT WITH CUSTOMER AT THIS TIME")
    ("contact_name", re.compile(
        r"\b((?i:CONTACT|CALL|REACH))\s+((?:(?i:DR|MR|MS|MRS)\.?\s+)?[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,2})"
        r"\s+((?i:AT|ON|VIA))\b(?=\s*:?\s*(?:\[PHONE REMOVED\]|\[EMAIL REMOVED\]))"),
     lambda m: f"{m.group(1)} [NAME REMOVED] {m.group(3)}"),
]
# ---- rules added 2026-11-03 -------------------------------------------------------------
# a phone number after an explicit label, in any digit layout ("PH" alone is pH, not phone)
_PHONE_LABEL = re.compile(
    r"\b((?i:TELEPHONE|PHONE|TEL|CELL|MOBILE|FAX|PH\s*#|CONTACT\s+(?:NUMBER|NO\.?|#)))"
    r"(\s*(?i:NUMBER|NO\.?|#)?\s*(?i:IS|WAS|:|-|=)?\s*)"
    r"(\+?\(?\d[\d ().-]{5,}\d)(?:\s*(?i:EXT|X)\.?\s*\d{1,5})?(?!\d)")
_RECORD_EXTRA = re.compile(
    r"\b((?i:MED(?:ICAL)?\.?\s+REC(?:ORD)?S?\.?\s*(?:NUMBER|NO\.?|#)|CHART\s+(?:NUMBER|NO\.?|#)|"
    r"HOSPITAL\s+(?:ID|NUMBER|NO\.?|#)|PATIENT\s+NO\.?))(\s*[:#]\s*|\s+)"
    r"(?:(?=[A-Za-z0-9-]*\d)[A-Za-z0-9-]{3,})\b")
_BORN = re.compile(r"\b((?i:BORN(?:\s+(?:ON|IN))?))(\s*:?\s*)" + _DATE + r"(?![\d.]*\s*(?:\+/-|±))", re.I)
# a name token: initials, or a word of letters (accented ones too: MÜLLER, JOSÉ) with ' ’ - inside; it may
# be followed by digits or "(" ("DR. ROE(B)(6)") but not by another letter. Before 2026-11-04 only
# A-Z letters counted and a following digit or "(" made the token "not a name".
_UP, _LO = "A-ZÀ-ÖØ-Þ", "a-zß-öø-ÿ"
_NAME_TOKEN = re.compile(r"\s*((?:[A-Z]\.){2,3}|[" + _UP + r"](?:(?:[^\W\d_]|['’-])*[^\W\d_])?\.?)(?![^\W\d_])")
_NAME_TOKEN_LC = re.compile(r"\s*((?:[A-Z]\.){2,3}|[" + _UP + _LO + r"](?:(?:[^\W\d_]|['’-])*[^\W\d_])?\.?)(?![^\W\d_])")
_LABELLED_NAME = re.compile(
    r"\b(?:((?i:PATIENT|PT|REPORTER|PHYSICIAN|DOCTOR|SURGEON|NURSE|USER|CONSUMER|CONTACT|CALLER|SPOUSE|"
    r"PARENT|MOTHER|FATHER|WIFE|HUSBAND|DAUGHTER|SON)(?:'?S)?\s+(?:(?i:FULL|FIRST|LAST)\s+)?(?i:NAME))"
    r"\s*(?i:IS|WAS|:|-|=)|(?i:NAMED))\s*(?=[A-Z])")
# a title glued to "/", "-", "'" or "(" counts too ("W/DR. ROE", "-DR. ROE"; before 2026-11-04 it did not);
# after a dotted title the first name token may be lower case ("per Ms. roe")
_TITLE = re.compile(r"(?<!\w)((?i:DOCTOR|DR|MRS|MR|MS|MISS|NURSE|PROFESSOR|PROF))(\.?)"
                    r"(?:(?=\s+[" + _UP + r"])|(?<=\.)(?=\s+[" + _LO + r"]))")
_INITIALS = re.compile(
    r"\b((?:(?i:PT|PATIENT|PATIENTS|REPORTER|USER|CONSUMER)(?:'?S|S')?\s+(?i:INITIALS?)|(?i:INITIALS))\b)"
    r"(\s*(?i:ARE|IS|WERE|WAS|OF)?\s*[:=(-]?\s*)((?:[A-Z]\.?\s?){1,3}[A-Z]\.?)(?![A-Za-z])")
_AGE_YEARS = re.compile(
    r"(?<![\d.,/])(\d{2,3})(?=\s*-?\s*(?i:YEARS?|YRS?\.?|Y/O|YO|Y\.O\.?)(?![A-Za-z]))")
_AGE_LABEL = re.compile(
    r"\b((?i:AGE[SD]?|AGE\s+AT\s+(?:THE\s+)?TIME\s+OF\s+(?:THE\s+)?EVENT))(\s*(?i:OF|IS|WAS|:|=|-)?\s*)(\d{2,3})"
    r"(?!\d|[.,]\d|\s*-?\s*(?i:DAYS?|MONTHS?|MOS?|WEEKS?|WKS?|HOURS?|HRS?|MIN|MINS|MINUTES?|SECONDS?|%|MM|CM|KG|LBS?|PSI)\b)")
# not CT / LN / PL / WAY / PLACE: in MAUDE those are CT scans, lymph nodes and prose ("2 HEAD CT,")
_STREET = (r"STREET|ST|AVENUE|AVE|ROAD|RD|BOULEVARD|BLVD|LANE|DRIVE|DR|HIGHWAY|HWY|PARKWAY|PKWY|"
           r"TERRACE|CIRCLE|COURT|TURNPIKE|TPKE")
_ADDRESS = re.compile(
    r"(?<![\w#/.,:-])(\d{1,6}(?:-\d{1,4})?[A-Z]?)\s+((?:[NSEW]\.?\s+)?(?:[A-Z][A-Za-z'.-]*\s+){1,3})"
    r"(?i:" + _STREET + r")\b\.?"
    r"(?:\s*,?\s*(?i:#|SUITE|STE\.?|APT\.?|APARTMENT|UNIT|BLDG\.?|FLOOR|FL\.?)\s*[\w-]+)?"
    r"(?=\s*[,;)]|\s*\.(?:\s|$)|\s*$|\s+(?i:SUITE|STE|APT|UNIT|#))")
# words that are not names: they follow DR / MR / NURSE ... in MAUDE text (taken from a 200,000
# narrative sample, 2026-11-03) or are common English words and device terms
_STOP = set("""
A ABOUT ABOVE ACCORDING ACROSS ADDITIONALLY ADJUSTED ADMINISTERED ADVANCED ADVICE ADVISED AFTER AFTERWARDS AGAIN
AGREED AIDE ALL ALLEGED ALLEGEDLY ALLEGES ALSO ALTHOUGH AN AND ANESTHESIA ANESTHETIST ANOTHER ANY APPEARS APPLIED
APPOINTMENT APPOINTMENTS APPROVED ARE AS ASKED ASPIRATED ASSESSED ASSISTANT ASSISTED AT ATTEMPTED ATTEMPTING ATTEMPTS
ATTENDING ATTRIBUTED AVERAGE AWARE BALLOON BASELINE BE BECAME BECAUSE BEEN BEFORE BEGAN BELIEVE BELIEVED BELIEVES
BETWEEN BOARD BOTH BROKE BUT BY CALL CALLED CAME CAN CANNOT CARE CASE CATHETER CAUSING CHANGED CHECKED CHOSE CLEANED
CLINIC CLINICAL CLOSED COMMENTED COMMUNICATION COMPATIBLE COMPLAINED COMPLAINS COMPLETED CONCLUDED CONDITIONAL
CONFIRMED CONNECTED CONSOLE CONSULTANT CONSULTED CONTACT CONTACTED CONTINUED COORDINATOR COULD COULDN'T CUSTOMER CUT
DECIDED DECIDES DECLINED DEPLOYED DESCRIBED DEVICE DEVICES DID DIDN'T DIAGNOSE DIAGNOSED DISCARDED DISCONNECTED
DISCOVERED DISCUSSED DOES DOSE DRAINED DUE DURING EACH EDUCATOR ELECTED ENCOUNTERED ENDED ENTERED ENVIRONMENT EVEN EVENT
EXAM EXAMINATION EXAMINATIONS EXAMINED EXPERIENCE EXPERIENCED EXPLAINED FAILED FAMILIAR FELT FEW FILM FINDS FIXED FOLLOW
FOLLOWED FOR FOUND FROM FURTHER GAVE GETTING GOT GRADE GUIDE HAD HAS HAVE HE HEARD HELD HER HIM HIS HIT HOSPITAL HOW
HOWEVER I IDENTIFIED IDENTIFICATION IF IMAGES IMAGING IMMEDIATELY IMPLANTED IN INADVERTENTLY INCISED INCLUDED INCREASED
INDICATE INDICATED INFORMED INFUSION INJECTED INSERTED INSPECTED INSTEAD INSTRUCTED INSTRUMENT INTO IS IT ITS JUST KEPT
KIT KNEW KNOWLEDGE LATER LEFT LENGTH LIFTING LOGGED LOOKED LOST MADE MANAGER MANIPULATION MANUFACTURER MANUFACTURING MAY
MEDICAL MOBILIZED MORE MOST MOVED MUST MY NEVER NO NONE NOT NOTED NOTICED NOTIFIED NOW OBSERVED OBTAINED OCCURRED OF
OFFICE ON ONCE ONE ONLY OPENED OPINED OR ORDERED OTHER OUR OUT PAIN PASSED PATIENT PATIENTS PEELED PELVIC PER PERCEIVED
PERFORMED PERHAPS PHYSICIAN PICKED PLACED PLUS PRACTITIONER PREPARED PRESCRIBED PRESENT PRESSED PRIOR PROCEEDED
PRODUCTION PROMPTLY PROPER PROVIDED PULLED PULSE PULSES PUMP PUNCTURED PUSHED PUT QUESTIONED QUIT RAISED RATHER REACHED
RECAPPED RECEIVED RECOGNIZED RECOMMENDED REDIRECTED REDUCED REDUCTION REFILLED REFUSED REGARDING REMAINED REMAINING
REMOVED REPEATED REPLACED REPORT REPORTED REPORTEDLY REPORTS REQUESTED RESET RESPONDED RESTARTED RESULT RETRACTED
RETRIEVED RETURNED ROTATED SAID SAW SAYS SCAN SCANNER SCORE SCORES SELECTED SENT SERVICE SEVERE SHE SHEARED SHOULD
SHOWED SINCE SITE SO SOME SOMETIME SPECIALIST SPIKED SPRAYED STAFF STANDING STARTED STATED STATES STATION STEM STENT STILL
STOP STOPPED SUCCESSFULLY SUCH SUGGESTED SUPERVISOR SURGEON SURGERY SUSPECTED SUSPECTS SWAPPED SWAPPING SWITCHED SYSTEM
TECHNICAL TESTED THAT THE THEIR THEM THEN THERAPY THERE THEREFORE THESE THEY THIS THOUGHT TIME TO TODAY TOLD TOOK TOO
TREATED TRIED TRIES TROUBLESHOOTING TWO UNABLE UNSAFE UPON US USE USED USER USES USING VERIFIED VIA VISIT VISITS WAITED
WANTED WANTS WAS WE WENT WERE WHAT WHEN WHERE WHICH WHILE WHO WILL WITH WITHDREW WITHOUT WORKED WORKFLOW WOULD WROTE YET
YOU ICON JET SAFE IMAGE MRI RN LPN NP PA CRNA MD DO OR ER ICU ED PROCEDURE EXPLANT EXPLANTED IMPLANT DRESSING LINE
SHIFT TEAM NURSES DOCTORS STUDENT TRAINEE ON-CALL ONCALL CHARGE HEAD FLOOR UNIT STATION'S REMARKED REVIEWED RECEIVING
REPLIED ANSWERED REALIZED NOTES NOTE WRITES INSERTING REMOVING USING PLACING NOTING STATING REPORTING CHECKING
PAGED CHOOSES WITHHELD RAISES URGED OFFICES OPINES LEAVES CALVES INJECTS EXAMINES PUTS HOPED PRICKS TELLS SETUP
LEARNER AIDES KNOWS FEELS THINKS GIVES TAKES ASKS SEES GETS WRITES WANTS NEEDS SAYS CALLS UNDERSTANDS INDICATES
UNKNOWN UNK NA N/A NAME NAMED DIDNT WASN'T WASNT HAVING DOING BEING WHOM WHOSE ALWAYS USUALLY OFTEN SOMETIMES LATER EARLIER
EXAMINE REPOSITIONED RE-POSITIONED REOPERATED PREFORMED PREDILATED RE-SCOPED OVERSEWED RE-TIGHTENED RETIGHTENED
PRETAPPED RE-EXAMINE RE-ADMITTED RE-PUNCTURED RE-PREPPED UNTHREADED UN-SCRUBBED PRELOADED RE-GRASPED RE-BROACHED
RE-COMMENCED REPREPPED
""".split())        # last three lines: prefixed verbs after a title, MAUDE / MedSun leak checks of 2026-11-04
_STOP_CORE = frozenset(_STOP)       # the hand-picked words only (shown as context by the leak checker)
# plus the common-word list next to this file (7,000+ words frequent in MAUDE text; see its header)
_WORDS_FILE = __import__("os").path.join(__import__("os").path.dirname(__file__), "scrub_words.txt")
with open(_WORDS_FILE, encoding="utf-8") as _fh:
    _STOP |= {w for ln in _fh if not ln.startswith("#") for w in ln.split()}
# dictionary verb / adverb forms (ED / ING / LY, 6+ letters), split over scrub_verbs_1.txt ...
# scrub_verbs_4.txt next to this file; see the header of scrub_verbs_1.txt
_VERBS_FILES = sorted(__import__("glob").glob(__import__("os").path.join(
    __import__("os").path.dirname(__file__), "scrub_verbs_[0-9].txt")))
if len(_VERBS_FILES) != 4:
    raise FileNotFoundError(f"scrub.py needs scrub_verbs_1.txt ... scrub_verbs_4.txt, found {_VERBS_FILES}")
_VERBS = set()
for _vf in _VERBS_FILES:
    with open(_vf, encoding="utf-8") as _fh:
        _VERBS |= {w for ln in _fh if not ln.startswith("#") for w in ln.split()}
_STOP_SHORT_INITIALS = {"ON", "OF", "IN", "IS", "AT", "AS", "TO", "NO", "OR", "AN", "BY", "IT", "NOT", "ARE", "WAS",
                        "AND", "THE", "FOR", "UNK", "NA", "WERE", "HAS", "HAD", "BE", "WE", "ALL", "ANY", "SEE", "USE"}
_STREET_DEVICE = {"USB", "FLASH", "DISK", "HARD", "THUMB", "MOTOR", "GEAR", "CD", "DVD", "TAPE", "BELT", "SHAFT", "ONE",
                  "TWO", "THREE", "FOUR", "WAY", "PIN", "LUER", "LEAD", "LEADS", "VALVE", "PORT", "PORTS", "PLACE",
                  "SITE", "SITES", "SIDE", "STOP", "STOPCOCK", "SHEATH", "CATHETER", "TUBE", "TUBING", "WIRE",
                  "NEEDLE", "F", "FR", "FRENCH", "MM", "CM", "ML", "CC", "MG", "UNITS", "LUMEN",
                  # prose words, never in a street name ("REPORTED IN 1999 BY DR. ...", "123 BY ST. JUDE")
                  "THE", "BY", "TO", "OF", "AND", "IN", "AT", "FROM", "WITH", "ON", "FOR", "OR", "AS", "IS", "WAS",
                  "WERE", "ARE", "A", "AN", "PER", "VIA", "HOURS", "DAYS", "MINUTES", "YEARS", "MONTHS", "WEEKS",
                  "TIMES", "PATIENTS", "SEEN", "THEN"}
_DEVICE_LABEL = re.compile(r"\b(LOT|REF|CAT|CATALOG|PART|P/N|PN|MODEL|SN|S/N|SERIAL|UDI|DI)\b\W{0,4}$", re.I)
# ---- rules added 2026-11-04 -------------------------------------------------------------
# any date after birth wording; the year alone only 1800-2099 ("BIRTH WEIGHT 3200 G" stays)
_DATE_ANY = (r"(?:\d{1,2}\s*[/.-]\s*\d{1,2}\s*[/.-]\s*\d{2,4}|\d{4}\s*[/.-]\s*\d{1,2}\s*[/.-]\s*\d{1,2}|"
             r"\d{1,2}(?:ST|ND|RD|TH)?\s*[/.-]?\s*(?:OF\s+)?" + _MON + r"\s*[/.,-]?\s*\d{2,4}|"
             + _MON + r"\s*[/.-]?\s*\d{1,2}(?:ST|ND|RD|TH)?\s*,?\s*\d{2,4}|" + _MON + r"\s*[/.,-]?\s*\d{4}|"
             r"\d{1,2}\s*/\s*\d{4}|(?:1[89]|20)\d{2}|#{2,}[/#-]*)")
_BIRTH_ANY = re.compile(
    r"\b((?i:D\.?\s?O\.?\s?B\.?|DATE\s+OF\s+BIRTH|BIRTH\s*-?\s*DATE|BIRTHDAY|BORN|BIRTH))"
    r"(\W{0,8}(?:(?i:IS|WAS|ON|IN|OF|ABOUT|AROUND|APPROX\.?|APPROXIMATELY|CIRCA|CA\.?)\W{1,8}){0,2})"
    r"(?i:" + _DATE_ANY + r")(?!\d)(?![\d.]*\s*(?:\+/-|±))")
# more dates listed right after a removed birth date ("DOB: (12JAN1950, 03FEB1961)")
_DOB_LIST = re.compile(r"\[DOB REMOVED\]((?:\s*(?:[,;/&]|(?i:AND|OR))\s*(?i:" + _DATE_ANY + r")(?!\d))+)")
# US-phone-shaped and SSN-shaped numbers; kept only when glued to a letter or after a device label
_PHONE_SHAPE = re.compile(r"(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s*|(?<![\d-])\d{3}[\s.-])\d{3}[\s.-]\d{4}(?![\d-])")
_SSN_SHAPE = re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])")
_NUM_KEEP = re.compile(r"\b(?:LOT|REF|CAT|CATALOG|PART|P/N|PN|MODEL|SN|S/N|SERIAL|UDI|DI|REPORT|MDR|COMPLAINT|"
                       r"PRODUCT|ITEM|BATCH|CODE|K\d*)(?:\W{0,3}(?:NUMBER|NO|#))?\W{0,6}$", re.I)
_NUM_PERSONAL = re.compile(r"\b(?:PHONE|TEL|CELL|FAX|CALL|SSN|SOCIAL\s+SECURITY)\w*\W{0,12}$", re.I)
_NUM_GATE = re.compile(r"\d{3}[\s.)-]")


def _number_shapes(text, hits):
    if not _NUM_GATE.search(text):
        return text

    def sub(m):
        before = m.string[max(0, m.start() - 24):m.start()]
        if re.search(r"[A-Za-z]$", before):
            return m.group(0)                          # "D123-45-6789": a letter-prefixed part number
        if not _NUM_PERSONAL.search(before) and _NUM_KEEP.search(before):
            return m.group(0)                          # "LOT 123-45-6789", "REF # 555-123-4567"
        hits["number_shape"] += 1
        return "[NUMBER REMOVED]"
    text = _PHONE_SHAPE.sub(sub, text)
    return _SSN_SHAPE.sub(sub, text)
_NOT_NAMES = {"ME", "US", "THE", "OUR", "YOUR", "CUSTOMER", "TECHNICAL", "SUPPORT", "SERVICE", "FDA", "MANUFACTURER"}


# names that end like a verb or an adverb; any other word of 6+ letters ending in ED / ING / LY
# after a title is a verb ("DR. EXCHANGED THE LEAD"), not a name
_NAMES_LIKE_VERBS = {"ALFRED", "AHMED", "MOHAMMED", "MOHAMED", "MUHAMMED", "MUHAMMAD", "WINIFRED", "MANFRED",
                     "SIEGFRIED", "FLEMING", "MANNING", "HARDING", "IRVING", "EWING", "STERLING", "DOWNING",
                     "GOLDING", "CUSHING", "BROWNING", "KIPLING", "PICKERING", "BUNTING", "WALDING", "BEVERLY",
                     "KIMBERLY", "REILLY", "SHELLY", "O'REILLY", "LILLY", "DARLING", "SPALDING", "REDDING",
                     "HASTING", "HASTINGS", "BILLING", "KESLING", "FIELDING", "ROWLING", "FARLEY", "STANLEY"}
_VERBS -= _NAMES_LIKE_VERBS
_WORD_PREFIX = re.compile(r"^(?:RE-?|UN-?|PRE-?|NON-?|DE-|DIS|MIS|OVER|UNDER|CO-|SELF-|POST-?|SUB|INTER|AUTO-?)")


def _common(core):
    return core in _STOP or core in _VERBS


def _is_name_token(tok):
    core = tok.strip(".'’-").upper()
    if not core or _common(core) or any(ch.isdigit() for ch in core):
        return False
    if core.endswith("'S") and len(core) >= 5 and _common(core[:-2]):
        return False                                   # "NURSE'S" base word; a 1-2 letter base ("DR. A'S") is a name
    p = _WORD_PREFIX.match(core)
    if p and len(core) - p.end() >= 4 and (core[p.end():] in _VERBS or core[p.end():] in _STOP_CORE):
        return False                                   # REINSERTED, UNHOOKED, PRE-FILLED
    return True


def _take_name(text, pos, limit=3, first_lower=False):
    """End offset of up to `limit` name tokens starting at text[pos:] (pos at the whitespace), or None."""
    end, n = None, 0
    while n < limit:
        m = (_NAME_TOKEN_LC if first_lower and n == 0 else _NAME_TOKEN).match(text, pos)
        if not m or not _is_name_token(m.group(1)):
            break
        end = pos = m.end()
        n += 1
    return end


def _sub_names(rx, text, hits, rule, check=None):
    out, last = [], 0
    for m in rx.finditer(text):
        if m.start() < last or (check and not check(m)):
            continue
        end = _take_name(text, m.end(), first_lower=rule == "title_name" and m.group(2) == ".")
        if end is None:
            continue
        out.append(text[last:m.start()] + m.group(0).rstrip() + " [NAME REMOVED]")
        last = end
        hits[rule] += 1
    if not out:
        return text
    return "".join(out) + text[last:]


def _title_ok(m):
    t = m.group(1).upper()
    before = m.string[max(0, m.start() - 6):m.start()].upper()
    if (t == "MS" or (t == "MR" and not m.group(2))) and re.search(r"\d\s*$", before):
        return False                                   # "5 MS PULSE", "1.5 MR" (but "3 MR. ROE" is a name)
    if t == "MISS" and before.endswith("NEAR "):
        return False
    return True


def _extra(text, hits, g=None):
    g = g or collections.defaultdict(lambda: True)

    def count(rule, fn):
        def _f(m):
            r = fn(m)
            if r != m.group(0):
                hits[rule] += 1
            return r
        return _f

    def phone_lbl(m):
        if sum(ch.isdigit() for ch in m.group(3)) < 7:
            return m.group(0)
        return f"{m.group(1)}{m.group(2) or ' '}[PHONE REMOVED]"
    if g["phone"]:
        text = _PHONE_LABEL.sub(count("phone", phone_lbl), text)
    if g["rec"]:
        text = _RECORD_EXTRA.sub(count("record_id", lambda m: f"{m.group(1)}{m.group(2) or ' '}[ID REMOVED]"), text)
    if g["born"]:
        text = _BORN.sub(count("dob", lambda m: f"{m.group(1)}{m.group(2) or ' '}[DOB REMOVED]"), text)
    if g["name"]:
        text = _sub_names(_LABELLED_NAME, text, hits, "labelled_name")
    if g["title"]:
        text = _sub_names(_TITLE, text, hits, "title_name", check=_title_ok)

    def initials(m):
        core = re.sub(r"[.\s]", "", m.group(3))
        if core in _STOP_SHORT_INITIALS:
            return m.group(0)
        tail = " " if m.group(3).endswith(" ") else ""
        return f"{m.group(1)}{m.group(2) or ' '}[INITIALS REMOVED]{tail}"
    if g["init"]:
        text = _INITIALS.sub(count("initials", initials), text)
    if g["age"]:
        text = _AGE_YEARS.sub(count("age_over_89", lambda m: "[AGE OVER 89]" if 90 <= int(m.group(1)) <= 129
                                    else m.group(0)), text)
        text = _AGE_LABEL.sub(count("age_over_89", lambda m: f"{m.group(1)}{m.group(2) or ' '}[AGE OVER 89]"
                                    if 90 <= int(m.group(3)) <= 129 else m.group(0)), text)

    def address(m):
        words = m.group(2).split()
        if any(w.upper().strip(".") in _STREET_DEVICE for w in words):
            return m.group(0)                          # "7 F SHEATH DR.", "1 USB DRIVE."
        return "[ADDRESS REMOVED]"
    if g["addr"]:
        text = _ADDRESS.sub(count("address", address), text)
    return text


# cheap pre-checks (supersets of each rule's trigger) so most narratives skip most regexes;
# they never change a result, only the speed
_GATE = {
    "email": lambda u: "@" in u,
    "ssn": lambda u: "SSN" in u or "SOCIAL" in u,
    "dob": lambda u: "DOB" in u or "D.O" in u or "DO.B" in u or "BIRTH" in u,
    "record_id": lambda u: "MR" in u or "MEDICAL" in u or "PT" in u or "PATIENT" in u or "ACCOUNT" in u,
    "contact_name": lambda u: "[PHONE REMOVED]" in u or "[EMAIL REMOVED]" in u,
}
_AGE_GATE = re.compile(r"(?<!\d)0?(?:9\d|1[0-2]\d)(?!\d)")
_ADDRESS_GATE = re.compile(r"\b(?:" + _STREET + r")\b", re.I)


def _gated_extra(text, hits):
    u = text.upper()
    g = {
        "phone": "PH" in u or "TEL" in u or "CELL" in u or "MOBILE" in u or "FAX" in u or "CONTACT" in u,
        "rec": "REC" in u or "CHART" in u or "HOSPITAL" in u or "PATIENT" in u,
        "born": "BORN" in u,
        "name": "NAME" in u,
        "title": any(t in u for t in ("DR", "MR", "MS", "MISS", "NURSE", "PROF", "DOCTOR")),
        "init": "INITIAL" in u,
        "age": bool(_AGE_GATE.search(text)),
        "addr": bool(_ADDRESS_GATE.search(text)),
    }
    return _extra(text, hits, g)


def scrub_text(text):
    hits = collections.Counter()
    if not text:
        return text, hits
    if "@" in text:                               # e-mail first, so the whole address goes (see scrub_email)
        text, k = scrub_email(text)
        if k:
            hits["email"] += k
    u = text.upper()
    for name, rx, repl in RULES:
        gate = _GATE.get(name)
        if gate and not gate(u if name != "contact_name" else text):
            continue
        def _sub(m, name=name, repl=repl):
            if name == "contact_name" and m.group(2).split()[0].upper().strip(".") in _NOT_NAMES:
                return m.group(0)
            if name == "phone" and _DEVICE_LABEL.search(m.string[max(0, m.start() - 12):m.start()]):
                return m.group(0)                     # a lot / catalog / model number, not a phone
            hits[name] += 1
            return repl(m)
        text = rx.sub(_sub, text)
    text = _gated_extra(text, hits)
    if "BIRTH" in u or "BORN" in u or "DOB" in u or "D.O" in u or "D O B" in u or "DO.B" in u:
        text = _BIRTH_ANY.sub(lambda m: (hits.update(["dob"]), f"{m.group(1)}{m.group(2) or ' '}[DOB REMOVED]")[1],
                              text)
    if "[DOB REMOVED]" in text:
        text = _DOB_LIST.sub(lambda m: (hits.update(["dob"]), "[DOB REMOVED]")[1], text)
    text = _number_shapes(text, hits)
    return text, hits


# ---- added 2026-11-08: e-mail addresses in EVERY MAUDE text literal --------------------------------
# The v0.0.7 check found 74 e-mail addresses in short structured MAUDE fields (manufacturer name,
# catalog / model number, generic name, device name, removal/correction number), which the narrative
# scrub never saw. event.py now passes every MAUDE text value through scrub_email(), and scrub_text()
# starts with it. EMAIL_ANY is a superset of both the narrative e-mail rule in RULES (Unicode word
# characters) and the release check's ASCII pattern EMAIL_CHECK (scripts/scrub_maude_text.py
# LEAKS["email"], the same as battery F7), so anything the check would count is removed, and the whole
# address goes ("J.DOE%QA@..." -> one marker). "5V@0.5MS" (a pacing threshold) is not an address.
EMAIL_CHECK = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
EMAIL_ANY = re.compile(r"[\w.%+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")


def scrub_email(text):
    """-> (text with every e-mail address replaced by [EMAIL REMOVED], number of addresses removed)."""
    if not text or "@" not in text:
        return text, 0
    text, n = EMAIL_ANY.subn("[EMAIL REMOVED]", text)
    return text, n


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
        ("PATIENT SSN: 123-45-6789", "PATIENT SSN: [ID REMOVED]"),
        ("I CALLED THE COMPANY AT 555.123.4567 EXT 22", "I CALLED THE COMPANY AT [PHONE REMOVED]"),
        ("THE FACILITY CONTACTED DAN AT 1 800 555 1234 AND", "THE FACILITY CONTACTED DAN AT [PHONE REMOVED] AND"),
        ("CONSUMER'S DATE OF BIRTH (04/1950) AND THE LOT", "CONSUMER'S DATE OF BIRTH ([DOB REMOVED]) AND THE LOT"),
        ("CHINA. WANGYU@163.COM. [ZHONGHUA", "CHINA. [EMAIL REMOVED]. [ZHONGHUA"),
        ("FEMALE, AGE 41, DOB: 4 SEPT 1975. WHAT", "FEMALE, AGE 41, DOB: [DOB REMOVED]. WHAT"),
        ("DATE OF BIRTH: 12APR1980 (PER MR", "DATE OF BIRTH: [DOB REMOVED] (PER MR"),
        ("FEMALE, DATE OF BIRTH 03/DEC/1950. THE LEAD", "FEMALE, DATE OF BIRTH [DOB REMOVED]. THE LEAD"),
        ("A1: DOB ¿ 1960; G1: FOREIGN", "A1: DOB ¿ [DOB REMOVED]; G1: FOREIGN"),
        ("PHONE: (775) 555 1234, EXT. 12", "PHONE: [PHONE REMOVED], EXT. 12"),
        ("ADDRESS: PAM.MEADOWS@CONVATEC.COM1234 NATIONAL", "ADDRESS: [EMAIL REMOVED]1234 NATIONAL"),
        ("PT ID 4 123456, DOB 5/12/60, FEMALE", "PT ID [ID REMOVED], DOB [DOB REMOVED], FEMALE"),
        ("EAB, MR 1234567, DOB 1-02-1950, FEMALE", "EAB, MR [ID REMOVED], DOB [DOB REMOVED], FEMALE"),
        # rules added 2026-11-03 (all examples synthetic)
        ("DR. JOHN SMITH REMOVED THE LEAD.", "DR. [NAME REMOVED] REMOVED THE LEAD."),
        ("Dr. Jane Roe stated the pump alarmed.", "Dr. [NAME REMOVED] stated the pump alarmed."),
        ("NURSE MARY DOE NOTICED A LEAK", "NURSE [NAME REMOVED] NOTICED A LEAK"),
        ("MRS. DOE WAS DISCHARGED", "MRS. [NAME REMOVED] WAS DISCHARGED"),
        ("per Ms. Roe, the unit", "per Ms. [NAME REMOVED], the unit"),
        ("DR J. R. ROE AND DR. K.L. DOE", "DR [NAME REMOVED] AND DR. [NAME REMOVED]"),
        ("DR SMITH EXCHANGED THE LEAD", "DR [NAME REMOVED] EXCHANGED THE LEAD"),
        ("DR. BROWN KNOWS THE PUMP", "DR. [NAME REMOVED] KNOWS THE PUMP"),
        ("PATIENT NAME: JOHN DOE, MALE", "PATIENT NAME: [NAME REMOVED], MALE"),
        ("A NURSE NAMED JANE PULLED THE LINE", "A NURSE NAMED [NAME REMOVED] PULLED THE LINE"),
        ("PT INITIALS: J.D. AGE 50", "PT INITIALS: [INITIALS REMOVED] AGE 50"),
        ("PATIENT'S INITIALS ARE JRD, AGE", "PATIENT'S INITIALS ARE [INITIALS REMOVED], AGE"),
        ("A 92 YEAR OLD FEMALE", "A [AGE OVER 89] YEAR OLD FEMALE"),
        ("a 101-year-old man", "a [AGE OVER 89]-year-old man"),
        ("PATIENT AGE: 94, WEIGHT 50 KG", "PATIENT AGE: [AGE OVER 89], WEIGHT 50 KG"),
        ("THE 95 YO PT FELL", "THE [AGE OVER 89] YO PT FELL"),
        ("SHE LIVES AT 123 MAIN STREET, SPRINGFIELD.", "SHE LIVES AT [ADDRESS REMOVED], SPRINGFIELD."),
        ("SENT TO 45 N. OAK AVE APT 3, TOWN", "SENT TO [ADDRESS REMOVED], TOWN"),
        ("PHONE: 5551234567 FOR", "PHONE: [PHONE REMOVED] FOR"),
        ("CELL # 555 1234 567.", "CELL # [PHONE REMOVED]."),
        ("BORN ON 3/4/1950 IN", "BORN ON [DOB REMOVED] IN"),
        ("MED REC # A12345 SHOWS", "MED REC # [ID REMOVED] SHOWS"),
        ("please contact Jane Doe at 555-123-4567", "please contact [NAME REMOVED] at [PHONE REMOVED]"),
        # rules added 2026-11-04 (all examples synthetic)
        ("PT DOB: - 03/04/1950 FEMALE", "PT DOB: - [DOB REMOVED] FEMALE"),
        ("DATE OF BIRTH IS JANUARY 5TH, 1950.", "DATE OF BIRTH IS [DOB REMOVED]."),
        ("BIRTH DATE: 3RD OF MARCH 1950, MALE", "BIRTH DATE: [DOB REMOVED], MALE"),
        ("BIRTHDAY 4/5/1960 PER CHART", "BIRTHDAY [DOB REMOVED] PER CHART"),
        ("(DOB): 1950-3-4) AND", "(DOB): [DOB REMOVED]) AND"),
        ("DOB 05 - 12 - 1950. PT", "DOB [DOB REMOVED]. PT"),
        ("WAS BORN ABOUT 1950 IN", "WAS BORN ABOUT [DOB REMOVED] IN"),
        ("HER NUMBER IS 555 123-4567.", "HER NUMBER IS [NUMBER REMOVED]."),
        ("PT 123-45-6789 WAS SEEN", "PT [NUMBER REMOVED] WAS SEEN"),
        ("REACHED HIM ON 555.123-4567 OR", "REACHED HIM ON [NUMBER REMOVED] OR"),
        ("+1 555 123.4567 (HOME)", "[NUMBER REMOVED] (HOME)"),
        ("LOT 123-45-6789, 123-45-6790", "LOT 123-45-6789, [NUMBER REMOVED]"),
        ("AJNR 41(3):456-789 2020", "AJNR 41(3):[NUMBER REMOVED]"),
        ("DR. BRADLEY REMOVED THE LEAD", "DR. [NAME REMOVED] REMOVED THE LEAD"),
        ("MR. KEATING STATED THAT", "MR. [NAME REMOVED] STATED THAT"),
        ("DR. ZORPLING EXPLANTED IT", "DR. [NAME REMOVED] EXPLANTED IT"),
        ("DR. INSTERTED THE CATHETER", "DR. [NAME REMOVED] THE CATHETER"),     # a misspelled verb: over-removed
        ("CONTACT - [NAME REMOVED] /DR. ROEBUCK AND", "CONTACT - [NAME REMOVED] /DR. [NAME REMOVED] AND"),
        ("SEEN W/DR. KEATON TODAY", "SEEN W/DR. [NAME REMOVED] TODAY"),
        ("BY DR. MÜLLER AND", "BY DR. [NAME REMOVED] AND"),
        ("THE SURGEON WAS DR. ROE(B)(6) AT", "THE SURGEON WAS DR. [NAME REMOVED](B)(6) AT"),
        ("per Ms. roe, the unit", "per Ms. [NAME REMOVED], the unit"),
        ("IN 3 MR. ROE WAS", "IN 3 MR. [NAME REMOVED] WAS"),
        ("TO DR. A'S OFFICE", "TO DR. [NAME REMOVED] OFFICE"),
        ("DOB: (12JAN1950, 03FEB1961)", "DOB: ([DOB REMOVED])"),
        ("DATE OF BIRTH: ¿ 1950.", "DATE OF BIRTH: ¿ [DOB REMOVED]."),
        ("DATE OF BIRTH: (1950) AND", "DATE OF BIRTH: ([DOB REMOVED]) AND"),
        ("BY __555-123-4567__,", "BY __[NUMBER REMOVED]__,"),
    ]
    for raw, want in cases:
        got, _ = scrub_text(raw)
        assert got == want, f"\n raw: {raw}\n got: {got}\nwant: {want}"
    # device facts must survive
    keep = ["LOT 123-456-7890 WAS RECALLED", "REF 555-123-4567", "MODEL 100-200-3000 PUMP",
            "PATIENT WEIGHT, AGE AND DATE OF BIRTH NOT PROVIDED", "THE PATIENT'S WEIGHT AND DATE OF BIRTH. THE PATIENT",
            "CALL THE MANUFACTURER AT ONCE", "MR SCAN AT 1.5T", "MR 3T SCANNER", "PATIENT # 2 OF 3",
            "UNABLE TO ESTABLISH CONTACT WITH CUSTOMER AT THIS TIME", "HAD TO CONTACT HER HUSBAND VIA INTERCOM",
            "A CONTACT INITIATED ON (B)(6) 2020", "PRODUCT ID: MRASC1234 SN: (B)(6)", "THE MR850V HUMIDIFIER",
            "PART NUMBER 123-45-6789, LOT NUMBER 123456", "FA ####-PD-MR-123",
            "CONTACT WAS MADE IN A FOLLOW-UP CALL BY TECH SUPPORT ON 01/02/2020. AT THAT TIME",
            "THE CUSTOMER TO CONTACT TS ON 01/02/2020", "THRESHOLD IS 1V@0.5MS",
            "PREVIOUSLY REPORTED UNDER MRN 3012345-2020-00001. NO FURTHER",
            "FAIL CODE 123:45, 123-456-789-0123; FACTORY",
            "AGE/DATE OF BIRTH: 62.5 +/- 10.2 YEARS. SEX", "AGE/DATE OF BIRTH: 12 YRS. SECTION D4", "K123456 CLEARED 2019", "UDI 00812345678901",
            # added 2026-11-03
            "DR. STATED THE LEAD WAS FRACTURED", "THE DOCTOR REPORTED", "NURSE PRACTITIONER NOTED", "MR CONDITIONAL",
            "MR SYSTEM ERROR", "MRS SCORE 3", "NEAR MISS OCCURRED", "A 5 MS PULSE", "THE DOCTOR. THE PATIENT",
            "DR (B)(6) REMOVED", "NURSE (B)(6) STATED", "DOCTOR ICON ON SCREEN", "DR. TRIED TO", "MS PUMP ALARM",
            "INITIAL REPORT FROM THE ER", "INITIALS ON THE FORM", "PATIENT INITIALS NOT PROVIDED", "AGE 92 DAYS",
            "DEVICE AGE 90 DAYS", "A 45 YEAR OLD MALE", "AT 90 PSI", "PH 7.4", "PHONE CALL ON 01/02/2020",
            "2 ST JUDE LEADS", "THE 2 HEAD CT, NEGATIVE", "SAVED TO 1 USB DRIVE.", "2 AXILLARY LN, BIOPSY",
            "OVER 90 DAYS", "THE CUSTOMER WAS CALLED", "PATIENT NAME NOT PROVIDED", "PATIENT NAME: (B)(6)",
            "DOCTOR'S OFFICE", "DR. OFFICE VISIT", "Dr. was notified", "NURSE STATION'S PHONE",
            "THE NURSE KNOWS THE PUMP", "DOCTOR FEELS THE LEAD", "INSERTED A 7 F SHEATH DR. STATED",
            "REPORT 123 BY ST. JUDE MEDICAL",
            # added 2026-11-04 (page ranges in citations, "29:123-456 2015", are now removed on purpose)
            "BIRTH WEIGHT 2000 G", "BIRTH CONTROL PILLS SINCE 2015", "DATE OF BIRTH: (B)(6)", "NEWBORN 2019 MODEL",
            "SINCE BIRTH, 2 YEARS AGO", "REF 123-45-6789", "CATALOG # 555-123-4567", "PART NO. 123-45-6789",
            "COMPLAINT # 555-12-3456", "D123-45-6789 WAS", "K123456 555-123 4567", "MDR REPORT 1234567-2020-00001",
            "DR. EXCHANGED THE LEAD", "DR. REINSERTED THE LEAD", "THE DR. UNHOOKED THE LINE", "DR. REPOSITIONING",
            "MR. CONDITIONAL LABELING", "CALL 911", "TIME 12:34:56 1234", "DR. REPOSITIONED THE LEAD",
            "DR. RE-TIGHTENED IT", "THE PT HAS MS. SHE", "5 ms. the pulse", "the dr. was paged", "MR-123 PORT"]
    for s in keep:
        got, hits = scrub_text(s)
        assert got == s, f"over-scrubbed: {s!r} -> {got!r} {dict(hits)}"
    # e-mail addresses in short structured fields (added 2026-11-08; all examples synthetic)
    emails = [
        ("ACME MEDICAL INFO@ACME-MED.COM", "ACME MEDICAL [EMAIL REMOVED]"),
        ("CAT.NO. X1 / J.DOE%QA@MAIL.EXAMPLE.CO.UK", "CAT.NO. X1 / [EMAIL REMOVED]"),
        ("müller@example.de", "[EMAIL REMOVED]"),
        ("RC-123 qa@example.org; ops@example.org", "RC-123 [EMAIL REMOVED]; [EMAIL REMOVED]"),
    ]
    for raw, want in emails:
        got, k = scrub_email(raw)
        assert got == want and k >= 1, (raw, got)
        assert not EMAIL_CHECK.search(got)
    for s in ("5V@0.5MS", "MODEL A@1", "LOT 12@34.5", "NO ADDRESS HERE", "", None):
        assert scrub_email(s) == (s, 0), s
    print(f"scrub.py self-test PASSED ({len(cases)} removals, {len(keep)} keeps, {len(emails)} e-mail cases).")


if __name__ == "__main__":
    _selftest()
