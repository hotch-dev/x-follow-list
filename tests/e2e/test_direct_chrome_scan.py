from pathlib import Path

import pytest

from x_follow_list.browser.contracts import BrowserSessionRequest, ProviderConfig
from x_follow_list.browser.direct_chrome import DirectChromeProvider
from x_follow_list.collector.models import RelationshipSide
from x_follow_list.collector.navigation import DomNavigator
from x_follow_list.collector.parser import VersionedRelationshipParser
from x_follow_list.collector.response import ResponseCollector


@pytest.mark.asyncio
async def test_direct_chrome_profile_survives_restart_and_scans_local_fixture(
    tmp_path: Path,
    fixture_origin: str,
) -> None:
    profiles_root = tmp_path / "managed-profiles"
    config = ProviderConfig(
        "DIRECT_CHROME", 1, {"profiles_root": str(profiles_root.resolve())}
    )
    provider = DirectChromeProvider(forbidden_profile_roots=frozenset())
    request = BrowserSessionRequest(config, "alice", "bind-one", headless=True)

    first = await provider.acquire(request)
    page = first.context.pages[0]
    collector = ResponseCollector(VersionedRelationshipParser())
    result = await collector.collect(
        page,
        DomNavigator(f"{fixture_origin}/profile?scenario=normal").collect,
        RelationshipSide.FOLLOWER,
    )
    await page.evaluate("localStorage.setItem('bound-x-user-id', 'x-123')")
    await first.close()

    second = await provider.acquire(
        BrowserSessionRequest(config, "alice", "scan-one", headless=True)
    )
    restarted_page = second.context.pages[0]
    await restarted_page.goto(f"{fixture_origin}/profile?scenario=normal")
    persisted_identity = await restarted_page.evaluate(
        "localStorage.getItem('bound-x-user-id')"
    )
    await second.close()
    deleted = await provider.delete_profile(config, "alice")

    assert [member.x_user_id for member in result.items] == ["101", "102", "103"]
    assert persisted_identity == "x-123"
    assert deleted is True
    assert not (profiles_root / "alice").exists()
