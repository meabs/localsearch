from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

DOC_NAMES = [
    "NF-INT-001.pdf",
    "NF-INT-002.pdf",
    "NF-SURV-003.pdf",
    "NF-SURV-004.pdf",
    "NF-FIN-005.pdf",
    "NF-COMMS-006.pdf",
    "NF-VEH-007.pdf",
    "NF-OPS-008.pdf",
    "NF-ANL-009.pdf",
    "NF-BRIEF-010.pdf",
]


PEOPLE = [
    "Marcus Webb",
    "Rania Khalil",
    "Jonas Pike",
    "Elena Ward",
    "Tariq Nasser",
    "M. Webb",
    "Webb, Marcus",
]

LOCATIONS = [
    "14 Arkwright Road, CH1",
    "Depot Yard, Kingsway Industrial Estate",
    "Pier 7 Logistics Hub",
    "Station Terrace Car Park",
    "North Ring Storage Units",
]

VEHICLES = ["RX71 KLD", "LM19 XTR", "KT22 BNF"]
PHONES = ["07712 345678", "+44 7700 900123", "07990 112233"]
CASE_REFS = ["OP_NIGHTFALL", "URN:NF-2026-3341", "OP NIGHTFALL"]
SOURCES = ["SURV_TEAM_ALPHA", "FIN_CELL_02", "HUMINT_4", "COMMS_DESK", "OPS_ROOM"]
CONFIDENCE = ["high confidence", "moderate confidence", "low confidence"]
UNCERTAINTY = [
    "timing is approximate due to clock drift",
    "identity is probable but not confirmed",
    "plate readability was partially obstructed",
    "statement consistency is unverified across witnesses",
]


def build_paragraph(seed: int, page: int, block: int) -> str:
    p1 = PEOPLE[(seed + page + block) % len(PEOPLE)]
    p2 = PEOPLE[(seed + page + block + 1) % len(PEOPLE)]
    loc = LOCATIONS[(seed + block) % len(LOCATIONS)]
    veh = VEHICLES[(seed + page) % len(VEHICLES)]
    phone = PHONES[(seed + page + block) % len(PHONES)]
    cref = CASE_REFS[(seed + page) % len(CASE_REFS)]
    minute = (12 + page + block) % 60
    hour = (19 + block) % 24
    src = SOURCES[(seed + page + block) % len(SOURCES)]
    conf = CONFIDENCE[(seed + page + block) % len(CONFIDENCE)]
    uncertainty = UNCERTAINTY[(seed + block) % len(UNCERTAINTY)]
    secondary_loc = LOCATIONS[(seed + block + 2) % len(LOCATIONS)]
    contact_time = f"{(hour + 1) % 24:02d}:{(minute + 11) % 60:02d}"
    contradictory = (
        f"A follow-up witness account at {contact_time} places {p1} near {secondary_loc} "
        f"rather than {loc}, requiring reconciliation."
        if (page + block + seed) % 3 == 0
        else ""
    )

    return (
        f"Case reference {cref}. Source {src} reports {p1} was observed at {loc} "
        f"at {hour:02d}:{minute:02d} with {conf}. "
        f"{veh} was seen at {loc} at {hour:02d}:{(minute + 7) % 60:02d}. "
        f"{p1} is known to associate with {p2}, and call data indicates "
        f"{p1}'s phone ({phone}) contacted a number linked to {p2} at {contact_time}. "
        f"Cross-reference note: alias forms include {p1}, M. Webb, and Webb, Marcus. "
        f"Assessment caveat: {uncertainty}. "
        f"{contradictory} "
        f"Analytical direction: verify whether movement from {loc} to {secondary_loc} "
        "supports a coordinated handoff or competing witness narratives."
    )


def draw_wrapped_text(c: canvas.Canvas, text: str, x: int, y: int, max_chars: int = 110) -> int:
    words = text.split()
    line = []
    line_height = 14
    for word in words:
        test_line = " ".join(line + [word])
        if len(test_line) <= max_chars:
            line.append(word)
            continue
        c.drawString(x, y, " ".join(line))
        y -= line_height
        line = [word]
    if line:
        c.drawString(x, y, " ".join(line))
        y -= line_height
    return y


def create_document(path: Path, seed: int) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    title = path.stem.replace("-", " ")

    for page in range(1, 13):
        y = height - 60
        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, y, f"{title} | Page {page}")
        y -= 24
        c.setFont("Helvetica", 10)
        for block in range(12):
            text = build_paragraph(seed=seed, page=page, block=block)
            y = draw_wrapped_text(c, text, 50, y)
            y -= 8
            if y < 80:
                break
        c.setFont("Helvetica-Oblique", 9)
        c.drawString(
            50,
            35,
            "OFFICIAL - DEMO ONLY. Synthetic data generated for Operation Lens v2 testing.",
        )
        c.showPage()
    c.save()


def main() -> None:
    out_dir = Path("data/pdfs")
    out_dir.mkdir(parents=True, exist_ok=True)
    for idx, name in enumerate(DOC_NAMES):
        create_document(out_dir / name, seed=idx + 1)
    print(f"Generated {len(DOC_NAMES)} sample PDFs in {out_dir}")


if __name__ == "__main__":
    main()
