"""实现固定、可脚本化且不接受任意命令的本机运维 CLI。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from commerce_resolve.web.settings import DeploymentSettings

from .audit import append_operation_audit
from .backup import create_backup, restore_backup, verify_backup
from .diagnostics import diagnostic_payload
from .lifecycle import initialize_instance, reconcile_unfinished_runs
from .locking import InstanceLockUnavailable
from .manifest import load_instance_manifest
from .models import OperationExitCode, PreflightMode
from .preflight import report_json, resolve_release_manifest, run_preflight
from .upgrade import upgrade_from_v08


def add_operations_parser(subparsers: argparse._SubParsersAction) -> None:
    """向主 CLI 注册固定运维子命令和受限参数。"""

    operations = subparsers.add_parser("ops", help="manage the local deployment")
    commands = operations.add_subparsers(dest="ops_command", required=True)
    commands.add_parser("manifest", help="print the immutable release manifest")
    preflight = commands.add_parser("preflight", help="run deployment checks")
    preflight.add_argument("--mode", choices=tuple(PreflightMode), required=True)
    commands.add_parser("init", help="initialize an empty local instance")
    status = commands.add_parser("status", help="read local component status")
    status.add_argument("--format", choices=("text", "json"), default="text")
    backup = commands.add_parser("backup", help="create or verify backup sets")
    backup_commands = backup.add_subparsers(dest="backup_command", required=True)
    backup_create = backup_commands.add_parser("create")
    backup_create.add_argument("--output", type=Path)
    backup_verify = backup_commands.add_parser("verify")
    backup_verify.add_argument("--backup", type=Path, required=True)
    restore = commands.add_parser("restore", help="restore a verified backup set")
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--replace", action="store_true")
    restore.add_argument("--confirm-instance-id")
    upgrade = commands.add_parser("upgrade", help="upgrade a stopped v0.8 instance")
    upgrade.add_argument(
        "--from", dest="source_version", choices=("v0.8",), required=True
    )
    commands.add_parser("reconcile", help="interrupt orphaned active runs")


def _audit(
    settings: DeploymentSettings,
    operation: str,
    status: str,
    *,
    error_code: str | None = None,
    details: dict[str, object] | None = None,
) -> None:
    """用统一保留上限记录一条脱敏运维结果。"""

    append_operation_audit(
        settings.operations_audit_path,
        operation=operation,
        status=status,
        error_code=error_code,
        details=details,
        max_bytes=settings.operations_audit_max_bytes,
    )


def _print_status_text(payload: dict[str, object]) -> None:
    """以稳定键值形式打印适合人工阅读的有限状态。"""

    for key in (
        "alive",
        "ready",
        "release_version",
        "release_commit",
        "instance_id",
        "lock_held",
    ):
        print(f"{key}: {payload.get(key)}")
    print(f"capabilities: {json.dumps(payload['capabilities'], ensure_ascii=False)}")
    print(f"counts: {json.dumps(payload['counts'], ensure_ascii=False)}")
    print(f"failure_codes: {json.dumps(payload['failure_codes'], ensure_ascii=False)}")


def _preflight_or_exit(
    settings: DeploymentSettings,
    mode: PreflightMode,
    *,
    project_root: Path,
) -> int | None:
    """在高影响运维动作前运行对应预检，失败时打印完整结构化报告。"""

    report = run_preflight(settings, mode, project_root=project_root)
    if report.passed:
        return None
    print(report_json(report), file=sys.stderr)
    return OperationExitCode.PREFLIGHT_FAILED


def run_operations_command(
    args: argparse.Namespace,
    settings: DeploymentSettings,
    *,
    project_root: Path,
) -> int:
    """分发固定运维命令，并把领域失败映射为稳定退出码。"""

    try:
        release = resolve_release_manifest(settings, project_root=project_root)
    except ValueError as error:
        print(
            json.dumps({"error_code": str(error)}, ensure_ascii=False), file=sys.stderr
        )
        return OperationExitCode.INVALID_CONFIGURATION
    operation = f"ops.{args.ops_command}"
    try:
        if args.ops_command == "manifest":
            print(release.model_dump_json(indent=2))
            return OperationExitCode.SUCCESS
        if args.ops_command == "preflight":
            report = run_preflight(
                settings,
                PreflightMode(args.mode),
                project_root=project_root,
            )
            print(report_json(report))
            return (
                OperationExitCode.SUCCESS
                if report.passed
                else OperationExitCode.PREFLIGHT_FAILED
            )
        if args.ops_command == "init":
            if (
                exit_code := _preflight_or_exit(
                    settings,
                    PreflightMode.INIT,
                    project_root=project_root,
                )
            ) is not None:
                return exit_code
            result, manifest = initialize_instance(settings, release)
            _audit(settings, operation, "succeeded", details={"result": result})
            print(
                json.dumps(
                    {"status": result, "instance_id": manifest.instance_id},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return OperationExitCode.SUCCESS
        if args.ops_command == "status":
            report = run_preflight(
                settings,
                PreflightMode.STATUS,
                project_root=project_root,
            )
            payload = diagnostic_payload(settings, release)
            payload["ready"] = bool(payload["ready"] and report.passed)
            payload["checks"] = [item.model_dump(mode="json") for item in report.checks]
            payload["failure_codes"] = sorted(
                {
                    *payload["failure_codes"],
                    *(
                        item.error_code
                        for item in report.checks
                        if item.error_code is not None
                    ),
                }
            )
            if args.format == "json":
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                _print_status_text(payload)
            return (
                OperationExitCode.SUCCESS
                if payload["ready"]
                else OperationExitCode.PREFLIGHT_FAILED
            )
        if args.ops_command == "backup" and args.backup_command == "create":
            if (
                exit_code := _preflight_or_exit(
                    settings,
                    PreflightMode.BACKUP,
                    project_root=project_root,
                )
            ) is not None:
                return exit_code
            backup = create_backup(settings, release, output_root=args.output)
            _audit(
                settings,
                "ops.backup.create",
                "succeeded",
                details={"backup_id": backup.name},
            )
            print(json.dumps({"backup_id": backup.name}, ensure_ascii=False))
            return OperationExitCode.SUCCESS
        if args.ops_command == "backup":
            manifest = verify_backup(args.backup, allowed_root=settings.backup_root)
            print(manifest.model_dump_json(indent=2))
            return OperationExitCode.SUCCESS
        if args.ops_command == "restore":
            if (
                exit_code := _preflight_or_exit(
                    settings,
                    PreflightMode.RESTORE,
                    project_root=project_root,
                )
            ) is not None:
                return exit_code
            manifest, rollback = restore_backup(
                settings,
                release,
                backup=args.backup,
                replace=args.replace,
                confirm_instance_id=args.confirm_instance_id,
            )
            _audit(
                settings,
                operation,
                "succeeded",
                details={
                    "backup_id": manifest.backup_id,
                    "rollback_backup_id": rollback.name if rollback else None,
                },
            )
            print(
                json.dumps(
                    {
                        "restored_from": manifest.backup_id,
                        "rollback_backup_id": rollback.name if rollback else None,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return OperationExitCode.SUCCESS
        if args.ops_command == "upgrade":
            if (
                exit_code := _preflight_or_exit(
                    settings,
                    PreflightMode.UPGRADE,
                    project_root=project_root,
                )
            ) is not None:
                return exit_code
            upgraded, backup = upgrade_from_v08(settings, release)
            _audit(
                settings,
                operation,
                "succeeded",
                details={"pre_upgrade_backup_id": backup.name},
            )
            print(
                json.dumps(
                    {
                        "status": "upgraded",
                        "version": upgraded.last_successful_release,
                        "pre_upgrade_backup_id": backup.name,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return OperationExitCode.SUCCESS
        if args.ops_command == "reconcile":
            load_instance_manifest(settings.instance_manifest_path)
            report = reconcile_unfinished_runs(settings.web.business_db_path)
            _audit(
                settings,
                operation,
                "succeeded",
                details=report.model_dump(mode="json"),
            )
            print(report.model_dump_json(indent=2))
            return OperationExitCode.SUCCESS
        raise ValueError("unsupported_operation")
    except InstanceLockUnavailable:
        _audit(settings, operation, "failed", error_code="instance_lock_held")
        print('{"error_code":"instance_lock_held"}', file=sys.stderr)
        return OperationExitCode.INSTANCE_LOCKED
    except (json.JSONDecodeError, OSError, ValueError, RuntimeError) as error:
        error_code = str(error) or "operation_failed"
        _audit(settings, operation, "failed", error_code=error_code)
        print(
            json.dumps({"error_code": error_code}, ensure_ascii=False), file=sys.stderr
        )
        if args.ops_command == "backup":
            return OperationExitCode.INVALID_BACKUP
        if args.ops_command == "restore":
            return OperationExitCode.RESTORE_REJECTED
        if args.ops_command == "upgrade":
            return OperationExitCode.UPGRADE_FAILED
        return OperationExitCode.PREFLIGHT_FAILED
