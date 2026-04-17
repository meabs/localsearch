#!/usr/bin/env python3
"""Generate the 10 Operation Nightfall synthetic PDFs.

Documents are designed so that no single file contains the full Webb–Khalil
connection chain — the system must construct it by cross-document graph traversal.

Cross-document chain:
  Webb (NF-INT-001) → RX71 KLD (NF-INT-001)
  RX71 KLD (NF-SURV-004) → Depot → Khalil (NF-SURV-004)
  Khalil (NF-FIN-005) → Arkwright Holdings → Transactions
  Marsh (NF-SURV-007) → Depot → [same location as Khalil]
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

CASE_REF = "OP_NIGHTFALL"


def _safe_pdf_text(value: str) -> str:
    """Normalize text for core PDF fonts that only support Latin-1."""
    replacements = {
        "\u2013": "-",  # en dash
        "\u2014": "-",  # em dash
        "\u2018": "'",  # left single quote
        "\u2019": "'",  # right single quote/apostrophe
        "\u201c": '"',  # left double quote
        "\u201d": '"',  # right double quote
        "\u2026": "...",  # ellipsis
        "\u2192": "->",  # arrow
    }
    normalized = value.translate(str.maketrans(replacements))
    return normalized.encode("latin-1", "replace").decode("latin-1")


class IntelPDF(FPDF):
    """Thin wrapper that renders a document in a consistent intelligence-report style."""

    def header(self) -> None:
        self.set_font("Courier", "B", 9)
        self.set_text_color(80, 80, 80)
        self.cell(
            0,
            6,
            _safe_pdf_text(f"OPERATION NIGHTFALL  |  RESTRICTED  |  {self.title}"),
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
        self.cell(0, 6, f"Page {self.page_no()}  |  NOT FOR DISTRIBUTION", align="C")

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

    def field(self, label: str, value: str) -> None:
        self.set_font("Courier", "B", 10)
        self.set_text_color(20, 20, 20)
        self.cell(45, 6, _safe_pdf_text(f"{label}:"), ln=False)
        self.set_font("Courier", "", 10)
        # Keep this as a single row field to avoid fpdf2 cursor edge-cases.
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
# NF-INT-001  Suspect Interview — Marcus Webb
# ──────────────────────────────────────────────────────────────────────────────
def gen_nf_int_001() -> None:
    pdf = _pdf("NF-INT-001", "SUSPECT INTERVIEW")
    pdf.chapter_title("INTERVIEW RECORD — MARCUS WEBB")
    pdf.field("Date", "14 March 2024")
    pdf.field("Location", "Northside Police Station, Interview Room 3")
    pdf.field("Officers", "DC Hargreaves, DS Patel")
    pdf.field("Subject", "Marcus Webb, DOB 12/04/1983, 22 Linton Gardens")
    pdf.field("Solicitor", "Present — Mr T. Ashworth")
    pdf.ln(4)
    pdf.body(
        "DC HARGREAVES: Marcus, can you tell me where you were on the evening of 8 March 2024?\n\n"
        "WEBB: I was at home. I live alone at 22 Linton Gardens. I wasn't out.\n\n"
        "DC HARGREAVES: CCTV from the area shows a vehicle registered to you — plate RX71 KLD — "
        "in the vicinity of Arkwright Road at approximately 21:40. Can you explain that?\n\n"
        "WEBB: My car? I lent it to a mate. I stayed in.\n\n"
        "DC HARGREAVES: Who did you lend it to?\n\n"
        "WEBB: Just someone I know. Can't remember the name.\n\n"
        "DC HARGREAVES: Do you know a woman called Rania Khalil?\n\n"
        "WEBB: No. Never heard of her. Who is she?\n\n"
        "DC HARGREAVES: Do you know anyone associated with Arkwright Holdings Ltd?\n\n"
        "WEBB: No comment.\n\n"
        "DC HARGREAVES: Have you ever visited the Depot at the Industrial Estate off "
        "Grange Lane?\n\n"
        "WEBB: No comment.\n\n"
        "DS PATEL: Marcus, we have a phone number for you — 07712 345678 — is that correct?\n\n"
        "WEBB: That's my old number. I've changed it.\n\n"
        "Interview suspended at 14:47."
    )
    pdf.chapter_title("INTERVIEWING OFFICER NOTES")
    pdf.body(
        "Subject was evasive throughout. Denied knowledge of Khalil without prompting on name. "
        "Vehicle RX71 KLD confirmed registered to Webb. Suspect admitted ownership of phone "
        "number 07712 345678 before retracting. No further admissions. Recommend surveillance "
        "on 22 Linton Gardens and further enquiries regarding vehicle movements."
    )
    _save(pdf, "NF-INT-001.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# NF-INT-002  Witness Statement — vehicle sighting at Arkwright Road
# ──────────────────────────────────────────────────────────────────────────────
def gen_nf_int_002() -> None:
    pdf = _pdf("NF-INT-002", "WITNESS STATEMENT")
    pdf.chapter_title("WITNESS STATEMENT")
    pdf.field("Date", "15 March 2024")
    pdf.field("Witness", "Name withheld — reference WIT-002")
    pdf.field("Officer", "PC Donnelly")
    pdf.ln(4)
    pdf.body(
        "I am providing this statement voluntarily. I do not wish my name to be disclosed.\n\n"
        "On the evening of 8 March 2024 I was walking past 14 Arkwright Road at approximately "
        "21:30. I observed an unknown male standing outside the property. He appeared to be "
        "waiting. He was approximately 5'11\", medium build, wearing a dark jacket.\n\n"
        "A vehicle was parked on the road outside — I noticed it because it had unusual "
        "personalised-style plates. To the best of my recollection the plate was something "
        "like RX71 and three letters after. I cannot be certain of the exact letters.\n\n"
        "The male did not appear to enter the property while I was present. I did not see "
        "anyone else arrive or leave. I did not recognise the male and cannot provide a name.\n\n"
        "I am willing to assist further if required but do not wish to appear in court.\n\n"
        "Statement taken by PC Donnelly. Witness declined to sign."
    )
    _save(pdf, "NF-INT-002.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# NF-SURV-003  Surveillance Log — Webb at Arkwright Road
# ──────────────────────────────────────────────────────────────────────────────
def gen_nf_surv_003() -> None:
    pdf = _pdf("NF-SURV-003", "SURVEILLANCE LOG")
    pdf.chapter_title("SURVEILLANCE LOG — NF-SURV-003")
    pdf.field("Operation", "NIGHTFALL")
    pdf.field("Date", "22 March 2024")
    pdf.field("Team", "Alpha-3 (DS Okonkwo lead)")
    pdf.field("Subject", "Marcus Webb")
    pdf.ln(4)
    pdf.chapter_title("Log Entries")
    pdf.body(
        "19:55  Subject leaves 22 Linton Gardens on foot. Dressed casually.\n\n"
        "20:12  Subject boards bus 47 heading south. Team maintains static observation.\n\n"
        "20:34  Subject alights at Arkwright Road junction. Proceeds on foot east.\n\n"
        "20:41  Marcus Webb was observed at 14 Arkwright Road. Subject paused at entrance, "
        "appeared to check phone, then proceeded inside. Two unidentified males were present "
        "at the address — descriptions below.\n\n"
        "      UM-1: Approx 6'0\", stocky build, shaved head, dark tracksuit.\n"
        "      UM-2: Approx 5'9\", slim, wearing grey hoodie, carrying holdall.\n\n"
        "21:10  Subject exits property alone. UM-1 and UM-2 remain inside.\n\n"
        "21:18  Subject boards bus 47 heading north. Returns to 22 Linton Gardens.\n\n"
        "21:40  Subject confirmed inside premises. Static observation concluded 22:00.\n\n"
    )
    pdf.chapter_title("Assessment")
    pdf.body(
        "Webb attended 14 Arkwright Road and met with two unidentified males. Duration of "
        "visit approximately 29 minutes. No property exchanged in view of the team. "
        "Recommend intelligence checks on 14 Arkwright Road occupants and CCTV from bus 47."
    )
    _save(pdf, "NF-SURV-003.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# NF-SURV-004  Surveillance Log — RX71 KLD at Depot / Khalil confirmed
# ──────────────────────────────────────────────────────────────────────────────
def gen_nf_surv_004() -> None:
    pdf = _pdf("NF-SURV-004", "SURVEILLANCE LOG")
    pdf.chapter_title("SURVEILLANCE LOG — NF-SURV-004")
    pdf.field("Operation", "NIGHTFALL")
    pdf.field("Date", "25 March 2024")
    pdf.field("Team", "Bravo-1 (DC Fernandez lead)")
    pdf.field("Subject vehicle", "RX71 KLD")
    pdf.ln(4)
    pdf.chapter_title("Log Entries")
    pdf.body(
        "11:05  Vehicle RX71 KLD seen at Depot, Industrial Estate, Grange Lane. "
        "Vehicle arrived from direction of town centre. Parked in bay 7.\n\n"
        "11:12  RX71 KLD was seen at Depot Industrial Estate. Driver exited vehicle. "
        "Driver confirmed by CCTV reference NF-CCTV-004-A as Rania Khalil, "
        "DOB 03/09/1989, known to intelligence as an associate of Arkwright Holdings Ltd.\n\n"
        "11:15  Khalil entered the Depot office building via side entrance. "
        "Met by unknown male.\n\n"
        "11:44  Khalil exited. Carrying document folder not present on arrival.\n\n"
        "11:47  Vehicle RX71 KLD departs Depot, heading east on Grange Lane.\n\n"
        "12:03  Vehicle RX71 KLD seen entering Westfield car park. Lost in car park.\n\n"
    )
    pdf.chapter_title("CCTV Note")
    pdf.body(
        "Still images from CCTV reference NF-CCTV-004-A attached to case file. "
        "Analyst confirms ID of Rania Khalil from custody photograph dated 2021. "
        "Vehicle confirmed as RX71 KLD from plate reader at estate entrance.\n\n"
        "This is the same vehicle confirmed registered to Marcus Webb (see NF-INT-001). "
        "Webb stated he lent the vehicle — Khalil now identified as the driver."
    )
    _save(pdf, "NF-SURV-004.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# NF-FIN-005  Financial Intelligence — Khalil / Arkwright Holdings
# ──────────────────────────────────────────────────────────────────────────────
def gen_nf_fin_005() -> None:
    pdf = _pdf("NF-FIN-005", "FINANCIAL INTELLIGENCE REPORT")
    pdf.chapter_title("FINANCIAL INTELLIGENCE — NF-FIN-005")
    pdf.field("Subject", "Rania Khalil")
    pdf.field("Analyst", "Financial Intelligence Unit")
    pdf.field("Date", "28 March 2024")
    pdf.ln(4)
    pdf.body(
        "This report summarises financial intelligence developed in relation to Rania Khalil "
        "(DOB 03/09/1989) in the context of Operation Nightfall.\n\n"
    )
    pdf.chapter_title("Company Directorship")
    pdf.body(
        "Companies House records confirm that Rania Khalil is a director of "
        "Arkwright Holdings Ltd, "
        "incorporated 14 June 2019. The company lists its registered address as Unit 7, Depot, "
        "Industrial Estate, Grange Lane — the same address visited by Khalil on 25 March 2024 "
        "(see NF-SURV-004).\n\n"
        "Rania Khalil is linked to Arkwright Holdings Ltd via directorship and "
        "operational control. "
        "No legitimate business activity has been identified for the company.\n\n"
    )
    pdf.chapter_title("Suspicious Transactions")
    pdf.body(
        "Account sort code 44-21-09, account number 87654321, held in the name of "
        "Arkwright Holdings Ltd, has received the following transactions since January 2024:\n\n"
        "  2024-01-15  £12,500 received — no reference — originating account untraced\n"
        "  2024-02-03  £8,000 received — REF: CONSULT — originating account untraced\n"
        "  2024-02-28  £22,000 received — no reference — originating account untraced\n"
        "  2024-03-14  £5,500 received — no reference — originating account untraced\n\n"
        "Total receipts in period: £48,000. No corresponding outgoings identified other than "
        "two cash withdrawals totalling £31,000 from branch ATM in March 2024.\n\n"
        "The account number 87654321 with sort code 44-21-09 is the primary account of interest."
    )
    pdf.chapter_title("Recommendation")
    pdf.body(
        "Recommend Suspicious Activity Report submission and consideration of Account Freezing "
        "Order. Further financial investigation into originating accounts required."
    )
    _save(pdf, "NF-FIN-005.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# NF-INT-006  Associate Interview — Danny Marsh (mentions Operation Redfox)
# ──────────────────────────────────────────────────────────────────────────────
def gen_nf_int_006() -> None:
    pdf = _pdf("NF-INT-006", "ASSOCIATE INTERVIEW")
    pdf.chapter_title("INTERVIEW RECORD — DANNY MARSH")
    pdf.field("Date", "29 March 2024")
    pdf.field("Location", "Northside Police Station, Interview Room 2")
    pdf.field("Officers", "DC Hargreaves, DC Osei")
    pdf.field("Subject", "Danny Marsh, DOB 07/11/1985, 9 Cornmill Lane")
    pdf.ln(4)
    pdf.body(
        "DC HARGREAVES: Danny, you are here voluntarily. You are not under arrest. "
        "Can you tell me if you know a Marcus?\n\n"
        "MARSH: Which Marcus? There's loads of them, isn't there.\n\n"
        "DC HARGREAVES: A Marcus who associates with people involved in moving property.\n\n"
        "MARSH: I know a Marcus, yeah. Met him a few times. Don't really know his surname.\n\n"
        "DC HARGREAVES: Where did you meet him?\n\n"
        "MARSH: Around. Can't remember exactly.\n\n"
        "DC OSEI: Danny, are you familiar with Operation Redfox? Case reference OP_REDFOX?\n\n"
        "MARSH: Yeah. I was spoken to about that a couple of years back. I had nothing to do "
        "with it. You've got that on record.\n\n"
        "DC OSEI: We do. We're asking whether the same network is active again.\n\n"
        "MARSH: I wouldn't know about that.\n\n"
        "DC HARGREAVES: Have you visited the Industrial Estate recently? Grange Lane area?\n\n"
        "MARSH: I might have driven past. Nothing specific.\n\n"
        "Interview concluded at 11:22. No further voluntary information provided."
    )
    _save(pdf, "NF-INT-006.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# NF-SURV-007  Surveillance Log — Marsh and Webb together at Depot
# ──────────────────────────────────────────────────────────────────────────────
def gen_nf_surv_007() -> None:
    pdf = _pdf("NF-SURV-007", "SURVEILLANCE LOG")
    pdf.chapter_title("SURVEILLANCE LOG — NF-SURV-007")
    pdf.field("Operation", "NIGHTFALL")
    pdf.field("Date", "2 April 2024")
    pdf.field("Team", "Alpha-3 and Bravo-1 (joint)")
    pdf.field("Subjects", "Marcus Webb, Danny Marsh")
    pdf.ln(4)
    pdf.chapter_title("Log Entries")
    pdf.body(
        "13:55  Marcus Webb departs 22 Linton Gardens driving vehicle RX71 KLD.\n\n"
        "14:10  Danny Marsh departs 9 Cornmill Lane on foot. Boards bus 12.\n\n"
        "14:18  Webb arrives at Depot, Industrial Estate, Grange Lane. Parks in bay 4.\n\n"
        "14:22  Danny Marsh and Marcus Webb together at Depot Industrial Estate. "
        "Both males entered the Depot via the main roller shutter entrance. "
        "Time-stamped by static camera NF-CAM-007. Third male present — unidentified.\n\n"
        "14:55  All three males exit Depot. Webb returns to RX71 KLD. "
        "Marsh boards bus 12 heading back toward Cornmill Lane.\n\n"
        "15:03  RX71 KLD departs Depot heading west.\n\n"
    )
    pdf.chapter_title("Significance")
    pdf.body(
        "This is the first direct observation of Webb and Marsh together at the Depot. "
        "Khalil was previously identified at the same Depot address on 25 March (NF-SURV-004). "
        "The presence of all three individuals at this location constitutes a significant "
        "intelligence link that should be developed."
    )
    _save(pdf, "NF-SURV-007.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# NF-TEC-008  Phone Data Extract — Webb / unregistered number contact
# ──────────────────────────────────────────────────────────────────────────────
def gen_nf_tec_008() -> None:
    pdf = _pdf("NF-TEC-008", "PHONE DATA EXTRACT")
    pdf.chapter_title("COMMUNICATIONS DATA — NF-TEC-008")
    pdf.field("Subject number", "+447712345678 (Marcus Webb)")
    pdf.field("Period", "1 January 2024 — 31 March 2024")
    pdf.field("Analyst", "Digital Forensics Unit")
    pdf.ln(4)
    pdf.body(
        "This report contains communications data obtained under lawful authority.\n\n"
        "The number +447712345678 is associated with Marcus Webb (see NF-INT-001). "
        "During the period under review the following contact pattern was identified:\n\n"
    )
    pdf.chapter_title("Contact Summary")
    pdf.body(
        "Number: +447798123456 (unregistered pay-as-you-go SIM)\n"
        "Total calls: 47\n"
        "Direction: Mixed inbound and outbound\n"
        "First contact: 4 January 2024\n"
        "Last contact: 29 March 2024\n"
        "Average call duration: 4 minutes 12 seconds\n"
        "Longest call: 18 minutes (14 February 2024)\n\n"
        "The volume and pattern of calls between +447712345678 (Webb) and "
        "+447798123456 (unregistered) is consistent with regular operational contact "
        "rather than casual acquaintance.\n\n"
        "Note: +447798123456 shows no calls to any other number in our dataset, "
        "suggesting it is a dedicated contact number.\n\n"
    )
    pdf.chapter_title("Location Data")
    pdf.body(
        "Cell site data for +447798123456 places the device in the area of Grange Lane "
        "Industrial Estate on 25 March 2024 between 11:00 and 12:00 — consistent with "
        "the Depot observation in NF-SURV-004 — and on 2 April 2024 between 14:00 and 15:30 "
        "— consistent with the Depot observation in NF-SURV-007.\n\n"
        "This pattern strongly suggests +447798123456 belongs to a regular contact of Webb "
        "who was present at the Depot on both surveillance occasions."
    )
    _save(pdf, "NF-TEC-008.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# NF-INT-009  Danny Marsh second interview — references Arkwright Road and female associate
# ──────────────────────────────────────────────────────────────────────────────
def gen_nf_int_009() -> None:
    pdf = _pdf("NF-INT-009", "SUSPECT INTERVIEW")
    pdf.chapter_title("INTERVIEW RECORD — DANNY MARSH (second interview)")
    pdf.field("Date", "5 April 2024")
    pdf.field("Location", "Northside Police Station, Interview Room 1")
    pdf.field("Officers", "DS Patel, DC Fernandez")
    pdf.field("Status", "Arrested — Possession with intent to supply")
    pdf.ln(4)
    pdf.body(
        "DS PATEL: Danny, you were arrested this morning at 9 Cornmill Lane. "
        "Items were found at your address. I want to ask you about your associates.\n\n"
        "MARSH: No comment.\n\n"
        "DS PATEL: We've seen you at the Depot on Grange Lane. Who do you meet there?\n\n"
        "MARSH: No comment.\n\n"
        "DC FERNANDEZ: Are you familiar with Arkwright Road?\n\n"
        "MARSH: I know where it is. Yeah.\n\n"
        "DC FERNANDEZ: Have you been to Arkwright Road recently?\n\n"
        "MARSH: Might have been in the area. It's not far from where I live.\n\n"
        "DS PATEL: Do you know a female associate of the people you've been meeting "
        "at the Depot?\n\n"
        "MARSH: There's a woman. I don't know her name. She was there once. "
        "Dark hair, professional-looking. That's all I know.\n\n"
        "DS PATEL: Can you describe her further?\n\n"
        "MARSH: No comment.\n\n"
        "Interview concluded at 14:33. Marsh declined to answer further questions."
    )
    pdf.chapter_title("Officer Notes")
    pdf.body(
        "Marsh references a female associate at the Depot but declines to name her. "
        "Description is consistent with the individual identified as Rania Khalil in "
        "NF-SURV-004, though Marsh does not use that name. "
        "Marsh acknowledged knowledge of Arkwright Road when prompted."
    )
    _save(pdf, "NF-INT-009.pdf")


# ──────────────────────────────────────────────────────────────────────────────
# NF-SUM-010  Intelligence Summary — Overview (deliberately incomplete)
# ──────────────────────────────────────────────────────────────────────────────
def gen_nf_sum_010() -> None:
    pdf = _pdf("NF-SUM-010", "INTELLIGENCE SUMMARY")
    pdf.chapter_title("OPERATION NIGHTFALL — INTELLIGENCE SUMMARY")
    pdf.field("Date", "6 April 2024")
    pdf.field("Author", "Intelligence Cell")
    pdf.field("Classification", "RESTRICTED")
    pdf.ln(4)
    pdf.body(
        "Operation Nightfall was initiated following intelligence received in December 2023 "
        "indicating the movement of controlled substances through the Grange Lane Industrial "
        "Estate area. The following is a summary of intelligence developed to date.\n\n"
    )
    pdf.chapter_title("Key Subjects")
    pdf.body(
        "Marcus Webb (DOB 12/04/1983, 22 Linton Gardens) — principal subject. "
        "Webb has previous convictions for handling stolen goods (2018). "
        "Vehicle RX71 KLD is registered to Webb and has been observed at multiple "
        "locations of interest.\n\n"
        "Danny Marsh (DOB 07/11/1985, 9 Cornmill Lane) — associate. "
        "Marsh was spoken to in connection with Operation Redfox (OP_REDFOX) in 2022 "
        "but was not prosecuted. Marsh has now been arrested in the current operation.\n\n"
        "NOTE: This summary does not include all intelligence developed. "
        "Financial intelligence and telephone analysis are filed separately. "
        "A third associate has been identified through surveillance but is not named "
        "in this document pending further enquiries."
    )
    pdf.chapter_title("Key Locations")
    pdf.body(
        "Depot, Industrial Estate, Grange Lane — central location of interest. "
        "Multiple subjects have been observed attending.\n\n"
        "14 Arkwright Road — secondary address visited by Webb.\n\n"
        "22 Linton Gardens — Webb's home address.\n\n"
        "9 Cornmill Lane — Marsh's home address."
    )
    pdf.chapter_title("Intelligence Gaps")
    pdf.body(
        "The precise role of the Depot in the operation is not yet confirmed. "
        "A third associate present at the Depot on 2 April 2024 remains unidentified. "
        "The ultimate source and destination of funds through the accounts of interest "
        "has not been established."
    )
    _save(pdf, "NF-SUM-010.pdf")


# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"\nGenerating Operation Nightfall corpus in: {OUTPUT_DIR}\n")
    gen_nf_int_001()
    gen_nf_int_002()
    gen_nf_surv_003()
    gen_nf_surv_004()
    gen_nf_fin_005()
    gen_nf_int_006()
    gen_nf_surv_007()
    gen_nf_tec_008()
    gen_nf_int_009()
    gen_nf_sum_010()
    print(f"\n10 documents written to {OUTPUT_DIR}")
    print("\nCross-document chain to discover:")
    print("  Webb (NF-INT-001) --[owns]--> RX71 KLD")
    print("  RX71 KLD (NF-SURV-004) --[driven by]--> Khalil")
    print("  Khalil (NF-FIN-005) --[director of]--> Arkwright Holdings")
    print("  Marsh + Webb (NF-SURV-007) --[both observed at]--> Depot")
    print("  Depot (NF-SURV-004, NF-SURV-007) --[same location]--> Khalil + Marsh + Webb\n")


if __name__ == "__main__":
    main()
