"""静态验证单机 Release Bundle 的版本、安全和数据边界。"""

import json
import re
from pathlib import Path

import yaml


def test_product_versions_are_unified() -> None:
    """验证 Python、前端和锁文件根包版本统一为 2.0.0。"""

    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    package = json.loads(Path("frontend/package.json").read_text(encoding="utf-8"))
    lock = json.loads(Path("frontend/package-lock.json").read_text(encoding="utf-8"))

    assert re.search(r'^version = "2\.0\.0"$', pyproject, re.MULTILINE)
    assert package["version"] == "2.0.0"
    assert lock["version"] == "2.0.0"
    assert lock["packages"][""]["version"] == "2.0.0"


def test_compose_uses_loopback_nonroot_readonly_single_service() -> None:
    """验证参考部署只公开回环端口并应用最小容器权限。"""

    raw = Path("compose.yaml").read_text(encoding="utf-8")
    normalized = re.sub(r"\$\{[^}]+\}", "placeholder", raw)
    compose = yaml.safe_load(normalized)
    app = compose["services"]["app"]

    assert set(compose["services"]) == {"app"}
    assert app["ports"][0].startswith("127.0.0.1:")
    assert app["read_only"] is True
    assert app["user"] == "10001:10001"
    assert app["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in app["security_opt"]
    assert app["stop_grace_period"] == "40s"
    assert app["logging"]["options"] == {"max-size": "10m", "max-file": "3"}


def test_bundle_excludes_runtime_data_and_has_runtime_only_lock() -> None:
    """验证镜像上下文不包含数据、Secret 和测试工具，运行锁仍包含业务依赖。"""

    dockerignore = Path(".dockerignore").read_text(encoding="utf-8")
    runtime = set(Path("requirements.runtime.lock").read_text().splitlines())

    assert {".env", "var", "tests", "frontend/node_modules"} <= set(
        dockerignore.splitlines()
    )
    assert "langgraph==1.2.9" in runtime
    assert "fastapi==0.139.2" in runtime
    assert "openai==2.45.0" in runtime
    assert not any(item.startswith("pytest==") for item in runtime)
    assert not any(item.startswith("ruff==") for item in runtime)


def test_dockerfile_requires_release_facts_and_nonroot_entrypoint() -> None:
    """验证正式镜像构建必须生成清单且最终不以 root 运行。"""

    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    for argument in (
        "APP_VERSION",
        "GIT_COMMIT",
        "BUILD_TIMESTAMP",
        "OFFLINE_BASELINE_ID",
    ):
        assert f"ARG {argument}" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "commerce_resolve.operations.manifest" in dockerfile
    assert 'ENTRYPOINT ["python", "-m", "commerce_resolve"]' in dockerfile


def test_host_lifecycle_refuses_dirty_release_builds() -> None:
    """验证正式镜像不会把未提交源码标记成旧 Git Commit。"""

    script = Path("deploy/commerce-resolve").read_text(encoding="utf-8")

    assert "git status --porcelain" in script
    assert "正式镜像只能从无未提交修改的 Git 工作树构建" in script


def test_host_lifecycle_preserves_service_state_for_stopped_operations() -> None:
    """验证备份恢复只重启原本运行的服务，显式 restart 会重建容器。"""

    script = Path("deploy/commerce-resolve").read_text(encoding="utf-8")

    assert "was_running=false" in script
    assert 'if [ "$was_running" = true ]; then' in script
    assert "compose up -d --force-recreate --wait app" in script
