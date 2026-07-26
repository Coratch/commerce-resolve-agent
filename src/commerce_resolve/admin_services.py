"""构建 Eval Artifact 与有限系统状态的只读运营投影。"""

from __future__ import annotations

import re
from pathlib import Path

from commerce_resolve.admin_models import (
    AdminEvalSnapshot,
    AdminEvalSuite,
    AdminSystemSnapshot,
)
from commerce_resolve.operations.models import ReleaseManifest
from commerce_resolve.web.health import readiness_state
from commerce_resolve.web.settings import DeploymentSettings

RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class AdminEvalReader:
    """只在固定目录内读取严格校验的 Eval Artifact 与 Baseline。"""

    def __init__(self, run_root: Path, baseline_path: Path) -> None:
        """保存固定服务端路径，不接受请求覆盖。"""

        self._run_root = run_root
        self._baseline_path = baseline_path

    def latest(self) -> AdminEvalSnapshot:
        """读取最近 Candidate；缺失、损坏和回归使用稳定四态返回。"""

        candidates = self._candidate_directories()
        if not candidates:
            return AdminEvalSnapshot(state="missing")
        return self.read(candidates[0].name)

    def read(self, run_id: str) -> AdminEvalSnapshot:
        """按受限 Run ID 读取 Candidate，拒绝路径逃逸和任意文件。"""

        from commerce_resolve.eval_runtime import (
            compare_with_baseline,
            read_baseline,
            read_run_report,
        )

        if RUN_ID_PATTERN.fullmatch(run_id) is None:
            return AdminEvalSnapshot(
                state="incompatible",
                compatibility_reasons=("run_id_invalid",),
            )
        run_path = self._safe_run_path(run_id)
        if run_path is None:
            return AdminEvalSnapshot(
                state="incompatible",
                candidate_run_id=run_id,
                compatibility_reasons=("run_path_outside_root",),
            )
        try:
            candidate = read_run_report(run_path)
        except (OSError, ValueError):
            return AdminEvalSnapshot(
                state="incompatible",
                candidate_run_id=run_id,
                compatibility_reasons=("candidate_unreadable",),
            )
        baseline = None
        if self._baseline_path.is_file():
            try:
                baseline = read_baseline(self._baseline_path)
            except (OSError, ValueError):
                return self._snapshot(
                    candidate,
                    state="incompatible",
                    reasons=("baseline_unreadable",),
                )
        if candidate.status != "passed" or candidate.safety_violations:
            return self._snapshot(candidate, state="failed")
        if baseline is None:
            return self._snapshot(
                candidate,
                state="incompatible",
                reasons=("baseline_missing",),
            )
        comparison = compare_with_baseline(candidate, baseline)
        if not comparison.compatible:
            return self._snapshot(
                candidate,
                state="incompatible",
                baseline_id=baseline.baseline_id,
                reasons=comparison.reasons,
            )
        if comparison.status != "passed":
            return self._snapshot(
                candidate,
                state="failed",
                baseline_id=baseline.baseline_id,
                reasons=comparison.reasons,
            )
        return self._snapshot(
            candidate,
            state="passed",
            baseline_id=baseline.baseline_id,
        )

    def _candidate_directories(self) -> list[Path]:
        """按完成时间近似的文件修改时间返回有效 Candidate 目录。"""

        if not self._run_root.is_dir():
            return []
        candidates = []
        for item in self._run_root.iterdir():
            safe_path = self._safe_run_path(item.name)
            if (
                safe_path is not None
                and safe_path.is_dir()
                and (safe_path / "results.json").is_file()
            ):
                candidates.append(safe_path)
        return sorted(
            candidates,
            key=lambda item: (item.stat().st_mtime_ns, item.name),
            reverse=True,
        )

    def _safe_run_path(self, run_id: str) -> Path | None:
        """解析固定根内的真实目录，并拒绝符号链接造成的路径逃逸。"""

        try:
            root = self._run_root.resolve()
            candidate = (root / run_id).resolve()
            candidate.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            return None
        return candidate

    def _snapshot(
        self,
        candidate,
        *,
        state: str,
        baseline_id: str | None = None,
        reasons: tuple[str, ...] = (),
    ) -> AdminEvalSnapshot:
        """把完整 Eval 报告缩减为不含场景正文和本机信息的摘要。"""

        return AdminEvalSnapshot(
            state=state,
            baseline_id=baseline_id,
            candidate_run_id=candidate.manifest.run_id,
            candidate_status=candidate.status,
            application_version=candidate.manifest.application_version,
            profile_version=candidate.manifest.profile_version,
            completed_at=candidate.manifest.completed_at,
            safety_violation_count=len(candidate.safety_violations),
            compatibility_reasons=reasons,
            suites=tuple(
                AdminEvalSuite(
                    suite_id=suite.suite_id,
                    suite_version=suite.suite_version,
                    passed_scenarios=suite.passed_scenarios,
                    total_scenarios=suite.total_scenarios,
                    passed=suite.passed,
                    safety_violation_count=len(suite.safety_violations),
                )
                for suite in candidate.suites
            ),
        )


def build_system_snapshot(
    settings: DeploymentSettings,
    release: ReleaseManifest,
) -> AdminSystemSnapshot:
    """读取健康与有限存储存在性，不公开路径、连接串或配置值。"""

    ready, error_code, capabilities = readiness_state(settings, release)
    web = settings.web
    return AdminSystemSnapshot(
        version=release.app_version,
        migration_head=release.business_schema_head,
        live=True,
        ready=ready,
        ready_error_code=error_code,
        capabilities=capabilities.model_dump(mode="json"),
        storage={
            "business": "available"
            if web.business_db_path.is_file()
            else "unavailable",
            "checkpoint": (
                "available" if web.checkpoint_db_path.is_file() else "unavailable"
            ),
            "memory": "available" if web.memory_db_path.is_file() else "unavailable",
            "policy_index": (
                "available" if web.policy_index_db_path.is_file() else "unavailable"
            ),
        },
    )
