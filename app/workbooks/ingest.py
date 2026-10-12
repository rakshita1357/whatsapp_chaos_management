"""Workbook ingestion: open, classify, map, normalize, version, load master, observe, diff."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.blobstore import sha256_file
from app.core.config import get_settings
from app.core.time import ist_date
from app.db.models import (
    Event,
    Observation,
    ReviewItem,
    WorkbookDiff,
    WorkbookDiffItem,
    WorkbookImport,
    WorkbookRow,
    WorkbookSheet,
)
from app.entities.registry import client_by_key
from app.entities.tata import MasterLoadStats, find_division_entities, load_master_rows
from app.workbooks.diff import DiffResult, RowSnap, compute_diff, group_rows
from app.workbooks.mapping import ColumnMap, map_headers
from app.workbooks.normalize import division_key, normalize_cell
from app.workbooks.open import OpenedWorkbook, UnsupportedWorkbook, open_workbook
from app.workbooks.roles import family_key, infer_role
from app.workbooks.settings import WorkbookConfig, load_workbook_config
from app.workbooks.sheets import classify_sheet

EXTRACTOR = "workbook"
EXTRACTOR_VERSION = "1"


@dataclass
class WorkbookResult:
    import_: WorkbookImport | None
    created: bool
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class _ParsedSheet:
    name: str
    visible: bool
    kind: str
    header_row: int | None
    max_row: int
    max_col: int
    mapping: list[ColumnMap]
    rows: list[dict[str, Any]]  # row_number, raw, norm, types, issues


def _parse_sheet(sheet: Any, cfg: WorkbookConfig, role_hint: str) -> _ParsedSheet:
    info = classify_sheet(sheet, cfg)
    ps = _ParsedSheet(
        sheet.name,
        sheet.visible,
        info.kind,
        info.header_row,
        sheet.max_row,
        sheet.max_col_effective,
        [],
        [],
    )
    if info.kind != "data_table" or info.header_row is None:
        return ps
    ps.mapping = map_headers(sheet.rows[info.header_row - 1], cfg)
    for offset, cells in enumerate(sheet.rows[info.header_row :], start=info.header_row + 1):
        if all(c is None or (isinstance(c, str) and not c.strip()) for c in cells):
            continue
        raw: dict[str, Any] = {}
        norm: dict[str, Any] = {}
        types: dict[str, str] = {}
        issues: list[dict[str, Any]] = []
        for cm in ps.mapping:
            value = cells[cm.index] if cm.index < len(cells) else None
            cell = normalize_cell(value, cm.canonical, role_hint, cfg)
            raw[cm.header_raw] = cell.raw
            types[cm.header_raw] = cell.type
            if cm.canonical:
                norm[cm.canonical] = cell.normalized
                if cell.normalized_text is not None:
                    norm[f"{cm.canonical}__text"] = cell.normalized_text
                if cell.parse_status in ("invalid", "unmapped"):
                    issues.append(
                        {
                            "column": cm.header_raw,
                            "field": cm.canonical,
                            "code": cell.parse_status,
                            "raw": cell.raw,
                        }
                    )
        _derive_dealer_code(norm)
        ps.rows.append(
            {"row_number": offset, "raw": raw, "norm": norm, "types": types, "issues": issues}
        )
    return ps


def _derive_dealer_code(norm: dict[str, Any]) -> None:
    """The real Dealer List has no Dealer Code column: it is the prefix of the Division Code
    (e.g. 3000060-Sv&Pa-Derikplaza-DetRik -> 3000060). Placeholder rows get none."""
    div = norm.get("division_code")
    if norm.get("dealer_code") or not div or "new setup" in str(div).casefold():
        return
    prefix = str(div).split("-")[0].strip()
    if prefix:
        norm["dealer_code"] = prefix


def _row_key(norm: dict[str, Any], raw: dict[str, Any]) -> str | None:
    div = norm.get("division_code")
    if div:
        if "new setup" in str(div).casefold():
            return None  # placeholders get a key from the master loader fingerprint
        return division_key(div)
    return f"dealer:{norm['dealer_code']}" if norm.get("dealer_code") else None


def import_workbook(
    session: Session,
    path: Path,
    *,
    filename: str,
    posted_at: datetime | None,
    posted_by: str | None = None,
    attachment_id: uuid.UUID | None = None,
    group_id: uuid.UUID | None = None,
    client_key: str | None = None,
    cfg: WorkbookConfig | None = None,
    derived_dir: Path | None = None,
) -> WorkbookResult:
    cfg = cfg or load_workbook_config()
    sha = sha256_file(path)
    existing = session.scalar(select(WorkbookImport).where(WorkbookImport.sha256 == sha))
    if existing is not None:  # same sha256 posted again -> same version, nothing new
        return WorkbookResult(existing, False, {"duplicate_of": str(existing.id)})
    try:
        opened = open_workbook(
            path,
            derived_dir or get_settings().derived_dir / "sanitized",
            int(cfg.settings.get("effective_max_cols_scan", 512)),
            suffix=Path(filename).suffix or path.suffix,
        )
    except UnsupportedWorkbook as exc:
        session.add(
            ReviewItem(
                kind="invalid_value",
                group_id=group_id,
                subject_refs={
                    "attachment_id": str(attachment_id) if attachment_id else None,
                    "filename": filename,
                },
                note=f"Workbook could not be opened: {exc}",
            )
        )
        return WorkbookResult(None, False, {"error": str(exc)})

    parsed = [_parse_sheet(s, cfg, "ANY") for s in opened.sheets]
    data_sheets = [p for p in parsed if p.kind == "data_table"]
    primary = max(data_sheets, key=lambda p: len(p.rows), default=None)
    only_pivots = primary is None and any(p.kind == "pivot_summary" for p in parsed)
    readiness = (
        [r["norm"].get("readiness") or r["norm"].get("readiness__text") for r in primary.rows]
        if primary
        else []
    )
    role = infer_role(
        [p.name for p in parsed], len(primary.rows) if primary else 0, readiness, only_pivots, cfg
    )
    if primary is not None:  # re-normalize enums with the real role
        parsed = [_parse_sheet(s, cfg, role) for s in opened.sheets]
        primary = max(
            [p for p in parsed if p.kind == "data_table"], key=lambda p: len(p.rows), default=None
        )
    canon = [m.canonical for m in primary.mapping] if primary else []
    fam = family_key(filename, role, canon)
    imp = WorkbookImport(
        attachment_id=attachment_id,
        sha256=sha,
        family_key=fam,
        role=role,
        posted_at=posted_at,
        posted_by=posted_by,
        opened_via=opened.opened_via,
        warnings=list(opened.warnings),
    )
    if posted_at is None:
        imp.warnings = [
            *imp.warnings,
            "posted_at unknown: version order falls back to import order",
        ]
    session.add(imp)
    session.flush()

    rows_by_sheet: dict[str, list[WorkbookRow]] = {}
    for p in parsed:
        sheet = WorkbookSheet(
            import_id=imp.id,
            name=p.name,
            visible=p.visible,
            kind=p.kind,
            header_row=p.header_row,
            max_row=p.max_row,
            max_col_effective=p.max_col,
            column_mapping={"columns": [m.as_json() for m in p.mapping]},
        )
        session.add(sheet)
        session.flush()
        objs: list[WorkbookRow] = []
        for r in p.rows:
            obj = WorkbookRow(
                sheet_id=sheet.id,
                row_number=r["row_number"],
                row_key=_row_key(r["norm"], r["raw"]),
                raw=r["raw"],
                normalized=r["norm"],
                cell_types=r["types"],
                issues=r["issues"],
            )
            session.add(obj)
            objs.append(obj)
        session.flush()
        rows_by_sheet[p.name] = objs
        _row_review_items(session, group_id, p, objs)
    _header_review_items(session, group_id, parsed)
    _renumber_versions(session, fam)

    stats: dict[str, Any] = {
        "role": role,
        "family_key": fam,
        "opened_via": opened.opened_via,
        "rows": len(primary.rows) if primary else 0,
    }
    client = client_by_key(session, client_key or str(cfg.settings.get("default_client", "tata")))
    client_id = client.id if client else None
    if primary is not None and role in ("MASTER", "READY_LIST", "IMPLEMENTATION_PLAN"):
        prows = rows_by_sheet[primary.name]
        headers = {m.canonical: m.header_raw for m in primary.mapping if m.canonical}
        if role == "MASTER":
            ms: MasterLoadStats = load_master_rows(
                session, prows, client_id, cfg, headers, group_id
            )
            for row in prows:
                row.entity_id = ms.entity_by_row.get(row.id)
            stats["master"] = ms.as_json()
        else:
            keys = [r.row_key for r in prows if r.row_key]
            found = find_division_entities(session, client_id, keys)
            for row in prows:
                row.entity_id = found.get(row.row_key or "")
            stats["unmatched_rows"] = sum(1 for r in prows if r.entity_id is None)
        stats["observations"] = _write_observations(session, imp, primary, prows, role, headers)
    if primary is not None:
        recompute_diffs(session, fam, cfg)
        _removed_from_ready_list(session, imp, cfg)
    session.flush()
    return WorkbookResult(imp, True, stats)


# ----------------------------------------------------------------------- review items
def _header_review_items(
    session: Session, group_id: uuid.UUID | None, parsed: list[_ParsedSheet]
) -> None:
    for p in parsed:
        for m in p.mapping:
            if m.method == "exact":
                continue
            session.add(
                ReviewItem(
                    kind="unknown_header",
                    group_id=group_id,
                    subject_refs={"sheet": p.name, "header": m.header_raw},
                    proposed_value={"canonical": m.canonical, "score": m.score, "method": m.method},
                    note="Unknown header: values are stored raw (INC-43)"
                    if m.method == "unknown"
                    else "Fuzzy header match is only a proposal",
                )
            )


def _row_review_items(
    session: Session, group_id: uuid.UUID | None, p: _ParsedSheet, rows: list[WorkbookRow]
) -> None:
    seen_enum: set[tuple[str, str]] = set()
    for row in rows:
        for issue in row.issues:
            if issue["code"] == "unmapped":
                k = (issue["field"], str(issue["raw"]))
                if k in seen_enum:
                    continue
                seen_enum.add(k)
                kind = "unmapped_status_value"
            else:
                kind = "invalid_value"
            session.add(
                ReviewItem(
                    kind=kind,
                    group_id=group_id,
                    subject_refs={
                        "workbook_row_id": str(row.id),
                        "sheet": p.name,
                        "row_number": row.row_number,
                        "column": issue["column"],
                    },
                    proposed_value={"raw": issue["raw"]},
                    note=f"{issue['code']} value in {issue['field']}",
                )
            )


# ----------------------------------------------------------------------- versions
def _renumber_versions(session: Session, fam: str) -> None:
    """Version order is by the posting message time, never filename or file mtime (INC-13)."""
    imps = list(session.scalars(select(WorkbookImport).where(WorkbookImport.family_key == fam)))
    floor = datetime.min.replace(tzinfo=UTC)
    imps.sort(key=lambda i: (i.posted_at is None, i.posted_at or floor, i.created_at))
    for n, i in enumerate(imps, start=1):
        i.version_no = n


# ----------------------------------------------------------------------- observations
def _write_observations(
    session: Session,
    imp: WorkbookImport,
    primary: _ParsedSheet,
    rows: list[WorkbookRow],
    role: str,
    headers: dict[str, str],
) -> int:
    if imp.posted_at is None:
        return 0
    n = 0
    for row in rows:
        if row.entity_id is None:
            continue
        ref = {
            "workbook_import_id": str(imp.id),
            "workbook_row_id": str(row.id),
            "sheet": primary.name,
            "row_number": row.row_number,
        }
        eff = ist_date(imp.posted_at)
        readiness = row.normalized.get("readiness")
        if readiness:
            session.add(
                Observation(
                    entity_id=row.entity_id,
                    dimension="readiness",
                    value=readiness,
                    polarity="positive" if readiness == "ready" else "negative",
                    observed_at=imp.posted_at,
                    effective_date=eff,
                    source_kind="workbook_cell",
                    source_ref={**ref, "column": headers.get("readiness")},
                    extractor=EXTRACTOR,
                    extractor_version=EXTRACTOR_VERSION,
                    confidence=1.0,
                )
            )
            n += 1
        if role == "READY_LIST":
            session.add(
                Observation(
                    entity_id=row.entity_id,
                    dimension="readiness_listed",
                    value="true",
                    polarity="positive",
                    observed_at=imp.posted_at,
                    effective_date=eff,
                    source_kind="workbook_cell",
                    source_ref=ref,
                    extractor=EXTRACTOR,
                    extractor_version=EXTRACTOR_VERSION,
                    confidence=1.0,
                )
            )
            n += 1
    return n


def _snaps(rows: list[WorkbookRow], mapping: list[dict[str, Any]]) -> list[RowSnap]:
    canon_by_header = {m["header_raw"]: m["canonical"] for m in mapping if m["canonical"]}
    out: list[RowSnap] = []
    for r in rows:
        if not r.row_key:
            continue
        raw = {canon_by_header[h]: v for h, v in r.raw.items() if h in canon_by_header}
        out.append(RowSnap(r.row_key, raw, dict(r.normalized)))
    return out


def _primary_sheet(session: Session, imp: WorkbookImport) -> WorkbookSheet | None:
    sheets = list(
        session.scalars(
            select(WorkbookSheet).where(
                WorkbookSheet.import_id == imp.id, WorkbookSheet.kind == "data_table"
            )
        )
    )
    best: tuple[int, WorkbookSheet] | None = None
    for s in sheets:
        n = len(session.scalars(select(WorkbookRow.id).where(WorkbookRow.sheet_id == s.id)).all())
        if best is None or n > best[0]:
            best = (n, s)
    return best[1] if best else None


def load_snaps(session: Session, imp: WorkbookImport) -> tuple[list[RowSnap], dict[str, str]]:
    sheet = _primary_sheet(session, imp)
    if sheet is None:
        return [], {}
    cols = sheet.column_mapping.get("columns", [])
    rows = list(
        session.scalars(
            select(WorkbookRow)
            .where(WorkbookRow.sheet_id == sheet.id)
            .order_by(WorkbookRow.row_number)
        )
    )
    headers = {m["canonical"]: m["header_raw"] for m in cols if m["canonical"]}
    return _snaps(rows, cols), headers


def diff_imports(
    session: Session, old: WorkbookImport, new: WorkbookImport, cfg: WorkbookConfig | None = None
) -> WorkbookDiff:
    cfg = cfg or load_workbook_config()
    old_snaps, old_h = load_snaps(session, old)
    new_snaps, new_h = load_snaps(session, new)
    res: DiffResult = compute_diff(old_snaps, new_snaps, old_h, new_h, cfg)
    d = WorkbookDiff(from_import_id=old.id, to_import_id=new.id, summary=res.summary)
    session.add(d)
    session.flush()
    for it in res.items:
        session.add(
            WorkbookDiffItem(
                diff_id=d.id,
                row_key=it.row_key,
                kind=it.kind,
                column=it.column,
                old_raw=None if it.old_raw is None else str(it.old_raw),
                new_raw=None if it.new_raw is None else str(it.new_raw),
                old_norm=None if it.old_norm is None else str(it.old_norm),
                new_norm=None if it.new_norm is None else str(it.new_norm),
                annotations=it.annotations,
            )
        )
    return d


def recompute_diffs(session: Session, fam: str, cfg: WorkbookConfig) -> None:
    imps = list(
        session.scalars(
            select(WorkbookImport)
            .where(WorkbookImport.family_key == fam)
            .order_by(WorkbookImport.version_no)
        )
    )
    wanted = {(a.id, b.id) for a, b in zip(imps, imps[1:], strict=False)}
    have = {
        (d.from_import_id, d.to_import_id): d
        for d in session.scalars(
            select(WorkbookDiff).where(WorkbookDiff.from_import_id.in_([i.id for i in imps]))
        )
    }
    for pair, d in have.items():
        if pair not in wanted:  # a late-arriving older version changed adjacency
            session.execute(delete(WorkbookDiffItem).where(WorkbookDiffItem.diff_id == d.id))
            session.delete(d)
    session.flush()
    by_id = {i.id: i for i in imps}
    for a, b in wanted:
        if (a, b) not in have:
            diff_imports(session, by_id[a], by_id[b], cfg)


def _removed_from_ready_list(session: Session, imp: WorkbookImport, cfg: WorkbookConfig) -> None:
    """INC-47: a row leaving a READY_LIST is observed as readiness_listed=false, reason unknown."""
    if imp.role != "READY_LIST" or imp.posted_at is None:
        return
    prev = session.scalar(
        select(WorkbookImport).where(
            WorkbookImport.family_key == imp.family_key,
            WorkbookImport.version_no == imp.version_no - 1,
        )
    )
    if prev is None:
        return
    old_snaps, _ = load_snaps(session, prev)
    new_snaps, _ = load_snaps(session, imp)
    gone = {s.key for s in old_snaps} - {s.key for s in new_snaps}
    if not gone:
        return
    sheet = _primary_sheet(session, prev)
    if sheet is None:
        return
    old_rows = {
        r.row_key: r
        for r in session.scalars(
            select(WorkbookRow).where(
                WorkbookRow.sheet_id == sheet.id, WorkbookRow.row_key.in_(gone)
            )
        )
    }
    for key, row in old_rows.items():
        if row.entity_id is None:
            continue
        since = imp.posted_at - timedelta(days=7)
        blockers = [
            str(e)
            for e in session.scalars(
                select(Event.id).where(
                    Event.entity_id == row.entity_id,
                    Event.type.in_(["issue", "not_installed_report"]),
                    Event.opened_at >= since,
                    Event.opened_at <= imp.posted_at,
                )
            )
        ]
        session.add(
            Observation(
                entity_id=row.entity_id,
                dimension="readiness_listed",
                value="false",
                polarity="negative",
                observed_at=imp.posted_at,
                effective_date=ist_date(imp.posted_at),
                source_kind="workbook_cell",
                source_ref={
                    "workbook_import_id": str(imp.id),
                    "removed_from": str(prev.id),
                    "row_key": key,
                    "reason": "unknown",
                    "linked_chat_blockers": blockers,
                },
                extractor=EXTRACTOR,
                extractor_version=EXTRACTOR_VERSION,
                confidence=1.0,
            )
        )


def ready_delta(
    session: Session,
    master: WorkbookImport,
    ready_list: WorkbookImport,
    cfg: WorkbookConfig | None = None,
) -> dict[str, int]:
    """MASTER rows that are ready vs a READY_LIST: newly ready and dropped (golden: 160 / 8)."""
    cfg = cfg or load_workbook_config()
    ready_vals = set(cfg.settings.get("ready_values", ["ready"]))
    m_snaps, m_headers = load_snaps(session, master)
    r_snaps, r_headers = load_snaps(session, ready_list)
    # keys whose duplicate rows disagree are conflicts, never counted as ready or listed
    m_cols = sorted(c for c in m_headers if cfg.field_type(c) != "positional")
    r_cols = sorted(c for c in r_headers if cfg.field_type(c) != "positional")
    m_unique, _ = group_rows(m_snaps, m_cols)
    r_unique, _ = group_rows(r_snaps, r_cols)
    master_ready = {k for k, s in m_unique.items() if s.norm.get("readiness") in ready_vals}
    listed = set(r_unique)
    return {"newly_ready": len(master_ready - listed), "dropped": len(listed - master_ready)}


__all__ = ["import_workbook", "diff_imports", "ready_delta", "WorkbookResult", "OpenedWorkbook"]
