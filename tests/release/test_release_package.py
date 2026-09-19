from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_release_package_documents_operable_deploy_recovery_and_limitations() -> None:
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    deployment = (ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")
    recovery = (ROOT / "docs" / "backup-restore.md").read_text(encoding="utf-8")
    limitations = (ROOT / "docs" / "known-limitations.md").read_text(encoding="utf-8")

    for service in ("api:", "worker:", "web:"):
        assert service in compose
    assert "healthcheck:" in compose
    assert "artifacts" in compose
    for term in ("生产配置", "密钥", "健康检查", "迁移", "回滚"):
        assert term in deployment
    for term in ("备份", "恢复演练", "SHA-256", "30 天"):
        assert term in recovery
    assert "不再测试 AdsPower" in limitations
    assert "不访问 X 生产环境" in limitations


def test_windows_start_script_migrates_and_starts_the_integrated_local_stack() -> None:
    script = (ROOT / "scripts" / "start-windows.ps1").read_text(encoding="utf-8")
    vite_config = (ROOT / "web" / "vite.config.ts").read_text(encoding="utf-8")

    assert "X_FOLLOW_LIST_APP_ORIGIN" in script
    assert "X_FOLLOW_LIST_DATA_DIR" in script
    assert '& $python "-m" "alembic" "upgrade" "head"' in script
    assert "x-follow-list-api.exe" in script
    assert "x-follow-list-worker.exe" in script
    assert "vite.js" in script
    assert "WindowStyle Hidden" in script
    assert "/health/ready" in script
    assert "bootstrap-token" in script
    assert "proxy:" in vite_config
    assert "http://127.0.0.1:8000" in vite_config
