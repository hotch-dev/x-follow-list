from __future__ import annotations

from x_follow_list.application.scan_coordination import (
    ResourceLease,
    ScanClaim,
    ScanCoordinator,
)
from x_follow_list.application.snapshots import ObservedMember, SnapshotCommitService
from x_follow_list.artifacts.xlsx import XlsxArtifactService
from x_follow_list.browser.contracts import BrowserSession
from x_follow_list.collector.models import RelationshipSide
from x_follow_list.collector.navigation import DomNavigator
from x_follow_list.collector.parser import VersionedRelationshipParser
from x_follow_list.collector.response import ResponseCollector
from x_follow_list.persistence.database import Database


class RelationshipScanJob:
    """Run the complete provider-independent collection and publication path."""

    def __init__(
        self,
        database: Database,
        coordinator: ScanCoordinator,
        artifacts: XlsxArtifactService,
        profile_url: str,
    ) -> None:
        self._snapshots = SnapshotCommitService(database)
        self._coordinator = coordinator
        self._artifacts = artifacts
        self._navigator = DomNavigator(profile_url)

    async def __call__(
        self,
        claim: ScanClaim,
        leases: tuple[ResourceLease, ...],
        session: BrowserSession,
    ) -> None:
        try:
            for side in (RelationshipSide.FOLLOWER, RelationshipSide.FOLLOWING):
                collection = await ResponseCollector(
                    VersionedRelationshipParser()
                ).collect(session.context.pages[0], self._navigator.collect, side)
                await self._snapshots.stage(
                    claim.run_id,
                    side.value,
                    [
                        ObservedMember(
                            item.x_user_id,
                            item.username,
                            item.display_name,
                            item.avatar_url,
                        )
                        for item in collection.items
                    ],
                )
                await self._snapshots.complete_side(claim.run_id, side.value)
            await self._snapshots.commit(claim.run_id, claim=claim, leases=leases)
        except Exception:
            await self._coordinator.finish_failed(
                claim, leases, "COLLECTION_FAILED", "relationship collection failed"
            )
            raise
        await self._artifacts.build(claim.run_id)
