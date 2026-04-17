#!/usr/bin/env python3
"""Generate the Operation Chester synthetic corpus.

Operation Chester is a modern-slavery / labour-exploitation investigation
overlapping with money laundering through a haulage front company. The
documents are deliberately *adversarial* to the extraction pipeline:

  - Names appear under inconsistent spellings (Tomasz / Tomash / Tomek Z /
    T. Zawadzki / "Toma" as phonetic rendering by a victim-witness).
  - A key vehicle plate is recorded once correctly (LB19 XAY) and once as
    an OCR/eye-witness error (LB19 XAV, and again as "??19 XAY").
  - Phone numbers appear in four different formats for the same handset.
  - Relationships are buried in prose, not stated in templated phrasings.
  - Documents contain realistic redactions, jargon (5x5x5, MG11, DIR, CHIS,
    ANPR, CDR, SAR), cross-references to unrelated operations, and
    time/location details that only combine into a chain across files.

Cross-document chain the system must construct:

  Zawadzki  ──(haulage yard)──►  Chester Logistics (UK) Ltd
      │                                    │
      │         (directorship)             │
      ▼                                    ▼
  Aleksandra Novak  ──(SAR money flow)──►  Two Rivers Crewing LLP  ──►  Dean Haig
      │
  (CDR pattern at docks cell) ──► burner +44 7700 900 481
      │
      ▼
  Elena Dumitrescu (victim) ──► "the big yard near the water" (= Sealand Industrial Park)
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from fpdf import FPDF  # type: ignore[import]
except ImportError:
    print("fpdf2 not installed. Run: pip install fpdf2")
    sys.exit(1)

OUTPUT_DIR = Path(__file__).parent.parent / "data" / "pdfs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CASE_REF = "OP_CHESTER"


def _safe_pdf_text(value: str) -> str:
    """Normalise text for core PDF fonts that only support Latin-1."""
    replacements = {
        "\u2013": "-",
        "\u2014": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
        "\u2192": "->",
        "\u00a3": "GBP ",
        "\u20ac": "EUR ",
    }
    normalised = value.translate(str.maketrans(replacements))
    return normalised.encode("latin-1", "replace").decode("latin-1")


class IntelPDF(FPDF):
    def header(self) -> None:
        self.set_font("Courier", "B", 9)
        self.set_text_color(80, 80, 80)
        self.cell(
            0,
            6,
            _safe_pdf_text(f"OPERATION CHESTER  |  OFFICIAL-SENSITIVE  |  {self.title}"),
            ln=True,
            align="R",
        )
        self.set_draw_color(80, 80, 80)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Courier", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 6, f"Page {self.page_no()}  |  HANDLING: POLICE EYES ONLY", align="C")

    def chapter_title(self, title: str) -> None:
        self.set_font("Courier", "B", 11)
        self.set_text_color(20, 20, 20)
        self.cell(0, 8, _safe_pdf_text(title), ln=True)
        self.ln(2)

    def body(self, text: str) -> None:
        self.set_font("Courier", "", 10)
        self.set_text_color(20, 20, 20)
        self.multi_cell(0, 6, _safe_pdf_text(text))
        self.ln(3)

    def mono(self, text: str) -> None:
        self.set_font("Courier", "", 9)
        self.set_text_color(30, 30, 30)
        self.multi_cell(0, 5, _safe_pdf_text(text))
        self.ln(2)

    def field(self, label: str, value: str) -> None:
        self.set_font("Courier", "B", 10)
        self.set_text_color(20, 20, 20)
        self.cell(55, 6, _safe_pdf_text(f"{label}:"), ln=False)
        self.set_font("Courier", "", 10)
        self.cell(0, 6, _safe_pdf_text(value), ln=True)


def _pdf(doc_id: str, doc_type: str) -> IntelPDF:
    pdf = IntelPDF()
    pdf.title = f"{doc_id}  |  {doc_type}"
    pdf.add_page()
    return pdf


def _save(pdf: IntelPDF, filename: str) -> None:
    out = OUTPUT_DIR / filename
    pdf.output(str(out))
    print(f"  Generated: {out}")


# ──────────────────────────────────────────────────────────────────────────────
# OC-BRIEF-001  Operation Briefing Document
# ──────────────────────────────────────────────────────────────────────────────
def gen_oc_brief_001() -> None:
    pdf = _pdf("OC-BRIEF-001", "OPERATIONAL BRIEFING")
    pdf.chapter_title("OPERATION CHESTER - BRIEFING PACK v2.3")
    pdf.field("Issued", "14/04/2024")
    pdf.field("Classification", "OFFICIAL-SENSITIVE / POLICE EYES ONLY")
    pdf.field("SIO", "DCI M. Alderton")
    pdf.field("Deputy SIO", "DI R. Collingwood")
    pdf.field("URN", "24/CH/00881")
    pdf.field("Parent strategy", "NWROCU MSHT Tasking Q2-24 (ref Op Redcliffe)")
    pdf.ln(3)
    pdf.chapter_title("1. Executive Summary")
    pdf.body(
        "Operation Chester is a joint investigation into suspected labour exploitation "
        "and money laundering centred on Chester Logistics (UK) Ltd, incorporated 02/08/2020 "
        "at Unit 11B, Sealand Industrial Park, Chester. Intelligence developed through Op "
        "Redcliffe indicates the company is used as a front for the movement of exploited "
        "foreign-national workers and the layering of cash proceeds. A parallel entity, "
        "Two Rivers Crewing LLP, appears on related invoicing; its beneficial ownership "
        "is not yet fully resolved.\n\n"
        "Subjects of interest are believed to be directing activity from a residential "
        "HMO at 44a Carney Street, Chester, CH2 4LP, with transit movements to a premises "
        "in the Bootle area of Liverpool (exact address not yet corroborated - see DIR "
        "entries OC-INT-003). No arrests are authorised at this stage; phase is intelligence "
        "development. All subject names below are suspects only - no evidential threshold "
        "reached."
    )
    pdf.chapter_title("2. Subjects Matrix (indicative)")
    pdf.body(
        "S1  Tomasz Zawadzki  (also spelt Tomash in some source material; street name "
        '    "Tomek" or "Tomek Z")  DOB on file 19/11/1984, Polish national.\n'
        "S2  Aleksandra Novak  (maiden name Aleksandra Nowakowska per HMRC record; "
        "    appears on Companies House as A. Novak).  DOB 07/02/1988.\n"
        "S3  Dean Marcus Haig  DOB 22/05/1979.  Two addresses: 9 Birchfold Close "
        "    (registered) and a secondary at [REDACTED - s.40].\n"
        "S4  Bilal Qureshi  DOB 04/04/1981.  Status unclear - may be unwitting.\n"
        "NC1 Elena Dumitrescu  (potential victim/witness; Romanian national).\n"
        "NC2 Karim al-Hassan  (potential victim/witness; Syrian national).\n\n"
        "Persons referenced in source material but not yet verified:\n"
        '  "The Chef" - unidentified male, possibly associated with hawala remittance leg.\n'
        '  "Mr Green" - code name used by CHIS XJ-71; see OC-CHIS-004.\n'
    )
    pdf.chapter_title("3. Vehicles and Assets of Interest")
    pdf.body(
        "VOI-1  Audi Q5 estate, dark grey, VRM LB19 XAY. Registered keeper reads as "
        "       Chester Logistics (UK) Ltd. ANPR capture from 02/04/2024 at camera "
        "       CAM-M53-WB shows the same vehicle as plate LB19 XAV - suspected "
        "       misread at camera; analyst to verify.\n"
        "VOI-2  Ford Transit, white, partial plate reported by witness as ??19 XAY "
        "       (may be same as VOI-1 at a distance; under review).\n"
        "VOI-3  A fleet of short-term hire vans associated with Chester Logistics "
        "       (see ANPR batch OC-ANPR-005).\n"
    )
    pdf.chapter_title("4. Telephony")
    pdf.body(
        "H-01  +44 7700 900 481  -  attributed tentatively to S1. Pattern of evening "
        "      calls from cell tower LPL-14 (Bootle docks area).\n"
        "H-02  07700 900 232       -  attributed tentatively to S2.\n"
        "H-03  +447700900157       -  unattributed; co-located with H-01 on 12 of 14 "
        "      observation days.\n"
        "H-04  07700-900-733       -  appears on invoice in OC-FOR-009.\n"
    )
    pdf.chapter_title("5. Cross-Operation References")
    pdf.body(
        "  - Op Redcliffe (NWROCU)         parent tasking; shared intelligence pool.\n"
        "  - Op Tangerine (Merseyside)     haulage MO overlap; liaison via DS Okorie.\n"
        "  - Op Hemlock (HMRC FIS)         shell-company analysis feed.\n"
    )
    _save(pdf, "OC-BRIEF-001.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# OC-MG11-002  MG11 Witness Statement - dog walker
# ──────────────────────────────────────────────────────────────────────────────
def gen_oc_mg11_002() -> None:
    pdf = _pdf("OC-MG11-002", "MG11 WITNESS STATEMENT")
    pdf.chapter_title("WITNESS STATEMENT (MG11 / CJA 1967 s.9)")
    pdf.field("Statement of", "Margaret Ellen Frobisher")
    pdf.field("Age if under 18", "over 18")
    pdf.field("Occupation", "Retired (former teaching assistant)")
    pdf.field("Date", "06 April 2024")
    pdf.field("Officer", "PC 4412 Heaney")
    pdf.ln(3)
    pdf.body(
        'This statement (consisting of 2 pages signed by me) is true to the best of my '
        "knowledge and belief and I make it knowing that, if it is tendered in evidence, "
        "I shall be liable to prosecution if I have wilfully stated in it anything which "
        "I know to be false, or do not believe to be true.\n\n"
        "I walk my dog every morning along the footpath behind Sealand Industrial Park "
        "around seven in the morning. Over the past few weeks, probably since about the "
        "middle of March, I have noticed more activity than usual at one of the units - "
        "I believe it's the one marked 11B, the one with the green roller door. Normally "
        "before eight you wouldn't see much going on there at all.\n\n"
        "I cannot swear to the exact dates. I would say on at least four occasions I have "
        "seen two or three men, sometimes four, standing by vans outside the unit. They "
        "did not appear to be doing any loading work. They were smoking and speaking in a "
        "language I did not recognise, not English. I only noticed because my dog barked "
        "at them once and one of them shouted at the dog in a way that frightened him.\n\n"
        "On one morning - I think it was the Tuesday of last week, so that would be the "
        "second of April - I saw a dark grey Audi estate-style car pull up. I am not good "
        "with registrations but I remember it started with the letters L B and the number "
        'looked like 19. Then three letters which I think ended in "Y" but might have been '
        '"V", I could not be sure from the angle. A man got out, walked to the door of 11B, '
        "knocked twice, and was let in by someone I could not see. He was in there for "
        "maybe ten or fifteen minutes. He was tall, I would say over six foot, with short "
        "dark hair and a beard. He was wearing a puffer jacket, North Face style.\n\n"
        "I had a feeling something was not right about it so I mentioned it to my nephew "
        "who used to be on the response team in Wrexham, and he told me to ring 101, which "
        "I did on the fifth of April. This is the statement I was asked to provide.\n\n"
        "I am willing to attend court if required but I would prefer not to be named in "
        "any public reporting. I have not been paid or offered anything for this statement."
    )
    pdf.ln(2)
    pdf.body("Signed:  M. E. Frobisher           Date:  06/04/2024")
    pdf.body("Witnessed by officer:  PC 4412 Heaney")
    _save(pdf, "OC-MG11-002.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# OC-INT-003  Daily Intelligence Report (DIR)
# ──────────────────────────────────────────────────────────────────────────────
def gen_oc_int_003() -> None:
    pdf = _pdf("OC-INT-003", "DAILY INTELLIGENCE REPORT")
    pdf.chapter_title("DAILY INTELLIGENCE REPORT - OP CHESTER - 08 APR 2024")
    pdf.field("Analyst", "PS 1102 Ainsworth")
    pdf.field("Grading (source)", "B - usually reliable")
    pdf.field("Grading (info)", "2 - known personally to source")
    pdf.field("Handling", "3 - dissemination to law enforcement only")
    pdf.ln(3)
    pdf.chapter_title("Entry 1  (5x5x5: B/2/3)")
    pdf.body(
        "Intelligence received via Op Redcliffe feed indicates Chester Logistics (UK) Ltd "
        "retains a standing arrangement with an unidentified freight agent operating out "
        "of the Port of Liverpool. Movements are timed around shift changes. Source unable "
        "to provide names at this time. No action against source assessed."
    )
    pdf.chapter_title("Entry 2  (5x5x5: A/1/3)")
    pdf.body(
        "ANPR analysis (reference OC-ANPR-005) confirms vehicle VRM LB19 XAY returning to "
        "Sealand Industrial Park four times between 25/03/2024 and 02/04/2024, each time "
        "in the early evening. On two occasions the same vehicle was subsequently imaged "
        "on the M53 northbound within ninety minutes. The keeper record for LB19 XAY is "
        "Chester Logistics (UK) Ltd. An apparent duplicate read as LB19 XAV on 02/04/2024 "
        "at camera CAM-M53-WB is assessed to be the same vehicle misread by the reader - "
        "operator confirms adjacent images are of a dark-grey Audi Q5 in both cases."
    )
    pdf.chapter_title("Entry 3  (5x5x5: B/3/3)")
    pdf.body(
        'Open-source check against Companies House shows "Two Rivers Crewing LLP" '
        "incorporated 11/01/2022 at an accommodation address in Altrincham. Designated "
        "members are listed as A. Novak and D. M. Haig. The same A. Novak does not appear "
        "on the Chester Logistics record, where the sole director is Tomasz Zawadzki. "
        "Novak's HMRC file carries the surname Nowakowska pre-2021 - assessed to be the "
        "same individual (subject to verification via passport record request)."
    )
    pdf.chapter_title("Entry 4  (5x5x5: UNGRADED - raw CHIS report)")
    pdf.body(
        "See OC-CHIS-004. Controller S/Sgt Bryant has signed off partial disclosure to "
        "investigative team; names of principals are redacted at source under s.40. DIR "
        "entry does not repeat redacted content."
    )
    pdf.chapter_title("Entry 5  (5x5x5: C/4/3)")
    pdf.body(
        "Anonymous CrimeStoppers report dated 03/04/2024 references a property on the "
        "Breckfield side of Liverpool used for short-term accommodation of foreign-national "
        'men. Caller states "the house is managed by a Polish guy called Tomek" who drives '
        'a "flash" dark estate car. Caller declined a callback. Intelligence value assessed '
        "as modest given lack of corroboration, but consistent with picture from ANPR."
    )
    _save(pdf, "OC-INT-003.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# OC-CHIS-004  CHIS Product Report (redacted)
# ──────────────────────────────────────────────────────────────────────────────
def gen_oc_chis_004() -> None:
    pdf = _pdf("OC-CHIS-004", "CHIS PRODUCT REPORT")
    pdf.chapter_title("CHIS PRODUCT REPORT - REDACTED FOR INVESTIGATIVE TEAM")
    pdf.field("Source ref", "XJ-71")
    pdf.field("Controller", "S/Sgt Bryant, Covert Intelligence Unit")
    pdf.field("Date of meet", "10/04/2024")
    pdf.field("Tasking", "OP CHESTER-T04")
    pdf.field("Authority", "RIPA Part II authorisation 24/NW/AUTH/2341")
    pdf.ln(3)
    pdf.body(
        "This report contains product material of a confidential human intelligence "
        "source. All references capable of identifying the source are redacted at source. "
        "The investigative team receives this report with certain names substituted with "
        "codenames agreed between Controller and Source to enable onward reporting. "
        "Under no circumstances are Source's code names to be disclosed beyond the "
        "Operation Chester investigative cell."
    )
    pdf.chapter_title("Product - meeting at [REDACTED - location]")
    pdf.body(
        'Source states that on the evening of 09/04/2024 "Mr Green" attended a meeting '
        "at [REDACTED - location] together with a male referred to in earlier reports as "
        '"The Chef". The meeting lasted approximately forty-five minutes. Source was not '
        "present inside but was in a position to observe arrivals and departures.\n\n"
        'Source reports that "Mr Green" is understood by those present to be responsible '
        'for "the paperwork side" of what Source describes as "the lads" - taken by '
        "Controller to mean the workers moved by the group. A second male, whom Source "
        'had not seen before, was addressed by "Mr Green" as [REDACTED - 7 chars]. '
        'Source says this individual was introduced as "Tomek\'s cousin" though Source '
        "places no weight on that description and believes it may be cover.\n\n"
        'Source notes "Mr Green" repeatedly referred to "the yard" and "the docks run" - '
        "Controller assesses these to be, respectively, the industrial premises already "
        "of interest to the Operation and a movement pattern that the ANPR work is "
        'independently establishing. Source also overheard a reference to "cash through '
        'Two Rivers" which Controller believes corresponds to Two Rivers Crewing LLP.'
    )
    pdf.chapter_title("Product - phone traffic")
    pdf.body(
        'Source observed "Mr Green" using a handset which rang twice during the meeting. '
        "Source was not able to view the caller ID. Source did observe that the handset "
        'was a newer-model iPhone in a black case with a sticker of a dog\'s paw print.\n\n'
        'Source has separately reported the number +44 7700 900 157 as one that "The Chef" '
        "uses to coordinate movements, though Source cannot confirm this is the only "
        'number. Source notes that "The Chef" refers to this number as "the office line".'
    )
    pdf.chapter_title("Controller commentary")
    pdf.body(
        'The assessed real identity of "Mr Green" is recorded in the closed annex to this '
        "product and is not disclosed in this document. Investigative team should treat "
        "all code-named references as pseudonyms for the purposes of case building until "
        "such time as the closed annex is released under a separate authority.\n\n"
        "Material graded 5x5x5 as B/2/4 (handling code 4 - dissemination requires controller "
        "approval). This report is not for inclusion in any disclosure bundle without "
        "prior consultation with the Senior Information Officer."
    )
    _save(pdf, "OC-CHIS-004.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# OC-ANPR-005  ANPR Hits Report (tabular)
# ──────────────────────────────────────────────────────────────────────────────
def gen_oc_anpr_005() -> None:
    pdf = _pdf("OC-ANPR-005", "ANPR HITS REPORT")
    pdf.chapter_title("ANPR HITS - OP CHESTER - BATCH 24-04-A")
    pdf.field("Analyst", "P. Okafor, Force Intelligence Bureau")
    pdf.field("Date range", "20/03/2024 - 11/04/2024")
    pdf.field("Subject plates", "LB19 XAY (primary); misreads under review")
    pdf.ln(2)
    pdf.body(
        "The table below lists ANPR hits of interest. Reads graded 'H' are high "
        "confidence (plate reader + contextual image match). Reads graded 'M' are "
        "medium confidence (plate reader only). Reads graded 'L' are low confidence "
        "(misread flagged by reader or plausible OCR error). Cross-ref the 'linked' "
        "column for the assessed canonical plate."
    )
    pdf.mono(
        "#   DATE        TIME    CAMERA                      PLATE READ  CONF  LINKED\n"
        "--  ----------  ------  --------------------------  ----------  ----  ----------\n"
        "01  25/03/2024  06:14   CAM-M53-NB (jct 4)          LB19 XAY    H     -\n"
        "02  25/03/2024  19:47   CAM-SEAL-IN (Sealand IP)    LB19 XAY    H     -\n"
        "03  27/03/2024  07:02   CAM-M53-NB (jct 4)          LB19 XAY    H     -\n"
        "04  27/03/2024  21:13   CAM-DOCKS-SB (Bootle N)     LB19 XAY    M     -\n"
        "05  30/03/2024  05:49   CAM-M53-NB (jct 4)          LB19 XAY    H     -\n"
        "06  30/03/2024  20:02   CAM-DOCKS-SB (Bootle N)     LB19 XAY    H     -\n"
        "07  02/04/2024  06:31   CAM-M53-WB (jct 2 slip)     LB19 XAV    L     LB19 XAY\n"
        "08  02/04/2024  06:33   CAM-M53-WB (jct 2 main)     LB19 XAY    H     -\n"
        "09  02/04/2024  19:58   CAM-SEAL-IN (Sealand IP)    LB19 XAY    H     -\n"
        "10  05/04/2024  07:08   CAM-M53-NB (jct 4)          L819 XAY    L     LB19 XAY\n"
        "11  05/04/2024  21:24   CAM-DOCKS-SB (Bootle N)     LB19 XAY    H     -\n"
        "12  07/04/2024  06:22   CAM-CARNEY-RD (CH2)         LB19 XAY    H     -\n"
        "13  09/04/2024  19:44   CAM-DOCKS-SB (Bootle N)     LB19 XAY    H     -\n"
        "14  10/04/2024  22:12   CAM-DOCKS-SB (Bootle N)     --          --    --\n"
    )
    pdf.chapter_title("Analyst Note")
    pdf.body(
        "Row 07 plate read LB19 XAV is assessed to be a single-character OCR substitution "
        "from the genuine plate LB19 XAY, caused by low-angle illumination at the slip "
        "camera. Row 08 (two minutes later, adjacent camera on the same vehicle heading) "
        "reads the plate cleanly as LB19 XAY. The reader image database confirms the "
        "vehicle in both captures is a dark-grey Audi Q5 with identical offside trim.\n\n"
        "Row 10 read L819 XAY is a common substitution of B -> 8 under glare and is "
        "treated as the same vehicle. Row 14 is a bare camera hit without a confirmed "
        "plate read; retained for completeness.\n\n"
        "Pattern assessment: the vehicle LB19 XAY moves routinely between the Chester/"
        "Sealand area and the Bootle docks area. The midday gap is consistent with work "
        "activity taking place at the docks end before an evening return. On 07/04/2024 "
        "the vehicle was imaged on Carney Road (CH2) for the first time in this batch - "
        "this corresponds to the suspected residential base at 44a Carney Street per "
        "OC-BRIEF-001."
    )
    _save(pdf, "OC-ANPR-005.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# OC-CDR-006  Call Data Records Extract
# ──────────────────────────────────────────────────────────────────────────────
def gen_oc_cdr_006() -> None:
    pdf = _pdf("OC-CDR-006", "CDR EXTRACT")
    pdf.chapter_title("CALL DATA RECORDS - EXTRACT - OP CHESTER")
    pdf.field("Obtained under", "CD Act 2014 / acquisition 24/NW/COMS/00442")
    pdf.field("Target MSISDN", "+44 7700 900 481 (tentatively S1)")
    pdf.field("Period", "18/03/2024 - 10/04/2024")
    pdf.field("Provider", "Operator redacted - see closed annex")
    pdf.ln(2)
    pdf.body(
        "The extract below is a selection from a larger return. Full return is retained "
        "on the evidential server. Times are in UTC; local time during the period was UTC+1."
    )
    pdf.mono(
        "DATE        UTC     DIR  OTHER PARTY           DUR    CELL-ID         NOTES\n"
        "----------  ------  ---  --------------------  -----  --------------  ------------------\n"
        "19/03/2024  18:02   OUT  +44 7700 900 232       00:41  LPL-14          Bootle Dock Rd\n"
        "19/03/2024  18:04   IN   +447700900157          00:15  LPL-14          Co-located\n"
        "19/03/2024  22:47   OUT  07700 900 733          00:08  CH-SEAL-02      Sealand IP\n"
        "21/03/2024  07:15   OUT  +44 7700 900 232       02:03  CH-CARNEY-01    Carney St area\n"
        "22/03/2024  19:31   IN   +447700900157          00:33  LPL-14          -\n"
        "25/03/2024  05:58   OUT  +44 7700 900 232       00:12  CH-SEAL-02      -\n"
        "25/03/2024  19:54   OUT  07700-900-733          00:04  LPL-14          Co-located\n"
        "27/03/2024  21:19   OUT  +447700900157          00:57  LPL-14          -\n"
        "30/03/2024  05:51   IN   +44 7700 900 232       00:08  CH-SEAL-02      -\n"
        "30/03/2024  20:08   IN   +447700900157          01:11  LPL-14          -\n"
        "02/04/2024  19:51   OUT  +447700900157          00:22  LPL-14          -\n"
        "04/04/2024  14:40   OUT  +44 161 555 0184       00:05  CH-CARNEY-01    Manchester land\n"
        "05/04/2024  21:30   OUT  +447700900157          00:46  LPL-14          -\n"
        "07/04/2024  06:19   OUT  +44 7700 900 232       00:18  CH-CARNEY-01    -\n"
        "09/04/2024  19:40   IN   +447700900157          01:52  LPL-14          -\n"
    )
    pdf.chapter_title("Attribution and Pattern Assessment")
    pdf.body(
        "Handset +44 7700 900 481 (target) is tentatively attributed to Tomasz Zawadzki "
        "(S1). Handset +44 7700 900 232 is tentatively attributed to Aleksandra Novak "
        "(S2) on the basis of outbound call pattern, billing name, and co-location with "
        "CH-CARNEY-01 cell during morning hours consistent with residence at 44a Carney "
        "Street, CH2 4LP. Neither attribution is beyond reasonable doubt.\n\n"
        "Handset +44 7700 900 157 is unattributed. The handset exhibits a strong co-location "
        "pattern with the target when the target is in the Bootle docks area (cell LPL-14). "
        "This handset has not been observed at the CH-CARNEY-01 cell during the observation "
        "period. Provisional assessment: +44 7700 900 157 is operated by an associate "
        'resident in the Merseyside area; possible identity is CHIS-referenced "The Chef" '
        '(OC-CHIS-004) but this is speculative.\n\n'
        "Handset 07700-900-733 appears twice, briefly, and is co-located on one occasion "
        "with the target. This number also appears on the billing invoice recovered in "
        "OC-FOR-009; possible attribution to D. M. Haig (S3) under review.\n\n"
        "The number +44 161 555 0184 is a Manchester-area landline. A single outbound call "
        "of five seconds duration is insufficient to draw inference. Business listing for "
        "the number is a takeaway premises in the Longsight area; link-charting to follow."
    )
    _save(pdf, "OC-CDR-006.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# OC-FIU-007  SAR / Financial Intelligence Report
# ──────────────────────────────────────────────────────────────────────────────
def gen_oc_fiu_007() -> None:
    pdf = _pdf("OC-FIU-007", "FINANCIAL INTELLIGENCE REPORT")
    pdf.chapter_title("FINANCIAL INTELLIGENCE REPORT - OP CHESTER")
    pdf.field("Author", "K. Mensah, Financial Investigation Unit")
    pdf.field("Report date", "12/04/2024")
    pdf.field("Underpinning SARs", "SAR-24-41192, SAR-24-41288, SAR-24-41310")
    pdf.ln(3)
    pdf.chapter_title("1. Entities")
    pdf.body(
        "Chester Logistics (UK) Ltd (company number 12887441) - sole director Tomasz "
        "Zawadzki. Registered office Unit 11B, Sealand Industrial Park, Chester. Primary "
        "banking relationship with a UK challenger bank; account 40-26-08 81337204. The "
        "account has received regular third-party transfers described in the narrative "
        '"CONSULTANCY" or "LABOUR SUPPLY", which is inconsistent with a declared haulage '
        "trade.\n\n"
        "Two Rivers Crewing LLP (LLP number OC443118) - designated members Aleksandra "
        "Novak and Dean Marcus Haig. Registered at a mail-handling address in Altrincham. "
        "Primary banking account 23-19-44 00982115 at the same challenger bank. This LLP "
        "appears to act as a staging account: inbound from Chester Logistics, outbound "
        "to a mixture of UK personal accounts and one foreign IBAN described below.\n\n"
        "Haigwood Contracting (sole trader, D. M. Haig) - trading account 60-14-02 44710029. "
        "Shows regular receipts from the Two Rivers LLP account consistent with a monthly "
        "fee, tentatively a shareholding-in-kind arrangement."
    )
    pdf.chapter_title("2. Flow of Interest")
    pdf.body(
        "Over the period 01/01/2024 - 31/03/2024 the Chester Logistics account transferred "
        "approximately GBP 412,850 to the Two Rivers Crewing LLP account in a series of "
        "47 transactions of between GBP 4,900 and GBP 9,980 - below the threshold at "
        "which most institutions apply enhanced scrutiny, but sufficiently consistent to "
        "constitute structuring.\n\n"
        "From the Two Rivers LLP account, the largest single outbound stream during the "
        "same period was to IBAN AE07 0331 2345 6789 0123 456 (UAE-resident receiving "
        "bank - masked in this report). Transfers to that IBAN totalled GBP 187,110 in "
        "six tranches. Narrative fields were populated with invoice numbers matching a "
        "template unique to Chester Logistics invoicing (see OC-FOR-009 for the recovered "
        "PDF template).\n\n"
        "A parallel stream of smaller personal receipts consistent with wage disbursement "
        "was sent from Two Rivers to four UK personal accounts, including one in the name "
        "of Bilal Qureshi (sort 09-01-28, account 52007719). Receipts to Qureshi's account "
        "are consistent with a declared contractor role."
    )
    pdf.chapter_title("3. Crypto Indicators")
    pdf.body(
        "A partial Bitcoin address recovered from an encrypted chat fragment during the "
        "digital forensic examination (see OC-FOR-009) - reads as bc1qx...a8z9 - is "
        "associated with five inbound transactions during the review period totalling "
        "approximately 0.62 BTC. Chain-analysis vendor reports a clustering relationship "
        "with wallets historically used for layering proceeds of labour-exploitation "
        "operations in Northern Europe. The partial address is insufficient for attribution "
        "to any individual in this operation."
    )
    pdf.chapter_title("4. Caveats")
    pdf.body(
        "All transactions referenced in this report are drawn from suspicious activity "
        "reports and banking acquisition orders and remain subject to further evidential "
        "work. Account numbers are disclosed in sort-code/account form in the primary "
        "and in IBAN form where relevant. This report does not amount to a criminal "
        "charge decision."
    )
    _save(pdf, "OC-FIU-007.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# OC-STMT-008  Victim/witness statement - Elena Dumitrescu
# ──────────────────────────────────────────────────────────────────────────────
def gen_oc_stmt_008() -> None:
    pdf = _pdf("OC-STMT-008", "VICTIM STATEMENT")
    pdf.chapter_title("ACHIEVING BEST EVIDENCE - INTERVIEW TRANSCRIPT EXTRACT")
    pdf.field("Subject", "Elena Dumitrescu (DOB 12/06/1996, Romanian national)")
    pdf.field("Date of interview", "08/04/2024")
    pdf.field("Location", "Victim suite, Chester Police HQ")
    pdf.field("Officers", "DC Hughes (lead), PC Okafor (second)")
    pdf.field("Interpreter", "Ms I. Radu (Romanian - English)")
    pdf.field("ISVA", "M. Carr, local provider")
    pdf.ln(3)
    pdf.body(
        "The transcript below is an extract. Full ABE recording is retained. Words in "
        "square brackets are interpreter/officer clarifications. Spelling of names is "
        "as rendered by the interpreter phonetically from the interviewee's account.\n\n"
        'ELENA:  I come here - to UK - last year, autumn. A man named "Toma" arrange '
        "everything. He say there is work in factory. He pay my travel from Bucharest. "
        "When I arrive, he take passport. He say I must work to pay back.\n\n"
        "DC HUGHES:  Can you describe Toma?\n\n"
        'ELENA:  Big, tall. Dark hair, beard. Maybe thirty-five, forty. Polish I think, '
        'not Romanian. When he speak on phone sometimes I hear him say "Tomek" to other '
        "men. Always he is on phone. I did not know his other name.\n\n"
        "DC HUGHES:  Where did you live in the UK?\n\n"
        "ELENA:  At first in a house. Many people, seven, eight in the house. Was "
        "Liverpool area I think - near water, you can smell the sea. I did not know the "
        "address. Later, maybe two months, they move me to another house, smaller, in "
        'town they call "Chester". In the street, a woman living nearby speak to me once '
        "and say we are in CH two - I remember because it was on a letter in the hall.\n\n"
        "DC HUGHES:  Where did you work?\n\n"
        "ELENA:  Sometime in the house - cleaning, cooking for the men. Sometime they "
        "take us in a van, big yard near the water. Many pallets, boxes. We load and "
        "unload. The yard is with green door on the building. I do not know the name "
        "but it is near a train track I think, industrial area. It was cold always.\n\n"
        "DC HUGHES:  How many people were with you?\n\n"
        "ELENA:  At yard, maybe ten, sometime more. Men from Syria, from Pakistan - I "
        "think two boys from Pakistan, one called Karim, he was kind to me, he share "
        "food. There is one girl from Romania also but she is gone I do not know where.\n\n"
        "DC HUGHES:  Do you remember any cars or vans that came to the yard?\n\n"
        'ELENA:  Vans many, white, always different. One time a big dark car come - not '
        "van, expensive. The man who drive it is the boss I think, different man from "
        "Toma - short hair, English. He did not speak to us. He speak to Toma and they "
        "go inside the office.\n\n"
        "DC HUGHES:  Was there a woman involved at any point?\n\n"
        'ELENA:  Yes. A woman they call "Ola" sometimes come. She is Polish too I think. '
        "She bring papers for the men to sign. I did not sign, I was not asked. She has "
        "short blonde hair. She drive a small car, red I think.\n\n"
        "DC HUGHES:  Elena, do you feel safe telling us this?\n\n"
        "ELENA:  No. But I tell you because Karim he help me leave. He find number for "
        "charity. I think Toma he will know I am gone. I am afraid.\n\n"
        "[Interview paused at interviewee's request. Welfare breaks provided. Resumed "
        "after 22 minutes.]"
    )
    _save(pdf, "OC-STMT-008.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# OC-FOR-009  Digital Forensic Report
# ──────────────────────────────────────────────────────────────────────────────
def gen_oc_for_009() -> None:
    pdf = _pdf("OC-FOR-009", "DIGITAL FORENSICS REPORT")
    pdf.chapter_title("DIGITAL FORENSIC EXAMINATION - EXHIBIT BMK/1")
    pdf.field("Examiner", "B. Meredith-Kane, Digital Forensics Unit")
    pdf.field("Exhibit", "BMK/1 (mobile handset, Samsung Galaxy S21)")
    pdf.field("Seizure context", "Recovered from bedside 9 Birchfold Close, 10/04/2024")
    pdf.field("IMEI", "IMEI: 356938035643809")
    pdf.field("Serial", "serial number RF8M901HXAP")
    pdf.field("Acquisition", "Physical (Cellebrite UFED Premium, kernel 7.64)")
    pdf.ln(3)
    pdf.chapter_title("1. Device state at seizure")
    pdf.body(
        "Device was powered on, locked. Authorisation was obtained under s.49 RIPA 2000 "
        "and the user compelled to provide the passcode. The device reported Android 13, "
        "patch level 2024-02-05. Biometric data was not used.\n\n"
        "The device was placed into Faraday conditions at the point of seizure and remained "
        "in Faraday until controlled examination under the forensic environment."
    )
    pdf.chapter_title("2. Account artefacts")
    pdf.body(
        "Primary Google account: dean.m.haig@[redacted mail domain].\n"
        "Secondary iCloud entry: dhaig74@icloud.com (present as passkey only, not "
        "authenticated on device).\n"
        "Signal application installed; linked number +44 7700 900 733.\n"
        "WhatsApp installed; linked number +44 7700 900 733.\n"
        "Three Telegram accounts signed in. Controller accounts are phone-number linked; "
        'one secondary account has the username "@two_rivers_ops" - account created '
        "16/01/2022, not active in the past 40 days."
    )
    pdf.chapter_title("3. Files of interest")
    pdf.body(
        "A PDF titled INV-CL-0447.pdf recovered from the Downloads directory:\n"
        "    Created:  02/04/2024 14:22\n"
        '    From:     "Chester Logistics (UK) Ltd", Unit 11B, Sealand Industrial Park\n'
        '    To:       "Two Rivers Crewing LLP", c/o Suite 14, Altrincham\n'
        "    Subject:  Labour supply - March 2024\n"
        "    Amount:   GBP 9,780.00 (inclusive of expenses)\n"
        "    Contact:  07700-900-733 (D. Haig)\n"
        "    MD5 hash: 3f9a1b0cf7a2e88d9114a6b3cae09f20\n\n"
        "Two photographs (JPEG) of handwritten lists of first names and numbers, taken "
        "on the device camera on 27/03/2024 at 19:14 and 19:15 respectively. Names "
        "include (as written, not corrected): Karim, Elena, Nasim, Omar, Adrian. Numbers "
        "are small integers in the range 1-3 - meaning unknown without further context."
    )
    pdf.chapter_title("4. Messaging artefacts")
    pdf.body(
        "Signal messaging history for the past 30 days was retrievable to the extent "
        "that messages had not been auto-deleted under the application's retention policy. "
        "The most significant retained thread is with contact saved as 'Tomek' (number "
        "stored: +44 7700 900 481). Selected retained messages:\n\n"
        '  27/03  19:12  [in]   "list tomorrow 6. usual yard"\n'
        '  27/03  19:14  [out]  "OK. Q5 needs tyres btw"\n'
        '  02/04  06:11  [in]   "on my way"\n'
        '  02/04  19:59  [out]  "all sorted this end. seal-in cam still on?"\n'
        '  02/04  20:02  [in]   "not my problem. we talked about this"\n'
        '  09/04  19:35  [in]   "157 wants to meet. friday probably"\n\n'
        'The contact saved as "157" on the device is the number +44 7700 900 157. No '
        "messages were exchanged with that contact within the recoverable window; the "
        "contact is referenced by Tomek in the exchange above."
    )
    pdf.chapter_title("5. Cryptocurrency")
    pdf.body(
        "A text fragment recovered from the keyboard-prediction database includes the "
        "partial string 'bc1qx...a8z9' consistent with a Bitcoin bech32 address. The "
        "fragment is not a contiguous string; the preceding characters were not recovered. "
        "The fragment corresponds to the partial address reported independently by the "
        "FIU (OC-FIU-007)."
    )
    _save(pdf, "OC-FOR-009.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# OC-INTV-010  Suspect interview transcript - Tomasz Zawadzki
# ──────────────────────────────────────────────────────────────────────────────
def gen_oc_intv_010() -> None:
    pdf = _pdf("OC-INTV-010", "SUSPECT INTERVIEW")
    pdf.chapter_title("INTERVIEW UNDER CAUTION - TRANSCRIPT EXTRACT")
    pdf.field("Subject", "Tomasz Zawadzki, DOB 19/11/1984")
    pdf.field("Solicitor", "Ms H. Okafor, Norton & Finn Solicitors")
    pdf.field("Officers", "DC Hughes, DS Pritchard")
    pdf.field("Date/Time start", "11/04/2024 09:42")
    pdf.field("Arrest location", "44a Carney Street, Chester CH2 4LP")
    pdf.field("Offences (as cautioned)", "MSA 2015 s.1 / POCA 2002 s.327")
    pdf.ln(3)
    pdf.body(
        "Interview conducted in English. Interviewee declined the services of a Polish "
        "interpreter and confirmed fluency. Caution administered and understood. The "
        "extract below omits opening formalities.\n\n"
        "DC HUGHES:    For the tape, can you confirm your full name and date of birth.\n\n"
        "ZAWADZKI:     Tomasz Zawadzki. Nineteenth of November nineteen eighty-four.\n\n"
        "DC HUGHES:    And you are the sole director of Chester Logistics UK Limited?\n\n"
        "ZAWADZKI:     I am the director, yes.\n\n"
        "DC HUGHES:    We have recovered from an address in Birchfold Close a phone "
        "              that contains a Signal conversation with a contact saved as "
        '              "Tomek", associated with the number ending four eight one. Is '
        "              that your number?\n\n"
        "ZAWADZKI:     No comment.\n\n"
        "DC HUGHES:    The phone we recovered from you this morning - is that correct? "
        "              Handset seized at time of arrest - is that your phone?\n\n"
        "ZAWADZKI:     Yes it is my phone.\n\n"
        "DC HUGHES:    What is the number for that phone?\n\n"
        "ZAWADZKI:     I do not remember. I just use the phone.\n\n"
        "DS PRITCHARD: The phone is associated with the number plus four-four seven "
        "              seven zero zero nine zero zero four eight one. Does that jog "
        "              your memory?\n\n"
        "ZAWADZKI:     I suppose so. If that is what my phone says.\n\n"
        "DC HUGHES:    Do you know a woman called Elena Dumitrescu?\n\n"
        "ZAWADZKI:     I don't know that name, no.\n\n"
        "DC HUGHES:    Do you know a woman you sometimes call Ola?\n\n"
        "ZAWADZKI:     Ola is common Polish name. Which Ola.\n\n"
        "DC HUGHES:    Aleksandra Novak.\n\n"
        "ZAWADZKI:     She is my partner. We live together. You know this, you were "
        "              at the house.\n\n"
        "DC HUGHES:    Do you know Dean Haig?\n\n"
        "ZAWADZKI:     Dean I know through business. He helps us with paperwork. He "
        "              works for Two Rivers. That is Ola's company really, not mine.\n\n"
        "DC HUGHES:    And Bilal Qureshi?\n\n"
        "ZAWADZKI:     Bilal drives for us sometimes. Contractor. He is not part of "
        "              anything.\n\n"
        "DC HUGHES:    Who is the man referred to in your messages as one five seven?\n\n"
        "ZAWADZKI:     No comment.\n\n"
        "DC HUGHES:    Your Signal messages from second of April reference a seal-in "
        '              camera. "Still on" is the phrase. Can you explain that?\n\n'
        "ZAWADZKI:     I don't know what you mean. Could be anything.\n\n"
        "DC HUGHES:    The camera is at Sealand Industrial Park. The vehicle registered "
        "              to your company - an Audi Q5, plate LB one nine XAY - has been "
        "              photographed at that camera repeatedly over the past month. Are "
        "              you the driver of that vehicle?\n\n"
        "ZAWADZKI:     Sometimes I drive it, yes. It is company car.\n\n"
        "DC HUGHES:    Were you the driver on the morning of second of April?\n\n"
        "ZAWADZKI:     I do not remember.\n\n"
        "DC HUGHES:    Do you know Karim al-Hassan?\n\n"
        "ZAWADZKI:     No.\n\n"
        "DC HUGHES:    Do you know of a Romanian woman staying at the Carney Street "
        "              address between approximately January and March of this year?\n\n"
        "ZAWADZKI:     No. We have a lodger sometimes. Not a Romanian. Polish lady, "
        "              my cousin, for a few weeks.\n\n"
        "DC HUGHES:    What is the name of your cousin?\n\n"
        "ZAWADZKI:     No comment.\n\n"
        "INTERVIEW SUSPENDED at 10:28 for legal consultation. Resumed at 10:52 - "
        "transcript continues on separate document OC-INTV-010B (not attached)."
    )
    _save(pdf, "OC-INTV-010.pdf")


def main() -> None:
    print(f"Generating Operation Chester corpus -> {OUTPUT_DIR}")
    gen_oc_brief_001()
    gen_oc_mg11_002()
    gen_oc_int_003()
    gen_oc_chis_004()
    gen_oc_anpr_005()
    gen_oc_cdr_006()
    gen_oc_fiu_007()
    gen_oc_stmt_008()
    gen_oc_for_009()
    gen_oc_intv_010()
    print("Done. 10 Operation Chester PDFs written.")


if __name__ == "__main__":
    main()
