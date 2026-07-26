"""验证 v1.0 运维 Schema、清单校验和稳定退出码。"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from commerce_resolve.operations.models import (
    OperationExitCode,
    PreflightMode,
    ReleaseManifest,
)


def test_release_manifest_rejects_unknown_fields() -> None:
    """验证发布清单不会静默接受运行环境注入的额外版本字段。"""

    payload = {
        "app_version": "1.0.0",
        "git_commit": "a" * 40,
        "build_timestamp": datetime.now(UTC),
        "python_version": "3.12.8",
        "frontend_version": "1.0.0",
        "frontend_asset_hash": "a" * 64,
        "runtime_lock_hash": "b" * 64,
        "npm_lock_hash": "c" * 64,
        "policy_source_hash": "d" * 64,
        "business_schema_head": "20260721_0005",
        "offline_baseline_id": "baseline-v1",
        "runtime_override": "forbidden",
    }

    with pytest.raises(ValidationError):
        ReleaseManifest.model_validate(payload)


def test_operations_contract_has_fixed_modes_and_exit_codes() -> None:
    """验证自动化依赖的模式集合与退出码不会随文案变化。"""

    assert {item.value for item in PreflightMode} == {
        "init",
        "serve",
        "backup",
        "restore",
        "upgrade",
        "status",
    }
    assert OperationExitCode.INSTANCE_LOCKED == 4
    assert OperationExitCode.INVALID_BACKUP == 5
    assert OperationExitCode.SECURITY_REJECTED == 8
