from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import openpyxl

HEADERS = [
    "Sr. No.",
    "Dealer Code",
    "Division",
    "City",
    "State",
    "Pin",
    "Readiness",
    "Site Ready Date",
    "Planned Installation Date",
    "Dispatch Date",
    "Dispatch Status",
]


def make_xlsx(path: Path, sheets: dict[str, list[list[Any]]], hidden: tuple[str, ...] = ()) -> Path:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
        if name in hidden:
            ws.sheet_state = "hidden"
    wb.save(path)
    return path


def row(sr: int, dealer: str, div: str, city: str, readiness: str, **kw: Any) -> list[Any]:
    return [
        sr,
        dealer,
        div,
        city,
        kw.get("state", "Bihar"),
        kw.get("pin", "800001"),
        readiness,
        kw.get("ready"),
        kw.get("plan"),
        kw.get("dispatch"),
        kw.get("dstatus"),
    ]


def add_fake_pivot(src: Path, dest: Path) -> Path:
    """Copy of an xlsx with pivot parts, a <pivotCaches> element and pivot relationships added."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dest, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "xl/workbook.xml":
                data = data.replace(
                    b"</workbook>",
                    b'<pivotCaches><pivotCache cacheId="1" r:id="rIdP"/></pivotCaches></workbook>',
                )
            if info.filename == "xl/_rels/workbook.xml.rels":
                data = data.replace(
                    b"</Relationships>",
                    b'<Relationship Id="rIdP" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/pivotCacheDefinition" Target="pivotCache/pivotCacheDefinition1.xml"/></Relationships>',
                )
            if info.filename == "[Content_Types].xml":
                data = data.replace(
                    b"</Types>",
                    b'<Override PartName="/xl/pivotCache/pivotCacheDefinition1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.pivotCacheDefinition+xml"/></Types>',
                )
            zout.writestr(info, data)
        zout.writestr("xl/pivotCache/pivotCacheDefinition1.xml", "<broken")
        zout.writestr("xl/pivotTables/pivotTable1.xml", "<broken")
    return dest
