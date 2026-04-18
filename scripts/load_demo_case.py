#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import sys
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from operation_lens_v2.config import settings  # noqa: E402
from operation_lens_v2.ingestion.duck_store import create_case, init_db  # noqa: E402
from operation_lens_v2.ingestion.pipeline import ingest_document  # noqa: E402

CASE_REF = "OP_DEMO_SIGNAL"
CASE_NAME = "Synthetic Demo Signal"
DOMAIN_PACK = "investigations"
DEMO_DIR = ROOT / "data" / "demo_seed" / CASE_REF

DEMO_QUERY = (
    "What connects Lena Hart to South Quay Locker and what evidence supports that connection?"
)


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def create_demo_seed() -> list[Path]:
    DEMO_DIR.mkdir(parents=True, exist_ok=True)

    report_text = """2026-04-18 09:14 Analyst intake note.
Subject Lena Hart, also using the alias L. Hart, met Jonah Vale near South Quay Locker 14.
Witness note states vehicle RX71 KLD arrived at South Quay Warehouse, Pier Road.
Lena Hart used phone 07712 345678 and referenced account 44-21-09 87654321 during the exchange.
"""
    _write_text(DEMO_DIR / "01_field_report.txt", report_text)

    with (DEMO_DIR / "02_contact_bridges.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["subject", "alias", "phone", "vehicle", "account", "location"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "subject": "Lena Hart",
                "alias": "L. Hart",
                "phone": "07712 345678",
                "vehicle": "RX71 KLD",
                "account": "44-21-09 87654321",
                "location": "South Quay Warehouse",
            }
        )
        writer.writerow(
            {
                "subject": "Jonah Vale",
                "alias": "JV",
                "phone": "07798 123456",
                "vehicle": "",
                "account": "",
                "location": "Pier Road",
            }
        )

    payload = {
        "case_ref": CASE_REF,
        "events": [
            {
                "date": "2026-04-18",
                "time": "09:14",
                "subject": "Lena Hart",
                "location": "South Quay Locker 14",
                "note": "Locker access recorded before contact with Jonah Vale.",
            },
            {
                "date": "2026-04-18",
                "time": "09:22",
                "subject": "RX71 KLD",
                "location": "South Quay Warehouse",
                "note": "Vehicle observed near Pier Road cameras.",
            },
        ],
    }
    (DEMO_DIR / "03_event_log.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    email = EmailMessage()
    email["Subject"] = "Locker handoff confirmed"
    email["From"] = "control@synthetic.local"
    email["To"] = "analyst@synthetic.local"
    email.set_content(
        "Lena Hart confirmed South Quay Locker 14 was opened before Jonah Vale arrived. "
        "Reference image attached separately to the case."
    )
    (DEMO_DIR / "04_control_update.eml").write_bytes(email.as_bytes())

    html_note = """<html><body><h1>South Quay Warehouse</h1>
    <p>Rania observer note: Lena Hart and Jonah Vale were seen together at South Quay Warehouse on Pier Road.</p>
    <p>Timeline marker: 2026-04-18 09:22 vehicle RX71 KLD at the loading gate.</p>
    </body></html>"""
    _write_text(DEMO_DIR / "05_site_note.html", html_note)

    try:
        from PIL import Image, ImageDraw  # type: ignore[import]
    except ImportError:
        pass
    else:
        image = Image.new("RGB", (1200, 800), color=(248, 244, 232))
        draw = ImageDraw.Draw(image)
        draw.text((80, 120), "South Quay Locker 14", fill=(20, 20, 20))
        draw.text((80, 180), "2026-04-18 09:14", fill=(20, 20, 20))
        draw.text((80, 240), "Lena Hart", fill=(20, 20, 20))
        exif = Image.Exif()
        exif[270] = "Synthetic demo image linked to South Quay Locker 14"
        exif[315] = "Operation Lens Demo"
        image.save(DEMO_DIR / "06_locker_photo.jpg", exif=exif)

    readme = f"""Synthetic demo seed for {CASE_REF}

Suggested demo query:
{DEMO_QUERY}
"""
    _write_text(DEMO_DIR / "README.txt", readme)
    return sorted(path for path in DEMO_DIR.iterdir() if path.is_file() and path.name != "README.txt")


async def main() -> int:
    files = create_demo_seed()
    con = init_db(settings.duckdb_path)
    create_case(
        con,
        case_ref=CASE_REF,
        case_name=CASE_NAME,
        domain_pack=DOMAIN_PACK,
    )
    print(f"Loading demo case {CASE_REF} from {DEMO_DIR}")
    for path in files:
        result = await ingest_document(
            path,
            case_ref=CASE_REF,
            case_name=CASE_NAME,
            force=False,
        )
        status = "skipped" if result.get("skipped") else "ingested"
        print(f"  {status:8} {path.name} -> {result.get('source_type', path.suffix)}")
    print("\nDemo case ready.")
    print(f"Case ref: {CASE_REF}")
    print(f"Suggested query: {DEMO_QUERY}")
    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
