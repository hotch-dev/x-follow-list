from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import text

from x_follow_list.application.rule_conflicts import (
    open_blocklist_conflict,
    resolve_blocklist_conflict,
)
from x_follow_list.application.scan_coordination import (
    LeaseLostError,
    ResourceLease,
    ScanClaim,
    ScanCoordinator,
)
from x_follow_list.domain.relationships import (
    EventCategory,
    Membership,
    RelationshipState,
    evaluate_transition,
)
from x_follow_list.persistence.database import Database


class IncompleteScanError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ObservedMember:
    x_user_id: str
    username: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None


@dataclass(frozen=True, slots=True)
class CommittedSnapshot:
    snapshot_id: str
    follower_count: int
    following_count: int


@dataclass(slots=True)
class _CurrentMember:
    follows_me: bool = False
    i_follow: bool = False
    username: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None


class SnapshotCommitService:
    def __init__(
        self, database: Database, fault_injector: Callable[[str], None] | None = None
    ) -> None:
        self._database = database
        self._fault = fault_injector or (lambda _checkpoint: None)

    async def stage(
        self, run_id: str, relationship_type: str, members: Sequence[ObservedMember]
    ) -> None:
        now = datetime.now(UTC).isoformat()
        async with self._database.session() as session:
            parameters = [
                {
                    "r": run_id,
                    "t": relationship_type,
                    "x": member.x_user_id,
                    "u": member.username,
                    "d": member.display_name,
                    "a": member.avatar_url,
                    "n": now,
                }
                for member in members
            ]
            if parameters:
                await session.execute(
                    text(
                        "INSERT INTO scan_staging_memberships (scan_run_id,relationship_type,subject_x_user_id,username,display_name,avatar_url,observed_at) VALUES (:r,:t,:x,:u,:d,:a,:n) ON CONFLICT(scan_run_id,relationship_type,subject_x_user_id) DO UPDATE SET username=excluded.username,display_name=excluded.display_name,avatar_url=excluded.avatar_url,observed_at=excluded.observed_at"
                    ),
                    parameters,
                )
            await session.commit()

    async def complete_side(self, run_id: str, relationship_type: str) -> None:
        async with self._database.session() as session:
            count = await session.scalar(
                text(
                    "SELECT count(*) FROM scan_staging_memberships WHERE scan_run_id=:r AND relationship_type=:t"
                ),
                {"r": run_id, "t": relationship_type},
            )
            await session.execute(
                text(
                    "INSERT INTO scan_staging_progress VALUES (:r,:t,:c,1) ON CONFLICT(scan_run_id,relationship_type) DO UPDATE SET accepted_count=excluded.accepted_count,terminal=1"
                ),
                {"r": run_id, "t": relationship_type, "c": count},
            )
            await session.commit()

    async def cleanup_staging_before(self, cutoff: datetime) -> int:
        cutoff_value = cutoff.astimezone(UTC).isoformat()
        async with self._database.session() as session:
            run_ids = (
                await session.scalars(
                    text("SELECT id FROM scan_runs WHERE created_at < :cutoff"),
                    {"cutoff": cutoff_value},
                )
            ).all()
            if not run_ids:
                return 0
            placeholders = ",".join(f":run_{index}" for index in range(len(run_ids)))
            parameters = {f"run_{index}": run_id for index, run_id in enumerate(run_ids)}
            delete_count = await session.scalar(
                text(
                    f"SELECT count(*) FROM scan_staging_memberships "  # noqa: S608
                    f"WHERE scan_run_id IN ({placeholders})"
                ),
                parameters,
            )
            await session.execute(
                text(
                    f"DELETE FROM scan_staging_memberships "  # noqa: S608
                    f"WHERE scan_run_id IN ({placeholders})"
                ),
                parameters,
            )
            await session.execute(
                text(
                    f"DELETE FROM scan_staging_progress "  # noqa: S608
                    f"WHERE scan_run_id IN ({placeholders})"
                ),
                parameters,
            )
            await session.commit()
            return int(delete_count or 0)

    async def commit(
        self,
        run_id: str,
        *,
        claim: ScanClaim | None = None,
        leases: tuple[ResourceLease, ...] | list[ResourceLease] | None = None,
    ) -> CommittedSnapshot:
        async with self._database.session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            try:
                existing = (
                    (
                        await session.execute(
                            text(
                                "SELECT id,follower_count,following_count FROM relationship_snapshots WHERE scan_run_id=:r"
                            ),
                            {"r": run_id},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing:
                    await session.rollback()
                    return CommittedSnapshot(
                        str(existing["id"]),
                        int(existing["follower_count"]),
                        int(existing["following_count"]),
                    )
                if claim is not None:
                    if leases is None:
                        raise LeaseLostError("fenced snapshot commit requires resource leases")
                    await ScanCoordinator.require_fence(session, claim, leases)
                run = (
                    (
                        await session.execute(
                            text(
                                "SELECT x_account_id FROM scan_runs WHERE id=:r AND status='RUNNING'"
                            ),
                            {"r": run_id},
                        )
                    )
                    .mappings()
                    .one()
                )
                progress_rows = (
                    await session.execute(
                        text(
                            "SELECT relationship_type,terminal FROM scan_staging_progress WHERE scan_run_id=:r"
                        ),
                        {"r": run_id},
                    )
                ).mappings().all()
                progress: dict[str, bool] = {
                    str(row["relationship_type"]): bool(row["terminal"])
                    for row in progress_rows
                }
                if progress != {"FOLLOWER": 1, "FOLLOWING": 1}:
                    raise IncompleteScanError("both collection sides must reach a terminal state")
                rows = (
                    (
                        await session.execute(
                            text(
                                "SELECT relationship_type,subject_x_user_id,username,display_name,avatar_url FROM scan_staging_memberships WHERE scan_run_id=:r"
                            ),
                            {"r": run_id},
                        )
                    )
                    .mappings()
                    .all()
                )
                current: dict[str, _CurrentMember] = {}
                for row in rows:
                    item = current.setdefault(str(row["subject_x_user_id"]), _CurrentMember())
                    item.follows_me |= row["relationship_type"] == "FOLLOWER"
                    item.i_follow |= row["relationship_type"] == "FOLLOWING"
                    item.username = str(row["username"]) if row["username"] is not None else None
                    item.display_name = (
                        str(row["display_name"]) if row["display_name"] is not None else None
                    )
                    item.avatar_url = (
                        str(row["avatar_url"]) if row["avatar_url"] is not None else None
                    )
                follower_count = sum(item.follows_me for item in current.values())
                following_count = sum(item.i_follow for item in current.values())
                account_id = str(run["x_account_id"])
                previous_id = await session.scalar(
                    text(
                        "SELECT id FROM relationship_snapshots WHERE x_account_id=:a ORDER BY captured_at DESC,id DESC LIMIT 1"
                    ),
                    {"a": account_id},
                )
                previous_rows = (
                    []
                    if previous_id is None
                    else (
                        await session.execute(
                            text(
                                "SELECT subject_x_user_id,follows_me,i_follow FROM snapshot_memberships WHERE snapshot_id=:s"
                            ),
                            {"s": previous_id},
                        )
                    )
                    .mappings()
                    .all()
                )
                previous = {
                    str(r["subject_x_user_id"]): Membership(
                        bool(r["follows_me"]), bool(r["i_follow"])
                    )
                    for r in previous_rows
                }
                prior_rows = (
                    (
                        await session.execute(
                            text(
                                "SELECT subject_x_user_id,state,non_followback_streak FROM relationship_states WHERE x_account_id=:a"
                            ),
                            {"a": account_id},
                        )
                    )
                    .mappings()
                    .all()
                )
                prior: dict[str, tuple[RelationshipState, int]] = {
                    str(r["subject_x_user_id"]): (
                        RelationshipState(str(r["state"])),
                        int(r["non_followback_streak"]),
                    )
                    for r in prior_rows
                }
                snapshot_id, now = str(uuid4()), datetime.now(UTC).isoformat()
                await session.execute(
                    text("INSERT INTO relationship_snapshots VALUES (:s,:a,:r,:f,:g,:n)"),
                    {
                        "s": snapshot_id,
                        "a": account_id,
                        "r": run_id,
                        "f": follower_count,
                        "g": following_count,
                        "n": now,
                    },
                )
                self._fault("after_snapshot")
                membership_parameters: list[dict[str, object]] = []
                state_parameters: list[dict[str, object]] = []
                event_parameters: list[dict[str, object]] = []
                for subject in sorted(set(previous) | set(current)):
                    data = current.get(subject, _CurrentMember())
                    membership = Membership(data.follows_me, data.i_follow)
                    if membership.state is not RelationshipState.ABSENT:
                        membership_parameters.append(
                            {
                                "s": snapshot_id,
                                "x": subject,
                                "f": membership.follows_me,
                                "g": membership.i_follow,
                                "u": data.username,
                                "d": data.display_name,
                                "a": data.avatar_url,
                            }
                        )
                    old_state: RelationshipState | None
                    old_streak: int
                    if subject in prior:
                        old_state, old_streak = prior[subject]
                    else:
                        old_state = previous[subject].state if subject in previous else None
                        old_streak = 0
                    previous_membership = previous.get(subject)
                    if (
                        previous_membership is None
                        and old_state is RelationshipState.ABSENT
                    ):
                        previous_membership = Membership(follows_me=False, i_follow=False)
                    result = evaluate_transition(
                        previous=previous_membership,
                        current=membership,
                        previous_state=old_state,
                        previous_non_followback_streak=old_streak,
                        scan_run_id=run_id,
                        subject_x_user_id=subject,
                    )
                    state_parameters.append(
                        {
                            "a": account_id,
                            "x": subject,
                            "st": result.state.value,
                            "k": result.non_followback_streak,
                            "s": snapshot_id,
                            "n": now,
                        }
                    )
                    for event in result.events:
                        event_parameters.append(
                            {
                                "id": str(uuid4()),
                                "a": account_id,
                                "r": run_id,
                                "x": subject,
                                "c": event.category.value,
                                "t": event.event_type.value,
                                "d": event.dedupe_key,
                                "st": "OPEN"
                                if event.category is EventCategory.ACTION_ITEM
                                else "INFORMATIONAL",
                                "n": now,
                            }
                        )
                if membership_parameters:
                    await session.execute(
                        text("INSERT INTO snapshot_memberships VALUES (:s,:x,:f,:g,:u,:d,:a)"),
                        membership_parameters,
                    )
                self._fault("after_memberships")
                if state_parameters:
                    await session.execute(
                        text(
                            "INSERT INTO relationship_states VALUES (:a,:x,:st,:k,:s,:n) ON CONFLICT(x_account_id,subject_x_user_id) DO UPDATE SET state=excluded.state,non_followback_streak=excluded.non_followback_streak,last_snapshot_id=excluded.last_snapshot_id,updated_at=excluded.updated_at"
                        ),
                        state_parameters,
                    )
                self._fault("after_states")
                if event_parameters:
                    await session.execute(
                        text(
                            "INSERT OR IGNORE INTO relationship_events "
                            "(id,x_account_id,scan_run_id,subject_x_user_id,category,"
                            "event_type,dedupe_key,status,created_at) "
                            "VALUES (:id,:a,:r,:x,:c,:t,:d,:st,:n)"
                        ),
                        event_parameters,
                    )
                rules = (
                    await session.execute(
                        text(
                            "SELECT subject_x_user_id,rule_type,reason FROM account_rules "
                            "WHERE x_account_id=:account"
                        ),
                        {"account": account_id},
                    )
                ).mappings().all()
                if rules:
                    await session.execute(
                        text(
                            "INSERT INTO snapshot_rule_hits "
                            "(snapshot_id,subject_x_user_id,rule_type,reason,i_follow) "
                            "VALUES (:snapshot,:subject,:type,:reason,:following)"
                        ),
                        [
                            {
                                "snapshot": snapshot_id,
                                "subject": str(rule["subject_x_user_id"]),
                                "type": str(rule["rule_type"]),
                                "reason": str(rule["reason"]),
                                "following": current.get(
                                    str(rule["subject_x_user_id"]), _CurrentMember()
                                ).i_follow,
                            }
                            for rule in rules
                        ],
                    )
                for rule in rules:
                    if rule["rule_type"] != "BUSINESS_BLOCKLIST":
                        continue
                    subject = str(rule["subject_x_user_id"])
                    if current.get(subject, _CurrentMember()).i_follow:
                        await open_blocklist_conflict(
                            session,
                            account_id=account_id,
                            subject_x_user_id=subject,
                            scan_run_id=run_id,
                            source_key=f"scan:{run_id}",
                            reason=str(rule["reason"]),
                            now=now,
                        )
                    else:
                        await resolve_blocklist_conflict(
                            session,
                            account_id=account_id,
                            subject_x_user_id=subject,
                            now=now,
                        )
                self._fault("after_events")
                await session.execute(
                    text(
                        "UPDATE scan_runs SET status='SUCCESS',follower_count=:f,following_count=:g,finished_at=:n WHERE id=:r"
                    ),
                    {"f": follower_count, "g": following_count, "n": now, "r": run_id},
                )
                self._fault("after_run_update")
                await session.execute(
                    text(
                        "UPDATE x_accounts SET last_successful_scan_at=:n,updated_at=:n WHERE id=:a"
                    ),
                    {"n": now, "a": account_id},
                )
                self._fault("after_account_update")
                if leases is not None:
                    for lease in leases:
                        await session.execute(
                            text(
                                "UPDATE resource_leases SET heartbeat_at=:n,expires_at=:n "
                                "WHERE resource_key=:key AND owner_task_id=:r "
                                "AND fencing_token=:token"
                            ),
                            {
                                "n": now,
                                "key": lease.resource_key,
                                "r": run_id,
                                "token": lease.fencing_token,
                            },
                        )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        async with self._database.session() as cleanup:
            await cleanup.execute(
                text("DELETE FROM scan_staging_progress WHERE scan_run_id=:r"), {"r": run_id}
            )
            await cleanup.execute(
                text("DELETE FROM scan_staging_memberships WHERE scan_run_id=:r"), {"r": run_id}
            )
            await cleanup.commit()
        return CommittedSnapshot(snapshot_id, follower_count, following_count)
