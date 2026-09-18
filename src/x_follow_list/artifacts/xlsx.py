from __future__ import annotations

import os
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openpyxl import Workbook
from sqlalchemy import text

from x_follow_list.application.errors import ApplicationError, ResourceNotFoundError
from x_follow_list.persistence.database import Database
from x_follow_list.storage.files import file_metadata

FORMULA_PREFIXES = ("=", "+", "-", "@")
SHEET_NAMES = (
    "Summary",
    "UnfollowedMe",
    "NonFollowers",
    "NewFollowers",
    "Followers",
    "Following",
    "BlocklistConflicts",
    "Rules",
)
MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ACCOUNT_HEADERS = (
    "x_user_id",
    "username",
    "display_name",
    "relationship_state",
    "non_followback_streak",
    "detected_at_utc",
    "detected_at_local",
)


def sanitize_excel_text(value: str | None) -> str | None:
    if value is not None and value.startswith(FORMULA_PREFIXES):
        return f"'{value}"
    return value


def write_xlsx_stream(
    target: Path,
    sheets: Iterable[tuple[str, Iterable[Sequence[object]]]],
) -> int:
    return _write_workbook(target, sheets)


class XlsxArtifactService:
    def __init__(
        self, database: Database, storage_root: Path, display_timezone: str = "UTC"
    ) -> None:
        self._database = database
        self._storage_root = storage_root.resolve()
        self._display_timezone = display_timezone

    async def build(
        self, scan_run_id: str, *, fail_after_rows: int | None = None
    ) -> dict[str, Any]:
        source = await self._load_source(scan_run_id)
        artifact = await self._prepare_artifact(scan_run_id)
        final_path = self._artifact_path(scan_run_id, str(artifact["id"]))
        if artifact["status"] == "READY" and final_path.is_file():
            return _artifact_result(artifact, final_path)

        final_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = final_path.with_name(f".{final_path.stem}.{uuid4().hex}.tmp")
        try:
            sheets = await self._sheet_rows(source)
            _write_workbook(temporary_path, sheets, fail_after_rows)
            digest, byte_size = file_metadata(temporary_path)
            os.replace(temporary_path, final_path)
            ready = await self._mark_ready(
                str(artifact["id"]), final_path, digest, byte_size
            )
            return _artifact_result(ready, final_path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            final_path.unlink(missing_ok=True)
            await self._mark_failed(str(artifact["id"]))
            raise

    async def _load_source(self, scan_run_id: str) -> dict[str, Any]:
        async with self._database.session() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT r.id AS scan_run_id,r.status,r.follower_count,r.following_count,"
                        "r.finished_at,s.id AS snapshot_id,s.captured_at,a.id AS account_id,"
                        "a.x_user_id,a.username,a.display_name "
                        "FROM scan_runs r JOIN relationship_snapshots s ON s.scan_run_id=r.id "
                        "JOIN x_accounts a ON a.id=r.x_account_id "
                        "WHERE r.id=:run"
                    ),
                    {"run": scan_run_id},
                )
            ).mappings().one_or_none()
        if row is None:
            raise ResourceNotFoundError
        if row["status"] != "SUCCESS":
            raise ApplicationError(
                "SCAN_NOT_SUCCESSFUL", "Only successful scans can be exported", 409
            )
        return dict(row)

    async def _prepare_artifact(self, scan_run_id: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        async with self._database.engine.connect() as connection:
            await connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                existing_row = (
                    await connection.execute(
                        text(
                            "SELECT * FROM artifacts WHERE scan_run_id=:run AND kind='XLSX' "
                            "ORDER BY created_at,id LIMIT 1"
                        ),
                        {"run": scan_run_id},
                    )
                ).mappings().one_or_none()
                existing: dict[str, Any]
                if existing_row is None:
                    artifact_id = str(uuid4())
                    await connection.execute(
                        text(
                            "INSERT INTO artifacts "
                            "(id,scan_run_id,kind,status,storage_path,created_at,expires_at) "
                            "VALUES (:id,:run,'XLSX','PENDING','',:now,:expires)"
                        ),
                        {
                            "id": artifact_id,
                            "run": scan_run_id,
                            "now": now.isoformat(),
                            "expires": (now + timedelta(days=30)).isoformat(),
                        },
                    )
                    created_row = (
                        await connection.execute(
                            text("SELECT * FROM artifacts WHERE id=:id"), {"id": artifact_id}
                        )
                    ).mappings().one()
                    existing = dict(created_row)
                else:
                    existing = dict(existing_row)
                if existing["status"] != "READY":
                    await connection.execute(
                        text(
                            "UPDATE artifacts SET status='PENDING',sha256=NULL,byte_size=NULL,"
                            "deleted_at=NULL WHERE id=:id"
                        ),
                        {"id": existing["id"]},
                    )
                    existing["status"] = "PENDING"
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise
        return existing

    async def _sheet_rows(
        self, source: Mapping[str, Any]
    ) -> tuple[tuple[str, Iterable[Sequence[object]]], ...]:
        snapshot_id = str(source["snapshot_id"])
        run_id = str(source["scan_run_id"])
        timezone_name = self._display_timezone
        async with self._database.session() as session:
            memberships = [
                dict(row)
                for row in (
                    await session.execute(
                        text(
                            "SELECT m.*,s.state,s.non_followback_streak "
                            "FROM snapshot_memberships m LEFT JOIN relationship_states s "
                            "ON s.last_snapshot_id=m.snapshot_id "
                            "AND s.subject_x_user_id=m.subject_x_user_id "
                            "WHERE m.snapshot_id=:snapshot ORDER BY m.subject_x_user_id"
                        ),
                        {"snapshot": snapshot_id},
                    )
                ).mappings()
            ]
            events = [
                dict(row)
                for row in (
                    await session.execute(
                        text(
                            "SELECT subject_x_user_id,event_type,created_at "
                            "FROM relationship_events "
                            "WHERE scan_run_id=:run AND event_type IN "
                            "('UNFOLLOWED_ME_AFTER_MUTUAL','NEW_FOLLOWER')"
                        ),
                        {"run": run_id},
                    )
                ).mappings()
            ]
            rule_hits = [
                dict(row)
                for row in (
                    await session.execute(
                        text(
                            "SELECT subject_x_user_id,rule_type,reason,i_follow "
                            "FROM snapshot_rule_hits WHERE snapshot_id=:snapshot "
                            "ORDER BY subject_x_user_id"
                        ),
                        {"snapshot": snapshot_id},
                    )
                ).mappings()
            ]
        by_id = {str(row["subject_x_user_id"]): row for row in memberships}
        event_ids: dict[str, dict[str, object]] = {}
        for event in events:
            event_ids[f"{event['event_type']}:{event['subject_x_user_id']}"] = event

        captured = _as_datetime(source["captured_at"])
        summary = (
            ("field", "value"),
            ("scan_run_id", run_id),
            ("snapshot_id", snapshot_id),
            ("x_account_id", source["account_id"]),
            ("account_x_user_id", source["x_user_id"]),
            ("account_username", source["username"]),
            ("account_display_name", source["display_name"]),
            ("followers_count", source["follower_count"]),
            ("following_count", source["following_count"]),
            ("captured_at_utc", _format_utc(captured)),
            ("timezone", timezone_name),
            ("captured_at_local", _format_local(captured, timezone_name)),
        )

        def selected_rows(
            predicate: Any, event_type: str | None = None
        ) -> Iterator[Sequence[object]]:
            yield ACCOUNT_HEADERS
            for subject_id, row in by_id.items():
                event = event_ids.get(f"{event_type}:{subject_id}") if event_type else None
                if not predicate(row, event):
                    continue
                detected = _as_datetime(event["created_at"] if event else captured)
                yield (
                    subject_id,
                    row["username"],
                    row["display_name"],
                    row["state"] or _state_from_membership(row),
                    row["non_followback_streak"],
                    _format_utc(detected),
                    _format_local(detected, timezone_name),
                )

        def rule_rows(*, conflicts_only: bool) -> Iterator[Sequence[object]]:
            yield (
                "x_user_id",
                "username",
                "display_name",
                "rule_type",
                "reason",
                "i_follow",
                "scan_run_id",
                "snapshot_id",
                "captured_at_utc",
                "captured_at_local",
            )
            for rule in rule_hits:
                if conflicts_only and not (
                    rule["rule_type"] == "BUSINESS_BLOCKLIST" and rule["i_follow"]
                ):
                    continue
                subject_id = str(rule["subject_x_user_id"])
                member = by_id.get(subject_id)
                yield (
                    subject_id,
                    member["username"] if member else None,
                    member["display_name"] if member else None,
                    rule["rule_type"],
                    rule["reason"],
                    bool(rule["i_follow"]),
                    run_id,
                    snapshot_id,
                    _format_utc(captured),
                    _format_local(captured, timezone_name),
                )

        return (
            ("Summary", summary),
            (
                "UnfollowedMe",
                selected_rows(
                    lambda _row, event: event is not None,
                    "UNFOLLOWED_ME_AFTER_MUTUAL",
                ),
            ),
            (
                "NonFollowers",
                selected_rows(
                    lambda row, _event: bool(row["i_follow"])
                    and not row["follows_me"]
                ),
            ),
            (
                "NewFollowers",
                selected_rows(lambda _row, event: event is not None, "NEW_FOLLOWER"),
            ),
            ("Followers", selected_rows(lambda row, _event: bool(row["follows_me"]))),
            ("Following", selected_rows(lambda row, _event: bool(row["i_follow"]))),
            ("BlocklistConflicts", rule_rows(conflicts_only=True)),
            ("Rules", rule_rows(conflicts_only=False)),
        )

    def _artifact_path(self, scan_run_id: str, artifact_id: str) -> Path:
        return self._storage_root / scan_run_id / f"{artifact_id}.xlsx"

    async def _mark_ready(
        self, artifact_id: str, path: Path, digest: str, byte_size: int
    ) -> dict[str, Any]:
        async with self._database.session() as session:
            await session.execute(
                text(
                    "UPDATE artifacts SET status='READY',storage_path=:path,sha256=:sha,"
                    "byte_size=:size,deleted_at=NULL WHERE id=:id"
                ),
                {"path": str(path), "sha": digest, "size": byte_size, "id": artifact_id},
            )
            await session.commit()
            row = (
                await session.execute(
                    text("SELECT * FROM artifacts WHERE id=:id"), {"id": artifact_id}
                )
            ).mappings().one()
        return dict(row)

    async def _mark_failed(self, artifact_id: str) -> None:
        async with self._database.session() as session:
            await session.execute(
                text(
                    "UPDATE artifacts SET status='FAILED',storage_path='',sha256=NULL,"
                    "byte_size=NULL WHERE id=:id"
                ),
                {"id": artifact_id},
            )
            await session.commit()


def _write_workbook(
    target: Path,
    sheets: Iterable[tuple[str, Iterable[Sequence[object]]]],
    fail_after_rows: int | None = None,
) -> int:
    workbook = Workbook(write_only=True)
    row_count = 0
    try:
        for name, rows in sheets:
            worksheet = workbook.create_sheet(name)
            for row in rows:
                worksheet.append([_safe_cell(value) for value in row])
                row_count += 1
                if fail_after_rows is not None and row_count >= fail_after_rows:
                    worksheet.close()
                    raise OSError("injected XLSX write failure")
        workbook.save(target)
        return row_count
    except BaseException:
        workbook.close()
        raise


def _safe_cell(value: object) -> object:
    return sanitize_excel_text(value) if isinstance(value, str) else value


def _state_from_membership(row: Mapping[str, Any]) -> str:
    if row["follows_me"] and row["i_follow"]:
        return "MUTUAL"
    if row["i_follow"]:
        return "NOT_FOLLOWING_BACK"
    return "FOLLOWS_ME_ONLY"


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _format_local(value: datetime, timezone_name: str) -> str:
    try:
        timezone: tzinfo = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone = UTC
    return value.astimezone(timezone).isoformat()


def _artifact_result(row: Mapping[str, Any], path: Path) -> dict[str, Any]:
    result = dict(row)
    result["storage_path"] = str(path)
    return result
