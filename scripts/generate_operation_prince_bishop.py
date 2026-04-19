#!/usr/bin/env python3
"""Generate the Operation Prince Bishop synthetic corpus.

Operation Prince Bishop is a financial-crime investigation centred on
Bishop Auckland, County Durham. The scheme under investigation layers
mortgage fraud, fraudulent construction invoicing, and offshore
laundering through a string of shell companies whose registered office
is a local solicitor's practice.

The documents are deliberately long, messy, and adversarial:

  - Names appear under inconsistent spellings
    (Sasha / Sacha Verity, Tomić / Tomic / V. Tomic, Qureshi / Quereshi).
  - A central phone number (+44 7488 112233) appears as
    "+447488112233", "07488 112 233", and "0-7488-112233".
  - Vehicle plates appear correctly in most places but are transcribed
    with an OCR-style error in one witness statement.
  - The criminal chain is split across files — no single document names
    every entity in the loop. The graph must join them.
  - Documents include realistic redactions, legal footers, SAR / MLR
    jargon, and references to an older case (OP_TEESDALE, 2021).

Cross-document chain the system must construct:

  Holcombe (BA-INT-001) --[director of]--> Wear Valley Regeneration Ltd
      |                                             |
      |           (BA-FIN-002 corporate map)        |
      v                                             v
  Bondgate Property Partners LLP   Coundon Estates Ltd --[UBO]--> Tees Holdings (IOM) Ltd
      |                                                                    |
      | (BA-FIN-005 invoice laundering)                                    |
      v                                                                    v
  Galgate Nominees Ltd --[pays]--> Imran Qureshi (dormant builder)   V. Tomic, Ljubljana
      |                                                                    ^
      +-----------[BA-FIN-006 offshore trace]------------------------------+

  Holcombe <--[meets repeatedly at The Coach House]--> Kevin Pryce (mortgage broker)
      |                      (BA-SURV-003, BA-INT-004)
      v
  +447488112233 (burner) --[BA-TEC-009 cell-site]--> 42 Etherley Lane (Holcombe's home)
                                                      18 Bondgate (Wainwright & Vane)
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

CASE_REF = "OP_PRINCE_BISHOP"


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
        "\u0107": "c",   # c with acute (Tomic)
        "\u0106": "C",
    }
    normalised = value.translate(str.maketrans(replacements))
    return normalised.encode("latin-1", "replace").decode("latin-1")


class IntelPDF(FPDF):
    """Intelligence-report style PDF renderer with multi-page support."""

    def header(self) -> None:
        self.set_font("Courier", "B", 9)
        self.set_text_color(80, 80, 80)
        self.cell(
            0,
            6,
            _safe_pdf_text(f"OPERATION PRINCE BISHOP  |  OFFICIAL-SENSITIVE  |  {self.title}"),
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
        self.cell(
            0,
            6,
            _safe_pdf_text(
                f"Page {self.page_no()}  |  {CASE_REF}  |  NOT FOR FURTHER DISSEMINATION"
            ),
            align="C",
        )

    def chapter_title(self, title: str) -> None:
        if self.get_y() > 250:
            self.add_page()
        self.set_font("Courier", "B", 11)
        self.set_text_color(20, 20, 20)
        self.cell(0, 8, _safe_pdf_text(title), ln=True)
        self.ln(2)

    def body(self, text: str) -> None:
        self.set_font("Courier", "", 10)
        self.set_text_color(20, 20, 20)
        self.multi_cell(0, 6, _safe_pdf_text(text))
        self.ln(3)

    def field(self, label: str, value: str) -> None:
        self.set_font("Courier", "B", 10)
        self.set_text_color(20, 20, 20)
        self.cell(50, 6, _safe_pdf_text(f"{label}:"), ln=False)
        self.set_font("Courier", "", 10)
        self.cell(0, 6, _safe_pdf_text(value), ln=True)

    def divider(self) -> None:
        self.ln(1)
        self.set_draw_color(160, 160, 160)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(3)


def _pdf(doc_id: str, doc_type: str) -> IntelPDF:
    pdf = IntelPDF()
    pdf.title = f"{doc_id}  |  {doc_type}"
    pdf.add_page()
    return pdf


def _save(pdf: IntelPDF, filename: str) -> None:
    out = OUTPUT_DIR / filename
    pdf.output(str(out))
    print(f"  Generated: {out}  ({pdf.page_no()} pages)")


# ──────────────────────────────────────────────────────────────────────────────
# BA-INT-001  Suspect Interview — Darren Holcombe
# ──────────────────────────────────────────────────────────────────────────────
def gen_ba_int_001() -> None:
    pdf = _pdf("BA-INT-001", "SUSPECT INTERVIEW UNDER CAUTION")

    pdf.chapter_title("INTERVIEW RECORD — DARREN HOLCOMBE")
    pdf.field("URN", "24/DU/2311")
    pdf.field("Date", "11 February 2025")
    pdf.field("Location", "Bishop Auckland Police Office, Woodhouse Lane")
    pdf.field("Interviewing officers", "DC Hartley, DS Ramsden")
    pdf.field("Subject", "Darren Holcombe, DOB 17/02/1976")
    pdf.field("Subject address", "42 Etherley Lane, Bishop Auckland, DL14 7RB")
    pdf.field("Occupation", "Property developer")
    pdf.field("Solicitor", "Mr G. Wainwright, Wainwright & Vane, 18 Bondgate")
    pdf.field("Caution given", "Yes — 10:04")
    pdf.ln(3)

    pdf.chapter_title("Preliminaries")
    pdf.body(
        "DS RAMSDEN: This interview is being conducted under caution in connection with "
        "Operation Prince Bishop. We are investigating allegations of mortgage fraud, money "
        "laundering and false accounting contrary to the Proceeds of Crime Act 2002 and the "
        "Fraud Act 2006. Mr Holcombe, you have been arrested today at 07:45 at your home "
        "address at 42 Etherley Lane. Do you understand the caution as it has been explained?\n\n"
        "HOLCOMBE: Yes.\n\n"
        "MR WAINWRIGHT: For the record, my client is attending voluntarily from the point of "
        "arrival at the station, and I have advised him on the procedure.\n\n"
        "DS RAMSDEN: Noted."
    )

    pdf.chapter_title("Business interests")
    pdf.body(
        "DC HARTLEY: Mr Holcombe, can you describe your business interests for us?\n\n"
        "HOLCOMBE: I'm a property developer. I run Wear Valley Regeneration Ltd. We do "
        "residential refurbishment, mainly on the Woodhouse Close Estate here in Bishop "
        "Auckland. We've done a few properties down South Church Road too.\n\n"
        "DC HARTLEY: Are you a director of any other companies?\n\n"
        "HOLCOMBE: Bondgate Property Partners LLP, yes. And Prince Bishop Developments but "
        "that one's dormant. It has been for about three years.\n\n"
        "DC HARTLEY: What about Coundon Estates Ltd?\n\n"
        "HOLCOMBE: I was a director. I resigned. I don't know exactly when - my accountant "
        "handled that. You'd have to ask her.\n\n"
        "DC HARTLEY: Your accountant being?\n\n"
        "HOLCOMBE: Sasha Verity. She's local. She does the books for all the companies.\n\n"
        "DC HARTLEY: Does she have an office?\n\n"
        "HOLCOMBE: She works from home mostly. Somewhere out at Etherley Grange I think. "
        "She comes into the site office when she needs to."
    )

    pdf.chapter_title("Property transactions")
    pdf.body(
        "DS RAMSDEN: Let's turn to the properties on Woodhouse Close Estate. Can you tell us "
        "roughly how many of those properties were purchased by your companies between 2022 "
        "and today?\n\n"
        "HOLCOMBE: I'd say somewhere between fifteen and twenty. Wear Valley did most. A few "
        "were bought through Coundon Estates before I left that one.\n\n"
        "DS RAMSDEN: How were those purchases financed?\n\n"
        "HOLCOMBE: Mix of cash from retained profits and buy-to-let mortgages.\n\n"
        "DS RAMSDEN: Mortgages with which lender?\n\n"
        "HOLCOMBE: Mostly Newgate Mutual Building Society. They've got a branch on "
        "Newgate Street. They understand the local market.\n\n"
        "DS RAMSDEN: Do you have a particular contact at Newgate Mutual?\n\n"
        "HOLCOMBE: No, I just go in. Whoever's on the desk.\n\n"
        "DS RAMSDEN: Do you know a Mr Kevin Pryce?\n\n"
        "HOLCOMBE: The name doesn't ring a bell.\n\n"
        "DS RAMSDEN: Mr Pryce is a senior mortgage adviser at Newgate Mutual Bishop Auckland. "
        "He has personally authorised eleven of the mortgage applications for properties "
        "purchased by Wear Valley Regeneration between January 2023 and November 2024.\n\n"
        "HOLCOMBE: I deal with a lot of people. If he's handled paperwork I wouldn't "
        "necessarily remember the name.\n\n"
        "DS RAMSDEN: You've never met with Mr Pryce outside the branch?\n\n"
        "HOLCOMBE: No.\n\n"
        "DS RAMSDEN: Not at The Coach House cafe on Newgate Street?\n\n"
        "HOLCOMBE: I go to The Coach House. Plenty of people do. I don't remember meeting "
        "anyone called Pryce there."
    )

    pdf.chapter_title("Vehicle and communications")
    pdf.body(
        "DC HARTLEY: Do you own a black Range Rover, registration DH22 FRD?\n\n"
        "HOLCOMBE: Yes. It's on the drive at Etherley Lane. It's mine.\n\n"
        "DC HARTLEY: What mobile numbers do you use?\n\n"
        "HOLCOMBE: Just the one - +44 7811 456789. That's been my number for years.\n\n"
        "DC HARTLEY: Only that number?\n\n"
        "HOLCOMBE: Yes.\n\n"
        "DC HARTLEY: You don't have a second handset?\n\n"
        "HOLCOMBE: No.\n\n"
        "DC HARTLEY: Do you know a number ending in triple-two-three-three?\n\n"
        "HOLCOMBE: No comment."
    )

    pdf.chapter_title("International contacts")
    pdf.body(
        "DS RAMSDEN: Do you know a Valerija Tomic?\n\n"
        "HOLCOMBE: No.\n\n"
        "DS RAMSDEN: She's a Slovenian national. Lives in Ljubljana. Would that jog anything?\n\n"
        "HOLCOMBE: No.\n\n"
        "DS RAMSDEN: Have you personally, or any of your companies, sent funds to accounts "
        "in Slovenia, Liechtenstein, or the Isle of Man?\n\n"
        "MR WAINWRIGHT: My client will answer no comment to that.\n\n"
        "DS RAMSDEN: Are you familiar with a company called Tees Holdings (IOM) Ltd?\n\n"
        "HOLCOMBE: No comment.\n\n"
        "DS RAMSDEN: Interview suspended 11:47 at the request of the solicitor for private "
        "consultation."
    )

    pdf.chapter_title("Officer assessment")
    pdf.body(
        "Holcombe responded freely on matters of general business activity but declined to "
        "comment on any enquiry relating to Kevin Pryce's personal involvement, overseas "
        "transfers, the Isle of Man structure, or the unregistered mobile number ending "
        "2233. He did not recognise the name Valerija Tomic. He confirmed directorships of "
        "Wear Valley Regeneration Ltd, Bondgate Property Partners LLP, and a historic "
        "directorship of Coundon Estates Ltd. He admitted ownership of vehicle DH22 FRD and "
        "mobile number +44 7811 456789. He did not admit to ownership of any other handset "
        "but declined to comment when pressed. Recommend continued enquiries with Newgate "
        "Mutual Building Society regarding the Pryce relationship and further disclosure "
        "to support a second interview."
    )
    _save(pdf, "BA-INT-001.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# BA-FIN-002  Corporate Structure Analysis
# ──────────────────────────────────────────────────────────────────────────────
def gen_ba_fin_002() -> None:
    pdf = _pdf("BA-FIN-002", "CORPORATE STRUCTURE ANALYSIS")

    pdf.chapter_title("CORPORATE STRUCTURE ANALYSIS — BA-FIN-002")
    pdf.field("Prepared by", "Financial Investigation Unit, Durham Constabulary")
    pdf.field("Analyst", "FIO T. Belshaw")
    pdf.field("Date", "18 February 2025")
    pdf.field("Operation", "PRINCE BISHOP")
    pdf.field("Classification", "OFFICIAL-SENSITIVE")
    pdf.ln(3)

    pdf.body(
        "This analysis maps the corporate vehicles connected to Darren Holcombe "
        "(DOB 17/02/1976, 42 Etherley Lane, Bishop Auckland, DL14 7RB) as identified from "
        "Companies House filings, HMRC VAT records, and the Persons with Significant "
        "Control (PSC) register. All findings are as at 14 February 2025. Information is "
        "subject to change as late filings are processed.\n"
    )

    pdf.chapter_title("1. Wear Valley Regeneration Ltd")
    pdf.field("Company number", "12884410")
    pdf.field("Incorporated", "04 March 2020")
    pdf.field("Registered office", "18 Bondgate, Bishop Auckland, DL14 7JR")
    pdf.field("SIC code", "41100 - Development of building projects")
    pdf.field("Sole director", "Darren HOLCOMBE, appointed 04/03/2020")
    pdf.field("Company secretary", "Sasha VERITY, appointed 04/03/2020")
    pdf.field("PSC", "Darren HOLCOMBE - 75% or more of shares")
    pdf.field("Accounts", "Micro-entity, filed to 31 March 2024")
    pdf.field("Turnover declared", "GBP 1,874,220 (2023/24)")
    pdf.ln(2)
    pdf.body(
        "Wear Valley Regeneration Ltd is the principal operating entity. Bank account on "
        "record with the Financial Investigation Unit: sort code 20-71-04, account number "
        "40318822. This account received mortgage advances from Newgate Mutual Building "
        "Society across the period January 2023 to November 2024 (see BA-INT-004 for "
        "authorising officer detail).\n\n"
        "Wear Valley Regeneration Ltd paid out GBP 1,214,500 to Galgate Nominees Ltd over "
        "the same period under the invoice narrative GROUNDWORKS - WOODHOUSE CLOSE. These "
        "payments are the subject of further analysis in BA-FIN-005."
    )

    pdf.chapter_title("2. Bondgate Property Partners LLP")
    pdf.field("Company number", "OC433077")
    pdf.field("Incorporated", "22 August 2021")
    pdf.field("Registered office", "18 Bondgate, Bishop Auckland, DL14 7JR")
    pdf.field("Designated members", "Darren HOLCOMBE; Sasha VERITY")
    pdf.field("PSC", "Darren HOLCOMBE - significant influence or control")
    pdf.field("Accounts", "Small LLP, filed to 31 August 2024")
    pdf.ln(2)
    pdf.body(
        "Bondgate Property Partners LLP holds four residential titles on Woodhouse Close "
        "Estate and three on South Church Road. Bank account: sort code 30-97-86, account "
        "number 77210543. The LLP is a landlord entity and does not trade externally. "
        "Rental income from this entity is periodically distributed to members."
    )

    pdf.chapter_title("3. Coundon Estates Ltd")
    pdf.field("Company number", "11409877")
    pdf.field("Incorporated", "12 January 2018")
    pdf.field("Registered office", "18 Bondgate, Bishop Auckland, DL14 7JR")
    pdf.field("Current director", "Sasha VERITY, appointed 30/09/2022")
    pdf.field("Former director", "Darren HOLCOMBE, resigned 30/09/2022")
    pdf.field("PSC", "Tees Holdings (IOM) Ltd - 75% or more of shares")
    pdf.field("Accounts", "Small company, filed to 31 January 2024")
    pdf.ln(2)
    pdf.body(
        "Coundon Estates Ltd is the entity of most analytical interest. Following "
        "Mr Holcombe's resignation as director on 30 September 2022, the sole beneficial "
        "owner declared to Companies House is Tees Holdings (IOM) Ltd - an Isle of Man "
        "registered company (see section 5).\n\n"
        "Despite the change of ownership, the operational picture has not changed. The "
        "company secretary is still Sasha Verity. The registered office remains 18 Bondgate. "
        "Mortgage paperwork for two Coundon Estates purchases, submitted in 2023, continued "
        "to list Mr Holcombe's personal mobile +44 7811 456789 as the day-time contact. "
        "This raises concerns that the change of directorship is a structural nominee and "
        "that Mr Holcombe retains de facto control."
    )

    pdf.chapter_title("4. Galgate Nominees Ltd")
    pdf.field("Company number", "13502914")
    pdf.field("Incorporated", "06 July 2021")
    pdf.field("Registered office", "c/o Wainwright & Vane, 18 Bondgate, Bishop Auckland")
    pdf.field("Director", "Imran QURESHI, appointed 06/07/2021")
    pdf.field("PSC", "Imran QURESHI - 75% or more of shares")
    pdf.field("Declared activity", "41202 - Construction of domestic buildings")
    pdf.field("Employees (PAYE)", "0")
    pdf.ln(2)
    pdf.body(
        "Galgate Nominees Ltd is the invoice counterparty to Wear Valley Regeneration. "
        "Despite being declared to Companies House as a construction business, Galgate "
        "Nominees Ltd has no PAYE scheme, no VAT registration, no declared premises, and "
        "no declared plant or equipment. Full invoice-level analysis is in BA-FIN-005. "
        "Bank account: sort code 60-31-12, account number 88440099.\n\n"
        "Mr Qureshi's home address is recorded on the Companies House filing as "
        "19 Tindale Crescent, Bishop Auckland."
    )

    pdf.chapter_title("5. Tees Holdings (IOM) Ltd")
    pdf.field("Registered jurisdiction", "Isle of Man")
    pdf.field("Registered office", "Athol Chambers, Douglas, IM1 1SA")
    pdf.field("Incorporated", "19 May 2022")
    pdf.field("Declared director", "Athol Corporate Services Ltd (nominee)")
    pdf.field("Declared beneficial owner", "Redacted in IOM filings")
    pdf.field("UK PSC", "Not required to file (non-UK entity)")
    pdf.ln(2)
    pdf.body(
        "Tees Holdings (IOM) Ltd is the ultimate declared owner of Coundon Estates Ltd. No "
        "UK tax presence. The Isle of Man Financial Services Authority has been approached "
        "for beneficial-ownership disclosure under mutual-assistance provisions. That request "
        "is pending. Open-source research identifies historic correspondence between Tees "
        "Holdings (IOM) Ltd and Nova Ljubljanska Banka d.d., Ljubljana. The identity of the "
        "Slovenian-end beneficiary is developed further in BA-FIN-006 and BA-INT-008."
    )

    pdf.chapter_title("6. Registered office concentration")
    pdf.body(
        "Three UK companies in this structure are registered at 18 Bondgate, Bishop Auckland, "
        "DL14 7JR - the offices of Wainwright & Vane Solicitors. A fourth (Galgate Nominees "
        "Ltd) is registered at the same address 'care of' that practice. A detailed review "
        "of the solicitor's role is provided in BA-LEG-007. At this stage there is no "
        "finding of wrongdoing on the part of the practice; the concentration is noted as a "
        "factor in the structural picture, not a conclusion.\n\n"
        "This address is also the postal contact given by Mr Holcombe for correspondence "
        "in the Woodhouse Close mortgage applications."
    )

    pdf.chapter_title("7. Summary graph")
    pdf.body(
        "    Holcombe ---[director]---> Wear Valley Regeneration Ltd\n"
        "    Holcombe ---[director]---> Bondgate Property Partners LLP\n"
        "    Holcombe ---[former director]---> Coundon Estates Ltd\n"
        "    Verity   ---[secretary]---> Wear Valley, Coundon, Bondgate LLP\n"
        "    Coundon Estates Ltd ---[owned by]---> Tees Holdings (IOM) Ltd\n"
        "    Qureshi  ---[director]---> Galgate Nominees Ltd\n"
        "    Wear Valley ---[pays invoices to]---> Galgate Nominees Ltd\n"
        "    18 Bondgate ---[registered office of]---> Wear Valley, Bondgate LLP, Coundon"
    )
    _save(pdf, "BA-FIN-002.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# BA-SURV-003  Surveillance Log — Holcombe / Pryce meetings
# ──────────────────────────────────────────────────────────────────────────────
def gen_ba_surv_003() -> None:
    pdf = _pdf("BA-SURV-003", "SURVEILLANCE LOG")

    pdf.chapter_title("SURVEILLANCE LOG — BA-SURV-003")
    pdf.field("Operation", "PRINCE BISHOP")
    pdf.field("Authority", "RIPA directed-surveillance authority 24/DU/2311-S")
    pdf.field("Period", "15 October - 22 November 2024")
    pdf.field("Team", "Echo-2 (DS Coulthard lead, DC Marris, DC Wren)")
    pdf.field("Subjects", "Darren Holcombe; unknown male (later ID: Kevin Pryce)")
    pdf.field("Vehicles of interest", "DH22 FRD (Holcombe); YR69 VXT (later ID: Pryce)")
    pdf.ln(3)

    pdf.body(
        "This log summarises directed surveillance conducted against Darren Holcombe over "
        "a six-week period. The subject was followed on twelve separate occasions. On each "
        "occasion, Holcombe travelled to a coffee shop, The Coach House Cafe, at "
        "23 Newgate Street, Bishop Auckland, and was observed meeting the same male "
        "subject. That male subject was later identified from the registered keeper of "
        "YR69 VXT as Kevin Pryce, 9 Finkle Street, Bishop Auckland. The significant entries "
        "are reproduced below. Full contact notes are on the operational file."
    )

    pdf.chapter_title("17 October 2024 - Thursday")
    pdf.body(
        "10:22  DH22 FRD leaves 42 Etherley Lane. Driver: Holcombe. Alone in vehicle.\n\n"
        "10:41  DH22 FRD parks on Fore Bondgate adjacent to The Batts car park. Driver "
        "walks north on Bondgate then turns east onto Newgate Street.\n\n"
        "10:47  Holcombe enters The Coach House Cafe, 23 Newgate Street.\n\n"
        "10:49  A silver Audi A6, registration YR69 VXT, parks on Kingsway. Driver, male, "
        "approximately 45, medium build, dark coat. Driver walks to The Coach House.\n\n"
        "10:51  Second male enters cafe. Sits opposite Holcombe.\n\n"
        "10:51-11:34  Both males engaged in conversation. Holcombe passes a buff A4 "
        "envelope across the table. Second male places envelope in an internal pocket and "
        "does not look inside. Neither male is observed to take photographs or use a laptop.\n\n"
        "11:34  Second male leaves. YR69 VXT departs east on Newgate Street.\n\n"
        "11:38  Holcombe leaves. Returns to DH22 FRD via Bondgate. Departs 11:42.\n\n"
        "POST-SURVEILLANCE CHECK: YR69 VXT registered to Kevin PRYCE, 9 Finkle Street, "
        "Bishop Auckland, DL14 7QR. Subsequent checks confirm Pryce is employed at "
        "Newgate Mutual Building Society, 11 Newgate Street, as a senior mortgage adviser. "
        "His place of work is 140 metres from the cafe."
    )

    pdf.chapter_title("24 October 2024 - Thursday")
    pdf.body(
        "10:19  DH22 FRD leaves 42 Etherley Lane.\n\n"
        "10:43  Subject repeats the Bondgate park-and-walk pattern.\n\n"
        "10:50  Holcombe enters The Coach House Cafe. A female subject is already seated "
        "at his usual table.\n\n"
        "10:52  YR69 VXT arrives at Kingsway. Pryce walks to cafe, enters.\n\n"
        "10:53  All three subjects are seated together.\n\n"
        "11:02  Female identified on exit as Sasha Verity (cross-reference NPCC PND entry; "
        "known to officers from historic HMRC referral 2020). Verity carries a leather "
        "document wallet to the meeting and leaves with it closed. Verity is described as "
        "white female, approximately mid-forties, dark hair tied back, professional "
        "business attire.\n\n"
        "11:37  Meeting concludes. Verity leaves first, on foot, heading north. Pryce "
        "leaves second. Holcombe leaves third.\n\n"
        "ASSESSMENT: This is the first observed three-way meeting and the first observed "
        "presence of Verity alongside Holcombe and Pryce."
    )

    pdf.chapter_title("31 October, 7 November, 14 November, 21 November")
    pdf.body(
        "The pattern of 17 October was repeated on each Thursday morning of the period. On "
        "each occasion Holcombe arrived first and Pryce arrived second. On 14 November the "
        "meeting was shorter (29 minutes) and no envelope was passed. On 21 November Pryce "
        "arrived carrying what appeared to be a laptop bag; the bag was not opened during "
        "the meeting. No further presence of Verity was observed after 24 October.\n\n"
        "Surveillance assessment: the regularity (same day of the week, same cafe, same "
        "approximate arrival times) is inconsistent with a casual acquaintance and is "
        "inconsistent with a normal professional-client relationship between a property "
        "developer and a mortgage officer, which would ordinarily occur in the branch."
    )

    pdf.chapter_title("Ancillary observations")
    pdf.body(
        "On 7 November at 13:40 DH22 FRD was observed parked on Silver Street adjacent to "
        "18 Bondgate, the offices of Wainwright & Vane Solicitors. Holcombe was observed "
        "entering those premises and leaving 52 minutes later. No photography of interior "
        "was attempted.\n\n"
        "On 14 November at 16:12 YR69 VXT was observed in the car park of Newgate Mutual "
        "Building Society. Pryce returned to the vehicle at 17:05 carrying a branded "
        "Newgate Mutual document folder. This is consistent with normal end-of-day "
        "activity for a branch employee.\n\n"
        "On 21 November at 19:44 DH22 FRD was observed parked on Escomb Road in Toronto, "
        "a village to the northwest of Bishop Auckland. Holcombe entered a residential "
        "property and remained approximately 90 minutes. The resident is not known to "
        "this operation and further enquiries were not made."
    )

    pdf.chapter_title("Recommendations")
    pdf.body(
        "1. Obtain communications data for the handset recorded as in use by Mr Pryce at "
        "Newgate Mutual (work-issued) and Pryce's personal handset, to identify whether "
        "contact with Holcombe occurs outside of the observed in-person meetings.\n\n"
        "2. Obtain details of mortgage applications personally processed by Pryce and "
        "relating to entities in the Holcombe group (see BA-FIN-002). Particular focus on "
        "properties on Woodhouse Close Estate.\n\n"
        "3. Consider whether sufficient material exists for an interview of Pryce under "
        "caution. Recommend this be advanced (see BA-INT-004)."
    )
    _save(pdf, "BA-SURV-003.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# BA-INT-004  Interview — Kevin Pryce
# ──────────────────────────────────────────────────────────────────────────────
def gen_ba_int_004() -> None:
    pdf = _pdf("BA-INT-004", "INTERVIEW UNDER CAUTION")

    pdf.chapter_title("INTERVIEW RECORD — KEVIN PRYCE")
    pdf.field("URN", "24/DU/2311-B")
    pdf.field("Date", "26 February 2025")
    pdf.field("Location", "Durham HQ, Aykley Heads, Interview Suite 4")
    pdf.field("Interviewing officers", "DS Ramsden, DC Okafor")
    pdf.field("Subject", "Kevin Pryce, DOB 04/09/1979")
    pdf.field("Subject address", "9 Finkle Street, Bishop Auckland, DL14 7QR")
    pdf.field("Occupation", "Senior mortgage adviser, Newgate Mutual Building Society")
    pdf.field("Solicitor", "Ms J. Dearden (Dearden Law, duty)")
    pdf.field("Status", "Voluntary attendance under caution")
    pdf.ln(3)

    pdf.chapter_title("Employment and role")
    pdf.body(
        "DS RAMSDEN: Mr Pryce, can you describe your role?\n\n"
        "PRYCE: I've been at Newgate Mutual for seventeen years. I'm senior mortgage "
        "adviser for the Bishop Auckland branch, which covers Bishop Auckland, Crook, "
        "Willington, West Auckland and the rural area out to Staindrop. I have delegated "
        "underwriting authority up to GBP 500,000 for residential applications and "
        "GBP 750,000 on buy-to-let within certain criteria.\n\n"
        "DS RAMSDEN: What's the branch address?\n\n"
        "PRYCE: 11 Newgate Street, Bishop Auckland.\n\n"
        "DS RAMSDEN: And you are a signatory on mortgage approval decisions?\n\n"
        "PRYCE: Yes, within my delegated limits. Above that it goes to Regional."
    )

    pdf.chapter_title("Relationship with Darren Holcombe")
    pdf.body(
        "DS RAMSDEN: Do you know Darren Holcombe?\n\n"
        "PRYCE: He's a customer.\n\n"
        "DS RAMSDEN: Since when?\n\n"
        "PRYCE: Maybe 2021. He brought business from a company he runs.\n\n"
        "DS RAMSDEN: Which company?\n\n"
        "PRYCE: Wear Valley Regeneration, I think. And another one - Bondgate something.\n\n"
        "DS RAMSDEN: How many mortgages have you personally authorised for Mr Holcombe or "
        "his companies?\n\n"
        "PRYCE: I'd need to check. Several.\n\n"
        "DS RAMSDEN: Our records from the building society, produced under a production "
        "order, show eleven mortgage applications authorised by you, totalling "
        "GBP 2.87 million in advances, between January 2023 and November 2024. Is that "
        "consistent with what you'd expect?\n\n"
        "PRYCE: That sounds high but it's possible. Wear Valley has been buying a lot on "
        "Woodhouse Close.\n\n"
        "DS RAMSDEN: Ten of the eleven applications used the same valuer - Mr R. Kenworthy. "
        "Are you aware that Mr Kenworthy's valuations on the Woodhouse Close properties "
        "averaged 34% above comparable-transaction evidence in the same postcode?\n\n"
        "MS DEARDEN: My client will answer no comment to that question.\n\n"
        "PRYCE: No comment."
    )

    pdf.chapter_title("The Coach House meetings")
    pdf.body(
        "DC OKAFOR: Mr Pryce, we have surveillance evidence showing you meeting Mr Holcombe "
        "at The Coach House Cafe on Newgate Street on at least six occasions between "
        "October and November 2024. Can you tell me about that?\n\n"
        "PRYCE: I meet customers for coffee. That's not unusual.\n\n"
        "DC OKAFOR: Six separate Thursdays, always around 10:50?\n\n"
        "PRYCE: I take a break when I can. If Darren's in town we catch up.\n\n"
        "DC OKAFOR: On 24 October you were joined by a woman. Can you tell us who she was?\n\n"
        "PRYCE: I don't remember a woman being there.\n\n"
        "DC OKAFOR: Her name, Mr Pryce, is Sasha Verity. Does that name mean anything?\n\n"
        "PRYCE: I might have met a Sacha at some point. She does the books for Darren. "
        "If she was there, it was probably to go through paperwork.\n\n"
        "DC OKAFOR: For the record, the paperwork was being passed in a buff A4 envelope. "
        "Why was it not being sent through the building society's document system?\n\n"
        "PRYCE: Some customers still prefer paper.\n\n"
        "DC OKAFOR: Including when the paperwork relates to a mortgage application that "
        "is already open in the building society's system?\n\n"
        "PRYCE: No comment."
    )

    pdf.chapter_title("Mobile devices")
    pdf.body(
        "DS RAMSDEN: What mobile numbers do you use?\n\n"
        "PRYCE: My personal number is +44 7900 812345. I also have a work-issued phone "
        "but I don't remember the number off the top of my head.\n\n"
        "DS RAMSDEN: Only the one personal handset?\n\n"
        "PRYCE: Yes.\n\n"
        "DS RAMSDEN: Have you ever been in contact with a number ending 2233?\n\n"
        "PRYCE: Not that I'm aware.\n\n"
        "DS RAMSDEN: Have you heard the name Valerija Tomic?\n\n"
        "PRYCE: No.\n\n"
        "DS RAMSDEN: Tees Holdings (IOM) Ltd?\n\n"
        "PRYCE: No."
    )

    pdf.chapter_title("Closing account")
    pdf.body(
        "PRYCE: Can I say something? I process volume. I authorise fifty applications a "
        "month. If some of Darren's paperwork was wrong I wouldn't necessarily have spotted "
        "it. That's on the valuer and on Darren. Not me.\n\n"
        "DS RAMSDEN: Would you like to revise any earlier answer in light of that?\n\n"
        "PRYCE: No.\n\n"
        "Interview concluded 15:54. Mr Pryce released pending further enquiries. "
        "Condition: surrender of passport; no contact with Holcombe, Verity, or Wainwright."
    )

    pdf.chapter_title("Officer note")
    pdf.body(
        "Pryce acknowledged that Sasha Verity (mis-pronounced as Sacha) is known to him as "
        "Mr Holcombe's bookkeeper and that she may have attended at least one meeting at "
        "The Coach House. He did not acknowledge the surveillance pattern of regular "
        "Thursday meetings and offered innocent explanations. He denied knowledge of "
        "Valerija Tomic and Tees Holdings (IOM) Ltd. He declined to account for the "
        "elevated valuations by Mr Kenworthy or for the passing of documents outside the "
        "building society's system. Recommend referral to the Prudential Regulation "
        "Authority and notification to Newgate Mutual's Money Laundering Reporting Officer "
        "in parallel with the criminal investigation."
    )
    _save(pdf, "BA-INT-004.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# BA-FIN-005  Invoice Chain Analysis
# ──────────────────────────────────────────────────────────────────────────────
def gen_ba_fin_005() -> None:
    pdf = _pdf("BA-FIN-005", "INVOICE CHAIN ANALYSIS")

    pdf.chapter_title("INVOICE CHAIN ANALYSIS — BA-FIN-005")
    pdf.field("Prepared by", "Financial Investigation Unit, Durham Constabulary")
    pdf.field("Analyst", "FIO M. Lightfoot")
    pdf.field("Date", "4 March 2025")
    pdf.field("Operation", "PRINCE BISHOP")
    pdf.ln(3)

    pdf.body(
        "This report analyses the invoicing pattern between Wear Valley Regeneration Ltd "
        "(company 12884410, sort code 20-71-04, account 40318822), Galgate Nominees Ltd "
        "(company 13502914, sort code 60-31-12, account 88440099), and the personal "
        "account of Mr Imran Qureshi (sort code 09-01-29, account 11550088). The "
        "investigation concerns payments purporting to be for construction and "
        "groundworks services at Woodhouse Close Estate, Bishop Auckland.\n\n"
        "The underlying assertion under test is that Galgate Nominees Ltd did not perform "
        "the services for which it was paid. If that is correct, the payments constitute "
        "the circulation of mortgage advance funds out of a declared business purpose and "
        "into private hands, with the appearance of legitimate trade expenses."
    )

    pdf.chapter_title("Invoice schedule")
    pdf.body(
        "The following invoices were issued by Galgate Nominees Ltd to Wear Valley "
        "Regeneration Ltd (all reproduced from originals seized under warrant at "
        "42 Etherley Lane on 11 February 2025 and cross-checked against bank records):\n\n"
        "  INV-G-0114  2023-02-03  GBP 76,400   'Groundworks, Woodhouse Close plot 14'\n"
        "  INV-G-0118  2023-04-11  GBP 88,120   'Groundworks, Woodhouse Close plot 18'\n"
        "  INV-G-0123  2023-06-02  GBP 112,850  'Groundworks + drainage, plots 22/23'\n"
        "  INV-G-0129  2023-08-27  GBP 96,300   'Groundworks, plot 29'\n"
        "  INV-G-0137  2023-11-19  GBP 134,700  'Groundworks + access road, plot 37'\n"
        "  INV-G-0142  2024-01-21  GBP 71,900   'Remediation, plot 42'\n"
        "  INV-G-0150  2024-04-08  GBP 188,400  'Groundworks + foundations, plots 49/50'\n"
        "  INV-G-0156  2024-06-14  GBP 102,330  'Groundworks, plot 56'\n"
        "  INV-G-0163  2024-09-05  GBP 156,100  'Groundworks, plots 62/63'\n"
        "  INV-G-0168  2024-10-23  GBP 101,450  'Remediation, plot 68'\n"
        "  INV-G-0171  2024-11-18  GBP 85,950   'Groundworks, plot 71'\n\n"
        "                              TOTAL: GBP 1,214,500\n\n"
        "All invoices follow an identical template. All carry the registered office "
        "18 Bondgate, Bishop Auckland. All were paid by Wear Valley Regeneration Ltd by "
        "faster payment from sort code 20-71-04, account 40318822, to Galgate Nominees "
        "Ltd at sort code 60-31-12, account 88440099. Payments occurred between one and "
        "six working days of each invoice date."
    )

    pdf.chapter_title("Indicators of non-performance")
    pdf.body(
        "(a) Galgate Nominees Ltd has no PAYE scheme and has declared zero employees "
        "since incorporation (06 July 2021). The invoices describe works requiring "
        "multiple operatives.\n\n"
        "(b) Galgate Nominees Ltd has no VAT registration. Invoices at the values shown "
        "would in each case exceed the VAT registration threshold for the preceding "
        "rolling twelve months.\n\n"
        "(c) Galgate Nominees Ltd has no plant-and-equipment register, no declared yard, "
        "and no hire agreements with any of the five national plant-hire companies serving "
        "the Teesside / County Durham area during the relevant period.\n\n"
        "(d) Environmental Health records for the Woodhouse Close Estate show that no "
        "planning-condition discharge was applied for in connection with 'remediation' on "
        "plots 42 or 68, despite the invoices describing remediation works.\n\n"
        "(e) Photographs of plot 42 and plot 68 taken in October 2024 by Durham County "
        "Council enforcement show those plots in an undeveloped state inconsistent with "
        "the remediation invoices."
    )

    pdf.chapter_title("Onward transfer to Mr Qureshi")
    pdf.body(
        "Bank analysis of Galgate Nominees Ltd (sort 60-31-12, account 88440099) reveals "
        "a single repeating outbound pattern. Within between two and nine working days of "
        "each Wear Valley payment received, Galgate Nominees Ltd paid a smaller sum "
        "(between 20% and 32% of the amount received) to sort code 09-01-29, account "
        "number 11550088, in the name of I. QURESHI. Reference on most transfers: "
        "'SUB-CONTRACT'.\n\n"
        "Total outbound to Mr Qureshi's personal account over the period: GBP 341,780. "
        "The balance of funds received by Galgate Nominees Ltd was then transferred "
        "internationally (see BA-FIN-006)."
    )

    pdf.chapter_title("Capacity of Mr Qureshi")
    pdf.body(
        "Mr Imran Qureshi, 19 Tindale Crescent, Bishop Auckland, is the sole director of "
        "Galgate Nominees Ltd. Enquiries with DWP and HMRC establish that Mr Qureshi has "
        "claimed means-tested benefits continuously since 2019, declared no self-employed "
        "income for the 2022/23 or 2023/24 tax years, and has no CITB or CSCS registrations "
        "that would be consistent with directing construction operations at the scale "
        "invoiced. Mr Qureshi suffered a serious industrial injury in 2017 and the "
        "medical record indicates he has been unable to undertake manual work since that "
        "date. Mr Qureshi's name sometimes appears in correspondence as 'Quereshi' - this "
        "is a transcription error by the letting agent and by Newgate Mutual's CRM."
    )

    pdf.chapter_title("Working hypothesis")
    pdf.body(
        "The invoicing pattern is consistent with Galgate Nominees Ltd being a paper "
        "entity used to extract funds from Wear Valley Regeneration Ltd under the cover "
        "of contractor payments. Mr Qureshi's account appears to be a pass-through for a "
        "fraction of the proceeds - its scale (approx 28% of gross received) is consistent "
        "with a facilitator commission. The remaining 72% of Galgate Nominees Ltd's "
        "inflows were routed internationally via Tees Holdings (IOM) Ltd, the onward "
        "transfer analysis of which is set out in BA-FIN-006."
    )
    _save(pdf, "BA-FIN-005.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# BA-FIN-006  Offshore Trace
# ──────────────────────────────────────────────────────────────────────────────
def gen_ba_fin_006() -> None:
    pdf = _pdf("BA-FIN-006", "OFFSHORE FINANCIAL TRACE")

    pdf.chapter_title("OFFSHORE TRACE — BA-FIN-006")
    pdf.field("Prepared by", "Financial Investigation Unit, Durham Constabulary")
    pdf.field("Analyst", "FIO T. Belshaw")
    pdf.field("Co-authors", "NCRO liaison; IOM FSA liaison")
    pdf.field("Date", "11 March 2025")
    pdf.field("Operation", "PRINCE BISHOP")
    pdf.field("Classification", "OFFICIAL-SENSITIVE - international distribution")
    pdf.ln(3)

    pdf.body(
        "This report traces the onward movement of funds from Galgate Nominees Ltd "
        "(sort code 60-31-12, account 88440099) and from Coundon Estates Ltd to a "
        "non-resident holding structure, and thence to a natural-person account in "
        "the Republic of Slovenia. The report combines data obtained via mutual legal "
        "assistance with the Isle of Man Financial Services Authority and via liaison "
        "with the Slovenian financial police (Urad Republike Slovenije za preprecevanje "
        "pranja denarja)."
    )

    pdf.chapter_title("Stage 1 - UK to Isle of Man")
    pdf.body(
        "Between March 2023 and December 2024, Galgate Nominees Ltd made 17 outbound "
        "international transfers totalling GBP 812,650 to:\n\n"
        "  Beneficiary:   TEES HOLDINGS (IOM) LTD\n"
        "  Bank:          Isle of Man Bank (part of NatWest Group)\n"
        "  Address:       Athol Chambers, Douglas, Isle of Man, IM1 1SA\n"
        "  IBAN:          IM64 NWBK 5600 2321 0099 44\n\n"
        "Transfer references included 'PROFESSIONAL SERVICES', 'CONSULTANCY', and on two "
        "occasions 'LOAN REPAYMENT'. No underlying loan agreement between Galgate "
        "Nominees Ltd and Tees Holdings (IOM) Ltd has been produced by either counterparty.\n\n"
        "Coundon Estates Ltd, which declares Tees Holdings (IOM) Ltd as its 75%+ "
        "beneficial owner, additionally distributed GBP 246,200 to the same IBAN over "
        "the same period, under the reference 'DISTRIBUTION TO MEMBER'. Coundon's "
        "accounts describe the company as being in a pre-trading phase. This is not "
        "consistent with the scale of the distribution."
    )

    pdf.chapter_title("Stage 2 - Isle of Man structure")
    pdf.body(
        "Tees Holdings (IOM) Ltd is administered by Athol Corporate Services Ltd, "
        "providing nominee director and nominee company secretary. Under the mutual "
        "assistance request of 29 January 2025, the IOM FSA has provided limited "
        "disclosure:\n\n"
        "  - The declared beneficial owner of Tees Holdings (IOM) Ltd is named in the "
        "    IOM register as 'V. TOMIC', of an address in Ljubljana, Slovenia.\n"
        "  - The beneficial ownership was declared at incorporation (19 May 2022) and "
        "    has not been amended.\n"
        "  - No other natural person is named in the IOM file.\n\n"
        "The IOM file additionally records that the introducing client for Tees "
        "Holdings (IOM) Ltd was 'Wainwright & Vane Solicitors, Bishop Auckland, UK'. "
        "See BA-LEG-007 for the solicitor's role; that role remains under evaluation."
    )

    pdf.chapter_title("Stage 3 - Isle of Man to Slovenia")
    pdf.body(
        "Tees Holdings (IOM) Ltd made outbound transfers from IBAN "
        "IM64 NWBK 5600 2321 0099 44 to:\n\n"
        "  Beneficiary:   VALERIJA TOMIC\n"
        "  Bank:          Nova Ljubljanska Banka d.d., Ljubljana\n"
        "  IBAN:          SI56 0208 3001 6789 012\n"
        "  Address:       Tacenska cesta 97, Ljubljana, Slovenia\n\n"
        "Over the 21-month period the cumulative outbound sum was EUR 1,037,440 "
        "equivalent. Transfers were in batches timed to follow receipts from the UK, "
        "with a typical lag of between 5 and 14 days. References on transfers were "
        "uniform: 'DIVIDEND'."
    )

    pdf.chapter_title("Identity of V. Tomic")
    pdf.body(
        "Slovenian police have provided the following identification:\n\n"
        "  Name:          Valerija TOMIC (also appears in Slovenian registers as Tomic "
        "                 with a diacritic on the final c)\n"
        "  DOB:           11 May 1990\n"
        "  Nationality:   Slovenian\n"
        "  Address:       Tacenska cesta 97, 1000 Ljubljana\n"
        "  Occupation:    Declared as 'private investor'. No declared employer for the "
        "                 period 2022 to date.\n\n"
        "Ms Tomic has no record of UK immigration presence that would be consistent with "
        "her being the operational decision-maker for Tees Holdings (IOM) Ltd. Travel "
        "records show short visits to the UK in May 2022, August 2023 and March 2024 "
        "(see BA-INT-008). Those visits coincide with incorporation and key corporate "
        "events for the UK structure.\n\n"
        "The Slovenian police assessment, shared informally, is that Ms Tomic is likely "
        "acting as a beneficial-ownership nominee on behalf of a UK-resident principal. "
        "That assessment is consistent with this Unit's assessment."
    )

    pdf.chapter_title("Reconciliation with UK investigation")
    pdf.body(
        "The cumulative outbound to Slovenia (EUR equivalent GBP 890,000 approximately) "
        "aligns closely with the difference between (i) Galgate Nominees Ltd inflows "
        "(GBP 1.21m from Wear Valley Regeneration Ltd) and (ii) Galgate Nominees Ltd "
        "outflows to Mr Qureshi (GBP 0.34m). This is consistent with the hypothesis that "
        "the Isle of Man structure is being used as a conduit rather than as a "
        "destination of funds.\n\n"
        "The ultimate UK-resident principal of the Tees Holdings (IOM) Ltd structure "
        "remains to be proven. Circumstantial evidence supporting Mr Holcombe as that "
        "principal includes (a) his historic directorship of Coundon Estates Ltd, "
        "(b) his personal mobile appearing as daytime contact on Coundon mortgage "
        "applications after the directorship change, and (c) the concentration of the "
        "corporate filings at Wainwright & Vane Solicitors, which also introduced the "
        "IOM structure."
    )
    _save(pdf, "BA-FIN-006.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# BA-LEG-007  Solicitor Activity Report
# ──────────────────────────────────────────────────────────────────────────────
def gen_ba_leg_007() -> None:
    pdf = _pdf("BA-LEG-007", "SOLICITOR ACTIVITY REPORT")

    pdf.chapter_title("SOLICITOR ACTIVITY REPORT — BA-LEG-007")
    pdf.field("Prepared by", "DC A. Farnham, Economic Crime Team")
    pdf.field("Reviewing officer", "DI S. Broadwood")
    pdf.field("Date", "14 March 2025")
    pdf.field("Operation", "PRINCE BISHOP")
    pdf.field("Classification", "OFFICIAL-SENSITIVE - handle under Legal Professional Privilege")
    pdf.ln(3)

    pdf.body(
        "This report reviews the conveyancing and company-services activity of Wainwright "
        "& Vane Solicitors (SRA 635112), 18 Bondgate, Bishop Auckland, DL14 7JR, in "
        "relation to the property portfolio of the Holcombe group of companies. The "
        "review is based on production orders against the firm's client ledger, the "
        "SDLT5 filings from HMRC, and Land Registry transfers. This report does not "
        "assert wrongdoing by the firm and none of its correspondence is reproduced here. "
        "The purpose of this report is to map the firm's factual role."
    )

    pdf.chapter_title("Principals and structure of the firm")
    pdf.body(
        "Wainwright & Vane is a long-established general practice in Bishop Auckland. "
        "The current partnership is:\n\n"
        "  - Gerald WAINWRIGHT (senior partner). Admitted 1992. Head of residential "
        "    conveyancing.\n"
        "  - Stephen VANE (partner). Admitted 2001. Head of commercial.\n"
        "  - Helen NIXON (salaried partner). Admitted 2011. Family.\n\n"
        "The firm employs 14 staff, of whom 5 are fee-earners. The firm has held an "
        "unqualified SRA accounts report for each of the last five years."
    )

    pdf.chapter_title("Conveyancing activity for the Holcombe group")
    pdf.body(
        "Across the period January 2022 to November 2024, Wainwright & Vane acted for "
        "the buyer on 23 of the 24 residential purchases completed by entities in the "
        "Holcombe group. The single exception was a transfer between Wear Valley "
        "Regeneration Ltd and Bondgate Property Partners LLP in April 2023, which was "
        "handled in-house by Ms Verity as a group transfer.\n\n"
        "In every one of the 23 external purchases the firm was instructed by "
        "Mr G. Wainwright personally. Junior fee-earners did not appear on the client "
        "ledger as conducting officers for any of these matters. This concentration is "
        "noted: in the firm's residential caseload as a whole, Mr Wainwright personally "
        "conducts approximately 22% of matters. His concentration on the Holcombe "
        "caseload is therefore materially greater than baseline.\n\n"
        "Enquiries with the Newgate Mutual Building Society, Bishop Auckland branch, "
        "confirm that in every one of the 23 purchases the firm was also acting for the "
        "lender. Dual representation is permitted under CLA 2011 where conditions are "
        "met, but it is relevant to the investigative picture that the same solicitor "
        "acted for both the property-buying entity and the lender, where the lending "
        "decision was taken by Mr Pryce under delegated authority."
    )

    pdf.chapter_title("Use of the firm's address")
    pdf.body(
        "18 Bondgate is the registered office of:\n\n"
        "  - Wear Valley Regeneration Ltd\n"
        "  - Bondgate Property Partners LLP\n"
        "  - Coundon Estates Ltd\n"
        "  - Galgate Nominees Ltd (care-of address)\n\n"
        "The firm charges an annual registered-office fee for each entity. This is a "
        "routine service offered by many high-street firms and is not of itself "
        "suspicious. It is noted because, in combination with the firm acting for the "
        "buyer in each transaction, it creates a concentration of professional touchpoints "
        "around the structure.\n\n"
        "The firm also acted as introducing client for the formation of Tees Holdings "
        "(IOM) Ltd via an Isle of Man agent in May 2022. The firm's position, stated in "
        "correspondence with the SRA, is that it acted on the instructions of a client "
        "whose identity is subject to legal professional privilege. The firm has indicated "
        "it will cooperate with any properly-drawn production order."
    )

    pdf.chapter_title("Historic reference — Operation Teesdale (2021)")
    pdf.body(
        "A footnote, rather than a conclusion. In 2021 the firm acted for an unrelated "
        "client who was later prosecuted in Operation Teesdale, case reference "
        "OP_TEESDALE, URN 21/DU/1109. The firm was not charged with any offence in that "
        "matter. The SRA conducted a thematic review in 2022 and took no action. That "
        "prior history is noted to complete the picture, and because the same fee-earner "
        "(Mr Wainwright) conducted the relevant matter in that operation. It is not "
        "asserted here that the prior history is probative in relation to Operation "
        "Prince Bishop."
    )

    pdf.chapter_title("Conclusion and recommendation")
    pdf.body(
        "Wainwright & Vane occupy a factual position at the centre of the corporate and "
        "conveyancing map of the Holcombe group. At the present state of enquiries there "
        "is no evidence of wrongdoing by any partner in the firm. However, the "
        "concentration of roles - conveyancer for buyer and for lender, registered office "
        "for four of the five UK group entities, and introducing agent for the Isle of "
        "Man structure - is sufficiently unusual to warrant continued scrutiny. Recommend "
        "a formal interview of Mr Wainwright as a witness, not a suspect, at a time "
        "after the disclosure position against Mr Holcombe and Mr Pryce is fully "
        "developed."
    )
    _save(pdf, "BA-LEG-007.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# BA-INT-008  Interpol / Slovenian liaison on Tomic
# ──────────────────────────────────────────────────────────────────────────────
def gen_ba_int_008() -> None:
    pdf = _pdf("BA-INT-008", "INTERNATIONAL LIAISON REPORT")

    pdf.chapter_title("INTERNATIONAL LIAISON — BA-INT-008")
    pdf.field("Prepared by", "DS Ramsden (UK lead)")
    pdf.field("Slovenian counterpart", "inspektor I. Novak, Uprava kriminalisticne policije")
    pdf.field("Channel", "INTERPOL i24/7 and bilateral MLA")
    pdf.field("Date", "18 March 2025")
    pdf.field("Operation", "PRINCE BISHOP")
    pdf.ln(3)

    pdf.body(
        "This report records the substance of information received from Slovenian "
        "authorities on Valerija Tomic, and the account she holds at Nova Ljubljanska "
        "Banka d.d. It summarises interview material gathered by Slovenian officers and "
        "travel-records data. No material in this report may be used in UK criminal "
        "proceedings without further process under the relevant MLA instrument."
    )

    pdf.chapter_title("Personal details")
    pdf.body(
        "Name: Valerija Tomic (Tomic with diacritic in Slovenian records)\n"
        "Date of birth: 11/05/1990\n"
        "Nationality: Slovenian; no other passport disclosed\n"
        "Address: Tacenska cesta 97, 1000 Ljubljana\n"
        "Occupation given: 'private investor'\n"
        "Declared employment 2018-2024: none\n"
        "Declared income 2020-2024: dividends only\n"
        "Known family: mother Jadranka Tomic (DOB 1962); no recorded partner; no "
        "recorded children\n"
        "Criminal record (Slovenia): nil"
    )

    pdf.chapter_title("Account")
    pdf.body(
        "Ms Tomic holds the following principal account:\n\n"
        "  Bank:       Nova Ljubljanska Banka d.d.\n"
        "  IBAN:       SI56 0208 3001 6789 012\n"
        "  Opened:     23 May 2022 (four days after incorporation of Tees Holdings (IOM) "
        "              Ltd)\n"
        "  Inflows:    EUR 1,037,440 in 14 batches between June 2022 and December 2024\n"
        "  Inflows source: Tees Holdings (IOM) Ltd via Isle of Man Bank\n"
        "  Outflows:  Property purchase in Ljubljana suburb Sentvid (EUR 380,000); "
        "             purchase of BMW 5 Series vehicle (EUR 68,000); "
        "             cash withdrawals in multiple tranches below Slovenian reporting "
        "             threshold (EUR 380,000 cumulative); remaining balance invested "
        "             in a Slovenian-domiciled investment fund\n\n"
        "The pattern of cash withdrawals below threshold is a classic smurfing pattern "
        "and in itself has triggered a Slovenian-side Suspicious Transaction Report. "
        "That STR is on the file."
    )

    pdf.chapter_title("Travel records")
    pdf.body(
        "Slovenian authorities have shared Ms Tomic's international travel where "
        "disclosed to them by airline manifest data:\n\n"
        "  17-19 May 2022:   Ljubljana-Frankfurt-Newcastle. Stayed Bishop Auckland "
        "                    (short-term let, Fore Bondgate). Returned via Manchester.\n"
        "  04-07 Aug 2023:   Ljubljana-Munich-Newcastle. Stayed Durham city centre.\n"
        "  11-13 Mar 2024:   Ljubljana-Amsterdam-Newcastle. Stayed Bishop Auckland "
        "                    (short-term let, Fore Bondgate).\n\n"
        "No other UK entries are recorded. Each UK visit coincides with a key corporate "
        "event:\n"
        "  - May 2022 visit coincides with incorporation of Tees Holdings (IOM) Ltd (19 "
        "    May 2022) and with her Nova Ljubljanska Banka account opening (23 May 2022).\n"
        "  - August 2023 visit coincides with the completion of three Woodhouse Close "
        "    purchases by Coundon Estates Ltd.\n"
        "  - March 2024 visit coincides with a step-change upward in Galgate Nominees "
        "    Ltd monthly billings to Wear Valley Regeneration Ltd."
    )

    pdf.chapter_title("Slovenian interview summary")
    pdf.body(
        "Ms Tomic attended a voluntary interview with Slovenian police on 28 February "
        "2025. She confirmed the account. She stated that the funds received were a "
        "'return on a family trust investment' but did not name a trust or produce trust "
        "documents. She declined to name any UK contact. When shown a photograph of "
        "Darren Holcombe she stated she did not recognise him. When shown a photograph "
        "of Sasha Verity she stated 'maybe I have met this woman but I cannot recall "
        "where'. She denied ever having been in Bishop Auckland despite the travel "
        "records above. When this was pointed out she asked for the interview to be "
        "concluded.\n\n"
        "Slovenian officers assess Ms Tomic as a nominee and not a principal. The UK "
        "team accepts that assessment pending further evidence."
    )

    pdf.chapter_title("Short-term let — Fore Bondgate")
    pdf.body(
        "Enquiries in the UK with the short-term let operator on Fore Bondgate, Bishop "
        "Auckland, confirm that the booking in May 2022 was made and paid for by a "
        "corporate account in the name of Wear Valley Regeneration Ltd. The booking in "
        "March 2024 was made by Ms Tomic personally. This is the first direct financial "
        "touchpoint linking Wear Valley Regeneration Ltd to Ms Tomic's presence in the "
        "UK and is of material evidential value."
    )

    pdf.chapter_title("Assessment and next step")
    pdf.body(
        "The international liaison supports the hypothesis that Ms Tomic is the public "
        "face of a UK-resident beneficial owner, and that she is not the real principal. "
        "The single direct UK-side evidential link between her travel and the subjects "
        "of this investigation is the May 2022 short-term let paid for by Wear Valley "
        "Regeneration Ltd. That link should be put to Mr Holcombe in a second interview "
        "along with the full financial picture (see BA-SUM-010 for disclosure-readiness "
        "review)."
    )
    _save(pdf, "BA-INT-008.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# BA-TEC-009  Telecoms / Cell-Site Analysis
# ──────────────────────────────────────────────────────────────────────────────
def gen_ba_tec_009() -> None:
    pdf = _pdf("BA-TEC-009", "TELECOMS & CELL-SITE ANALYSIS")

    pdf.chapter_title("TELECOMS & CELL-SITE ANALYSIS — BA-TEC-009")
    pdf.field("Prepared by", "Digital Forensics Unit")
    pdf.field("Analyst", "DC Holroyd")
    pdf.field("Authority", "Communications data warrant 24/DU/2311-C")
    pdf.field("Period", "01 October 2023 - 28 February 2025")
    pdf.field("Operation", "PRINCE BISHOP")
    pdf.ln(3)

    pdf.body(
        "Communications data has been obtained from all three UK mobile network operators "
        "for the numbers in scope. This report summarises the material findings. Call "
        "charts and cell-site maps are appended to the operational file."
    )

    pdf.chapter_title("Numbers in scope")
    pdf.body(
        "  +44 7811 456789  (Darren Holcombe, registered, admitted)\n"
        "  +44 7900 812345  (Kevin Pryce, registered, admitted)\n"
        "  +44 7765 432100  (Sasha Verity, registered)\n"
        "  +44 7488 112233  (unregistered, pay-as-you-go SIM - subject of this report)\n\n"
        "The number +44 7488 112233 appears across seized documentation in three "
        "different formats: '+447488112233' in contact lists extracted from Mr Qureshi's "
        "handset; '07488 112 233' handwritten in a notebook at 42 Etherley Lane; and "
        "'0-7488-112233' in a contact card on Ms Verity's laptop. These are the same "
        "number. The standardised form used in the remainder of this report is "
        "+44 7488 112233."
    )

    pdf.chapter_title("Registered handsets - contact patterns")
    pdf.body(
        "Holcombe's registered number +44 7811 456789 has light contact with the other "
        "two registered handsets:\n\n"
        "  - to Pryce +44 7900 812345: 9 calls in 17 months, all during business hours, "
        "    average 2 min 40 seconds\n"
        "  - to Verity +44 7765 432100: 36 calls in 17 months, mostly end-of-month, "
        "    average 8 min 10 seconds\n\n"
        "This level of contact is not inconsistent with a legitimate "
        "developer/accountant/mortgage-broker triangle. Taken alone, the registered-handset "
        "traffic would not raise investigative concern."
    )

    pdf.chapter_title("The unregistered handset +44 7488 112233")
    pdf.body(
        "The unregistered pay-as-you-go number +44 7488 112233 was first active on "
        "14 September 2023 and remained active throughout the review period. Top-ups are "
        "paid for in cash at two convenience stores on Cockton Hill Road. The handset "
        "has never been associated with a contract, with a bank card, or with an account "
        "held in any name.\n\n"
        "Traffic analysis:\n\n"
        "  - to Pryce +44 7900 812345: 241 calls, most frequent evening contact outside "
        "    Newgate Mutual hours; average 6 min 45 seconds; regular Thursday-evening "
        "    pattern ahead of the Friday surveillance meetings recorded in BA-SURV-003\n"
        "  - to Verity +44 7765 432100: 178 calls, regular early-morning pattern\n"
        "  - to Qureshi's handset: 412 calls, very heavy traffic, often around the "
        "    dates of the Wear Valley -> Galgate payments in BA-FIN-005\n"
        "  - to +386 xxx xxx (Slovenian mobile, registered to V. Tomic): 17 calls, all "
        "    of under 2 minutes, and seven SMS.\n\n"
        "The pattern is consistent with +44 7488 112233 being the operational handset "
        "of the principal of the scheme."
    )

    pdf.chapter_title("Cell-site attribution")
    pdf.body(
        "The unregistered handset's cell-site pattern is strongly attributable to Mr "
        "Darren Holcombe:\n\n"
        "  - Between 22:00 and 07:00, on 89% of nights in the review period, the "
        "    handset is powered on and attached to the cell site at Etherley Lane which "
        "    serves 42 Etherley Lane (Holcombe's home).\n\n"
        "  - On 41 separate weekday afternoons, the handset is attached to the cell site "
        "    serving the Bondgate area of Bishop Auckland. On at least 12 of those "
        "    occasions the session timing places the handset inside, or immediately "
        "    adjacent to, 18 Bondgate (Wainwright & Vane).\n\n"
        "  - On 2 April 2024 the handset attaches to a cell at Newcastle International "
        "    Airport at 08:14, consistent with meeting arrivals from Amsterdam (KL1575 "
        "    lands 08:35 - see Ms Tomic's travel BA-INT-008).\n\n"
        "  - On each Thursday morning matching the BA-SURV-003 surveillance window the "
        "    unregistered handset attaches to the cell serving Newgate Street, Bishop "
        "    Auckland, between approximately 10:40 and 11:40.\n\n"
        "Taken together, the cell-site pattern places the unregistered handset at all "
        "four locations of investigative interest (Etherley Lane, Bondgate, Newgate "
        "Street cafe, Newcastle Airport) in correlation with the movement pattern of "
        "Mr Holcombe."
    )

    pdf.chapter_title("Device history")
    pdf.body(
        "The IMEI associated with +44 7488 112233 was changed twice during the review "
        "period - on 19 December 2023 and on 2 September 2024. On each occasion the SIM "
        "was transferred to a new handset within 48 hours. This deliberate-looking "
        "handset-rotation practice is an indicator of operational communications "
        "discipline.\n\n"
        "The number also appears on a contact list exported from a handset seized at "
        "19 Tindale Crescent (Mr Qureshi's home) under the label 'D'. No surname was "
        "stored."
    )

    pdf.chapter_title("Evidential standing")
    pdf.body(
        "The cell-site data is direct evidence of the locations visited by the device. "
        "It is circumstantial evidence of the identity of the user. The strongest "
        "evidential bridge to Mr Holcombe is (a) the overnight pattern at Etherley Lane, "
        "and (b) the correlation with surveillance. The prosecution position, if taken, "
        "would be that the unregistered handset was used by Mr Holcombe. That "
        "proposition should be put to him in any second interview."
    )
    _save(pdf, "BA-TEC-009.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# BA-SUM-010  Operation Prince Bishop — Intelligence Summary
# ──────────────────────────────────────────────────────────────────────────────
def gen_ba_sum_010() -> None:
    pdf = _pdf("BA-SUM-010", "INTELLIGENCE SUMMARY")

    pdf.chapter_title("OPERATION PRINCE BISHOP — INTELLIGENCE SUMMARY")
    pdf.field("Date", "25 March 2025")
    pdf.field("Author", "DI S. Broadwood, SIO")
    pdf.field("Classification", "OFFICIAL-SENSITIVE")
    pdf.field("URN", "24/DU/2311")
    pdf.ln(3)

    pdf.body(
        "Operation Prince Bishop was initiated in September 2024 following a Suspicious "
        "Activity Report submitted by the Money Laundering Reporting Officer of Newgate "
        "Mutual Building Society. The SAR concerned a cluster of above-valuation "
        "mortgage applications on properties on Woodhouse Close Estate, Bishop Auckland, "
        "DL14, all processed by the same adviser. The investigation has expanded to "
        "cover a structured laundering pattern combining mortgage fraud, fictitious "
        "contractor invoicing, and offshore layering. This summary records the state of "
        "enquiries at the end of March 2025 and the position for disclosure review."
    )

    pdf.chapter_title("Key subjects")
    pdf.body(
        "SUBJECT 1 - Darren HOLCOMBE. DOB 17/02/1976. 42 Etherley Lane, Bishop Auckland. "
        "Principal developer. Interviewed under caution 11 February 2025 (BA-INT-001). "
        "Admitted directorships and vehicle (DH22 FRD) and admitted registered mobile "
        "+44 7811 456789. Declined to account for unregistered mobile +44 7488 112233 "
        "or for relationship with Kevin Pryce beyond 'customer'.\n\n"
        "SUBJECT 2 - Kevin PRYCE. DOB 04/09/1979. 9 Finkle Street, Bishop Auckland. "
        "Senior mortgage adviser, Newgate Mutual Building Society. Interviewed under "
        "caution 26 February 2025 (BA-INT-004). Acknowledged authorising eleven "
        "mortgages on properties purchased by Wear Valley Regeneration Ltd but declined "
        "to account for the regular Thursday meetings or for the elevated valuations by "
        "Mr Kenworthy.\n\n"
        "SUBJECT 3 - Sasha VERITY. DOB 23/08/1981. Etherley Grange area. Company "
        "secretary to three of the entities in the structure and signatory on two of the "
        "bank accounts. Not yet interviewed. Directed-surveillance product at "
        "BA-SURV-003 places Ms Verity at a three-way meeting with Holcombe and Pryce on "
        "24 October 2024.\n\n"
        "SUBJECT 4 - Imran QURESHI. DOB 02/03/1972. 19 Tindale Crescent, Bishop "
        "Auckland. Sole director of Galgate Nominees Ltd. Medically unable to perform "
        "the invoiced work. Received approximately 28% of inbound funds to Galgate as "
        "personal transfers. Not yet interviewed."
    )

    pdf.chapter_title("Third-party of interest")
    pdf.body(
        "Valerija TOMIC, Slovenian national, declared beneficial owner of Tees Holdings "
        "(IOM) Ltd. Assessed by both UK and Slovenian authorities as a nominee (see "
        "BA-INT-008, BA-FIN-006). No direct evidential link has yet been established "
        "between Ms Tomic and Mr Holcombe beyond the short-term let booking on "
        "Fore Bondgate, Bishop Auckland paid for by Wear Valley Regeneration Ltd in "
        "May 2022.\n\n"
        "Gerald WAINWRIGHT, senior partner at Wainwright & Vane Solicitors. Material "
        "witness. Not currently a suspect. See BA-LEG-007 for the factual picture."
    )

    pdf.chapter_title("Cross-reference to earlier operation")
    pdf.body(
        "Operation Teesdale (OP_TEESDALE), URN 21/DU/1109, concluded 2022. That "
        "operation concerned an unrelated subject but the same solicitor's practice "
        "acted, and the same fee-earner (Mr Wainwright) had conduct. The SRA reviewed "
        "the practice thematically in 2022 and took no action. The existence of this "
        "prior matter is noted on the disclosure schedule for this operation."
    )

    pdf.chapter_title("Evidential position")
    pdf.body(
        "The investigation is at a point where the financial and corporate picture is "
        "materially complete. The weaker areas are:\n\n"
        "  (1) The beneficial-ownership link between Mr Holcombe and Tees Holdings "
        "      (IOM) Ltd is circumstantial only. Direct evidence is sought via further "
        "      mutual-assistance with the IOM FSA and via comparison of Ms Tomic's UK "
        "      visits with Mr Holcombe's movements (still to be completed).\n\n"
        "  (2) The role of Ms Verity is understated in the current disclosure. A "
        "      directed interview is recommended.\n\n"
        "  (3) The status of the unregistered mobile +44 7488 112233 as being operated "
        "      by Mr Holcombe is circumstantial. It is supported by overnight cell-site "
        "      attribution at Etherley Lane and by its appearance in the Qureshi handset "
        "      contact list, but direct attribution evidence (e.g. DNA or fingerprint "
        "      on the handset) has not been obtained as the handset has not been "
        "      recovered.\n\n"
        "Notwithstanding these gaps, the volume of converging material - mortgage "
        "pattern, valuer concentration, invoice pattern, offshore routing, cell-site, "
        "corporate registered-office concentration, and surveillance - is assessed as "
        "sufficient for charges of fraud and money laundering against Mr Holcombe and "
        "Mr Pryce and sufficient for charge of money laundering against Ms Verity and "
        "Mr Qureshi subject to interview."
    )

    pdf.chapter_title("Known gaps")
    pdf.body(
        "The precise UK-resident identity behind Tees Holdings (IOM) Ltd remains to be "
        "proven in evidential form. The role, if any, of the Wainwright & Vane practice "
        "remains under evaluation. At least one further handset (the most recent IMEI "
        "for +44 7488 112233) has not been recovered. The 2 September 2024 handset "
        "rotation is of particular interest - it coincides with the opening of the "
        "investigation file and may indicate that disclosure of the operation reached "
        "the subject."
    )

    pdf.chapter_title("Immediate next steps")
    pdf.body(
        "1. Second interview of Mr Holcombe focused on the unregistered handset, the "
        "   short-term let paid by Wear Valley Regeneration Ltd for Ms Tomic, and the "
        "   Thursday pattern of meetings.\n\n"
        "2. Directed interview of Ms Verity under caution.\n\n"
        "3. Interview of Mr Qureshi under caution.\n\n"
        "4. Witness interview of Mr Wainwright.\n\n"
        "5. Further mutual assistance with IOM FSA for unredacted beneficial ownership "
        "   file on Tees Holdings (IOM) Ltd.\n\n"
        "6. Full file to CPS Specialist Fraud Division for charging advice on completion "
        "   of steps 1-4."
    )
    _save(pdf, "BA-SUM-010.pdf")


# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"\nGenerating Operation Prince Bishop corpus in: {OUTPUT_DIR}\n")
    gen_ba_int_001()
    gen_ba_fin_002()
    gen_ba_surv_003()
    gen_ba_int_004()
    gen_ba_fin_005()
    gen_ba_fin_006()
    gen_ba_leg_007()
    gen_ba_int_008()
    gen_ba_tec_009()
    gen_ba_sum_010()
    print(f"\n10 documents written to {OUTPUT_DIR}")
    print("\nCross-document chain to discover:")
    print("  Holcombe (BA-INT-001) --[director]--> Wear Valley Regeneration Ltd")
    print("  Wear Valley --[pays invoices to]--> Galgate Nominees Ltd (BA-FIN-005)")
    print("  Galgate Nominees --[onward transfers]--> Tees Holdings (IOM) Ltd (BA-FIN-006)")
    print("  Tees Holdings --[declared UBO]--> V. Tomic, Ljubljana (BA-INT-008)")
    print("  Holcombe <--[Thursday meetings]--> Pryce (BA-SURV-003, BA-INT-004)")
    print("  +44 7488 112233 --[cell-site]--> Etherley Lane + 18 Bondgate (BA-TEC-009)")
    print("  18 Bondgate --[registered office for 4 of 5 UK entities]--> (BA-LEG-007)\n")


if __name__ == "__main__":
    main()
