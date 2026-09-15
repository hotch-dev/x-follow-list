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
