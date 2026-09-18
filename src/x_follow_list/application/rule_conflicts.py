from __future__ import annotations

from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from x_follow_list.domain.relationships import RelationshipEventType, event_dedupe_key


async def open_blocklist_conflict(
    session: AsyncSession,
    *,
    account_id: str,
    subject_x_user_id: str,
    scan_run_id: str,
    source_key: str,
    reason: str,
    now: str,
) -> None:
    active = await session.scalar(
        text(
            "SELECT id FROM relationship_events WHERE x_account_id=:account "
            "AND subject_x_user_id=:subject AND event_type='FOLLOWING_BLOCKLISTED_ACCOUNT' "
            "AND status IN ('OPEN','ACKNOWLEDGED') LIMIT 1"
        ),
        {"account": account_id, "subject": subject_x_user_id},
    )
    if active is not None:
        await session.execute(
            text("UPDATE relationship_events SET last_seen_at=:now WHERE id=:id"),
            {"now": now, "id": active},
        )
        return
    await session.execute(
        text(
            "INSERT INTO relationship_events "
            "(id,x_account_id,scan_run_id,subject_x_user_id,category,event_type,"
            "dedupe_key,status,created_at,rule_reason,last_seen_at) VALUES "
            "(:id,:account,:run,:subject,'ACTION_ITEM','FOLLOWING_BLOCKLISTED_ACCOUNT',"
            ":dedupe,'OPEN',:now,:reason,:now)"
        ),
        {
            "id": str(uuid4()),
            "account": account_id,
            "run": scan_run_id,
            "subject": subject_x_user_id,
            "dedupe": event_dedupe_key(
                source_key,
                subject_x_user_id,
                RelationshipEventType.FOLLOWING_BLOCKLISTED_ACCOUNT,
            ),
            "now": now,
            "reason": reason,
        },
    )


async def resolve_blocklist_conflict(
    session: AsyncSession, *, account_id: str, subject_x_user_id: str, now: str
) -> None:
    await session.execute(
        text(
            "UPDATE relationship_events SET status='RESOLVED',resolved_at=:now,"
            "last_seen_at=:now,version=version+1 WHERE x_account_id=:account "
            "AND subject_x_user_id=:subject AND event_type='FOLLOWING_BLOCKLISTED_ACCOUNT' "
            "AND status IN ('OPEN','ACKNOWLEDGED')"
        ),
        {"now": now, "account": account_id, "subject": subject_x_user_id},
    )
