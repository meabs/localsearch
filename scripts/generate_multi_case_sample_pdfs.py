from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

CASES = [
    {
        "case_ref": "OP_IRONVALE",
        "people": ["Aisha Grant", "Darren Cole", "Mina Patel", "I. Vale"],
        "locations": ["Ironvale Wharf", "12 Millstream Road", "Dock Office Block C"],
        "vehicles": ["IV22 LKE", "KR11 TPN"],
        "phones": ["07888 112244", "+44 7711 229900"],
    },
    {
        "case_ref": "OP_SEAGLASS",
        "people": ["Ruben Hale", "Nadia Quinn", "Lewis Shaw", "R. Hale"],
        "locations": ["Seaglass Marina", "47 Coastline Drive", "Warehouse 9 South Pier"],
        "vehicles": ["SG71 HRT", "BN24 QWE"],
        "phones": ["07900 881122", "+44 7700 450900"],
    },
]
SOURCE_TYPES = ["surveillance", "financial", "communications", "human source", "ops log"]
RISK_FLAGS = [
    "possible counter-surveillance behavior",
    "unverified handoff event",
    "financial motive not yet established",
    "route diversion may indicate contingency planning",
]
OUTCOMES = [
    "recommend targeted follow-up on shared location overlap",
    "recommend corroboration against ANPR and cell-site records",
    "recommend priority interview planning for timeline conflict",
]


def paragraph(case: dict[str, list[str] | str], idx: int, page: int, block: int) -> str:
    people = case["people"]
    locations = case["locations"]
    vehicles = case["vehicles"]
    phones = case["phones"]
    p1 = people[(idx + page + block) % len(people)]
    p2 = people[(idx + page + block + 1) % len(people)]
    loc = locations[(idx + block) % len(locations)]
    veh = vehicles[(idx + page) % len(vehicles)]
    phone = phones[(idx + page + block) % len(phones)]
    src = SOURCE_TYPES[(idx + page + block) % len(SOURCE_TYPES)]
    risk = RISK_FLAGS[(idx + block) % len(RISK_FLAGS)]
    outcome = OUTCOMES[(idx + page) % len(OUTCOMES)]
    secondary_loc = locations[(idx + block + 1) % len(locations)]
    contradictory = (
        f"A second report within 20 minutes places {p1} near {secondary_loc}, "
        "which may conflict with earlier siting."
        if (idx + page + block) % 4 == 0
        else ""
    )
    return (
        f"Case reference {case['case_ref']}. "
        f"Source type {src} indicates {p1} was observed at {loc} at 20:{(11 + block) % 60:02d}. "
        f"{veh} was seen at {loc}. {p1} is known to associate with {p2}. "
        f"Analyst note: {p1}'s phone ({phone}) "
        f"was in contact with a number linked to {p2}. "
        f"Risk flag: {risk}. {contradictory} "
        f"Operational summary confirms repeated contact and movement linked to {case['case_ref']}; "
        f"{outcome}."
    )


def draw_wrapped(c: canvas.Canvas, text: str, x: int, y: int, width_chars: int = 110) -> int:
    words = text.split()
    line: list[str] = []
    for word in words:
        candidate = " ".join([*line, word])
        if len(candidate) <= width_chars:
            line.append(word)
            continue
        c.drawString(x, y, " ".join(line))
        y -= 14
        line = [word]
    if line:
        c.drawString(x, y, " ".join(line))
        y -= 14
    return y


def make_doc(path: Path, case: dict[str, list[str] | str], doc_idx: int) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    for page in range(1, 11):
        y = height - 60
        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, y, f"{case['case_ref']} | Document {doc_idx:03d} | Page {page}")
        y -= 24
        c.setFont("Helvetica", 10)
        for block in range(10):
            y = draw_wrapped(c, paragraph(case, doc_idx, page, block), 50, y)
            y -= 6
            if y < 80:
                break
        c.setFont("Helvetica-Oblique", 9)
        c.drawString(50, 35, "Synthetic case evidence for local Operation Lens v2 testing only.")
        c.showPage()
    c.save()


def main() -> None:
    out = Path("data/pdfs")
    out.mkdir(parents=True, exist_ok=True)
    created = 0
    for case in CASES:
        case_ref = str(case["case_ref"])
        for idx in range(1, 6):
            filename = f"{case_ref}-DOC-{idx:03d}.pdf"
            make_doc(out / filename, case, idx)
            created += 1
    print(f"Generated {created} multi-case PDFs in {out}")


if __name__ == "__main__":
    main()
