"""CommerceResolve 订单查询、政策 RAG、索引和 Eval 的命令行入口。"""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv

from commerce_resolve.adapters.fake import build_fake_dependencies
from commerce_resolve.adapters.sqlite_business import (
    SqliteBusinessRepository,
    assert_business_schema_current,
    create_business_engine,
    upgrade_business_database,
)
from commerce_resolve.adapters.sqlite_policy import (
    PolicyIndexBuildError,
    SqlitePolicyRepository,
    build_policy_index,
)
from commerce_resolve.checkpointing import open_sqlite_checkpointer
from commerce_resolve.context_evaluation import run_context_eval_suite
from commerce_resolve.evaluation import run_eval_suite
from commerce_resolve.gateways import (
    INTERPRETER_UNAVAILABLE_MESSAGE,
    Dependencies,
    InterpreterUnavailableError,
)
from commerce_resolve.l2_evaluation import run_l2_eval_suite
from commerce_resolve.l2_memory import setup_memory_store
from commerce_resolve.policy_evaluation import run_policy_eval_suite
from commerce_resolve.state import RunContext
from commerce_resolve.workflow import build_workflow

DEFAULT_CHECKPOINT_DB = Path("var/checkpoints.sqlite")
DEFAULT_POLICY_SOURCE = Path("data/policies")
DEFAULT_POLICY_INDEX_DB = Path("var/policy-index.sqlite")
DEFAULT_ENV_FILE = Path(".env")
DEFAULT_BUSINESS_DB = Path("var/business.sqlite")
DEFAULT_MEMORY_DB = Path("var/memory.sqlite")


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器及当前支持的子命令。"""

    parser = argparse.ArgumentParser(prog="commerce-resolve")
    subparsers = parser.add_subparsers(dest="command", required=True)
    from commerce_resolve.operations.cli import add_operations_parser

    add_operations_parser(subparsers)
    ask = subparsers.add_parser("ask", help="submit an order inquiry")
    ask.add_argument("message", help="customer message")
    ask.add_argument("--thread-id", required=True, help="conversation thread")
    ask.add_argument("--user-id", required=True, help="demo user identity")
    ask.add_argument(
        "--interpreter",
        choices=("fake", "openai"),
        default="fake",
        help="intent interpreter; defaults to deterministic fake",
    )
    evaluation = subparsers.add_parser(
        "eval",
        help="run deterministic release eval suites",
    )
    evaluation.add_argument(
        "--suite",
        choices=(
            "v0.1",
            "v0.2",
            "v0.3",
            "v0.4",
            "v0.5",
            "v0.6",
            "v0.7",
            "v0.8",
            "v1.0",
            "v1.1",
            "v1.2",
            "v1.3",
            "v1.3.1",
            "v1.3.2",
            "v2.0",
            "all",
        ),
        dest="legacy_eval_suite",
        default=None,
        help="eval suite; defaults to all",
    )
    eval_commands = evaluation.add_subparsers(dest="eval_command")
    eval_run = eval_commands.add_parser(
        "run",
        help="run versioned suites and write a reproducible artifact",
    )
    eval_run.add_argument(
        "--suite",
        action="append",
        choices=(
            "v0.1",
            "v0.2",
            "v0.3",
            "v0.4",
            "v0.5",
            "v0.6",
            "v0.7",
            "v0.8",
            "v1.0",
            "v1.1",
            "v1.2",
            "v1.3",
            "v1.3.1",
            "v1.3.2",
            "v2.0",
            "all",
        ),
        dest="eval_suites",
        help="repeat to select suites; defaults to all",
    )
    eval_run.add_argument(
        "--output-root",
        type=Path,
        default=Path("var/eval/runs"),
    )
    eval_run.add_argument("--run-id")
    eval_compare = eval_commands.add_parser(
        "compare",
        help="compare a candidate artifact with an accepted baseline",
    )
    eval_compare.add_argument("--candidate", type=Path, required=True)
    eval_compare.add_argument("--baseline", type=Path, required=True)
    eval_qualify = eval_commands.add_parser(
        "qualify",
        help="run the explicit real-provider qualification dataset",
    )
    eval_qualify.add_argument(
        "--dataset",
        type=Path,
        default=Path("data/eval/v2.0/provider-qualification.json"),
    )
    eval_qualify.add_argument("--repetitions", type=int, default=2)
    eval_qualify.add_argument(
        "--output-root",
        type=Path,
        default=Path("var/eval/runs"),
    )
    eval_release = eval_commands.add_parser(
        "release",
        help="run the complete fixed offline release gate",
    )
    eval_release.add_argument(
        "--output-root",
        type=Path,
        default=Path("var/eval/releases"),
    )
    eval_release.add_argument("--baseline", type=Path, required=True)
    eval_release.add_argument("--run-id")
    eval_baseline = eval_commands.add_parser(
        "baseline",
        help="manage explicitly accepted eval baselines",
    )
    eval_baseline_commands = eval_baseline.add_subparsers(
        dest="eval_baseline_command",
        required=True,
    )
    eval_baseline_accept = eval_baseline_commands.add_parser(
        "accept",
        help="accept a passing run artifact as a baseline",
    )
    eval_baseline_accept.add_argument("--run", type=Path, required=True)
    eval_baseline_accept.add_argument("--output", type=Path, required=True)
    eval_baseline_accept.add_argument("--reason", required=True)
    eval_baseline_accept.add_argument("--replace", action="store_true")
    policy_index = subparsers.add_parser(
        "policy-index",
        help="manage the derived policy search index",
    )
    policy_index_commands = policy_index.add_subparsers(
        dest="policy_index_command",
        required=True,
    )
    policy_index_commands.add_parser(
        "build",
        help="validate policy sources and rebuild the SQLite FTS5 index",
    )
    database = subparsers.add_parser("db", help="manage the business database")
    database_commands = database.add_subparsers(
        dest="database_command",
        required=True,
    )
    database_commands.add_parser(
        "upgrade",
        help="upgrade the business database to the current Alembic head",
    )
    memory = subparsers.add_parser("memory", help="manage the long-term memory store")
    memory_commands = memory.add_subparsers(
        dest="memory_command",
        required=True,
    )
    memory_commands.add_parser(
        "setup",
        help="create the independent LangGraph SQLite Store schema",
    )
    invitation = subparsers.add_parser(
        "invite",
        help="manage invitation codes through the trusted local CLI",
    )
    invitation_commands = invitation.add_subparsers(
        dest="invitation_command",
        required=True,
    )
    create_invitation = invitation_commands.add_parser(
        "create",
        help="create an expiring invitation code",
    )
    create_invitation.add_argument("--expires-in-hours", type=int, default=168)
    create_invitation.add_argument("--max-uses", type=int, default=1)
    revoke_invitation = invitation_commands.add_parser(
        "revoke",
        help="revoke an invitation by its public id",
    )
    revoke_invitation.add_argument("--invite-id", required=True)
    admin = subparsers.add_parser(
        "admin",
        help="manage the trusted local administrator role",
    )
    admin_commands = admin.add_subparsers(dest="admin_command", required=True)
    admin_grant = admin_commands.add_parser(
        "grant",
        help="grant administrator role to an existing account",
    )
    admin_grant.add_argument("username")
    admin_revoke = admin_commands.add_parser(
        "revoke",
        help="revoke administrator role from an existing account",
    )
    admin_revoke.add_argument("username")
    admin_commands.add_parser("list", help="list account roles without credentials")
    demo_catalog = subparsers.add_parser(
        "demo-catalog",
        help="validate or seed the versioned local demo catalog",
    )
    demo_catalog_commands = demo_catalog.add_subparsers(
        dest="demo_catalog_command",
        required=True,
    )
    validate_catalog = demo_catalog_commands.add_parser(
        "validate",
        help="validate products, scenarios and local asset hashes",
    )
    validate_catalog.add_argument("--version", default="v1.3")
    seed_catalog = demo_catalog_commands.add_parser(
        "seed",
        help="idempotently seed the commercial service scenario set",
    )
    seed_catalog.add_argument("--version", default="v1.3")
    seed_catalog.add_argument("--username", required=True)
    seed_catalog.add_argument(
        "--scenario-set",
        choices=("commercial-service",),
        default="commercial-service",
    )
    serve = subparsers.add_parser("serve", help="run the single-instance Web app")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    openapi = subparsers.add_parser(
        "openapi",
        help="export the internal Web API schema for the TypeScript client",
    )
    openapi_commands = openapi.add_subparsers(
        dest="openapi_command",
        required=True,
    )
    export_openapi = openapi_commands.add_parser("export")
    export_openapi.add_argument("--output", type=Path, default=Path("var/openapi.json"))
    l2_context = subparsers.add_parser(
        "l2-context",
        help="inspect redacted local L2 context diagnostics",
    )
    l2_context_commands = l2_context.add_subparsers(
        dest="l2_context_command",
        required=True,
    )
    inspect_context = l2_context_commands.add_parser(
        "inspect",
        help="read manifests, public trace and metrics without replaying the graph",
    )
    inspect_context.add_argument("--case-id", required=True)
    inspect_context.add_argument("--database", type=Path)
    return parser


def _open_business_repository(
    database: Path,
) -> tuple[SqliteBusinessRepository, object]:
    """打开已迁移业务库，并同时返回需要由调用方关闭的 Engine。"""

    engine = create_business_engine(database)
    try:
        assert_business_schema_current(engine, database)
    except Exception:
        engine.dispose()
        raise
    return SqliteBusinessRepository(engine), engine


def _run_database_upgrade(database: Path) -> int:
    """执行显式 Alembic 升级并输出不含本地绝对路径的结果。"""

    upgrade_business_database(database)
    print("业务数据库已升级到当前版本。")
    return 0


def _run_memory_setup(database: Path) -> int:
    """显式初始化独立长期记忆 Store，不写入任何用户偏好。"""

    setup_memory_store(database)
    print("长期记忆 Store 已初始化。")
    return 0


def _run_invitation_command(args: argparse.Namespace, database: Path) -> int:
    """在可信本地入口创建或撤销邀请码，并关闭数据库连接。"""

    try:
        repository, engine = _open_business_repository(database)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2
    try:
        if args.invitation_command == "create":
            invitation = repository.create_invitation(
                expires_in_hours=args.expires_in_hours,
                max_uses=args.max_uses,
            )
            print("邀请码已创建；明文只显示本次：")
            print(f"invite_id: {invitation.id}")
            print(f"code: {invitation.code}")
            print(f"expires_at: {invitation.expires_at.isoformat()}")
            print(f"max_uses: {invitation.max_uses}")
            return 0
        revoked = repository.revoke_invitation(args.invite_id)
        if not revoked:
            print("邀请码不存在。", file=sys.stderr)
            return 1
        print("邀请码已失效。")
        return 0
    finally:
        engine.dispose()


def _run_admin_command(args: argparse.Namespace, database: Path) -> int:
    """通过受控本机入口授予、撤销或查看账号角色。"""

    from commerce_resolve.adapters.sqlite_admin import (
        AdminDataError,
        SqliteAdminRepository,
    )

    try:
        repository, engine = _open_business_repository(database)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2
    admin_repository = SqliteAdminRepository(repository.engine)
    try:
        if args.admin_command == "list":
            payload = [
                {
                    "username": item.username,
                    "status": item.status,
                    "role": item.role,
                }
                for item in admin_repository.list_customers()
            ]
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        role = "admin" if args.admin_command == "grant" else "customer"
        account = admin_repository.set_role(args.username, role)
        print(
            json.dumps(
                {
                    "username": account.username,
                    "status": account.status,
                    "role": account.role,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except AdminDataError:
        print("账号不存在或不可用。", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


def _run_demo_catalog_command(
    args: argparse.Namespace,
    database: Path,
) -> int:
    """校验目录，或为显式账号幂等初始化全部商业化演示场景。"""

    from commerce_resolve.demo_catalog import DemoCatalogError, DemoCatalogService

    if args.demo_catalog_command == "validate":
        try:
            summary = DemoCatalogService(project_root=Path.cwd()).summary(args.version)
        except DemoCatalogError as error:
            print(f"目录校验失败：{error.error_code}", file=sys.stderr)
            return 1
        print(summary.model_dump_json(indent=2))
        return 0
    try:
        repository, engine = _open_business_repository(database)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2
    del repository
    try:
        service = DemoCatalogService(project_root=Path.cwd(), engine=engine)
        summary = service.summary(args.version)
        results = [
            service.seed_for_username(
                username=args.username,
                scenario_id=item.scenario_id,
                version=args.version,
            ).model_dump(mode="json")
            for item in summary.scenarios
        ]
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    except DemoCatalogError as error:
        print(f"目录初始化失败：{error.error_code}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


def _run_web_server(
    args: argparse.Namespace,
    *,
    business_database: Path,
    checkpoint_database: Path,
    policy_source: Path,
    policy_database: Path,
    memory_database: Path,
) -> int:
    """按环境配置启动单 worker FastAPI，不自动创建或迁移数据库。"""

    import uvicorn

    from commerce_resolve.operations.locking import (
        InstanceLock,
        InstanceLockUnavailable,
    )
    from commerce_resolve.operations.models import PreflightMode
    from commerce_resolve.operations.preflight import (
        report_json,
        resolve_release_manifest,
        run_preflight,
    )
    from commerce_resolve.structured_logging import configure_json_logging
    from commerce_resolve.web import create_app
    from commerce_resolve.web.settings import DeploymentSettings, WebSettings

    settings = WebSettings.from_env()
    settings = replace(
        settings,
        business_db_path=business_database,
        checkpoint_db_path=checkpoint_database,
        policy_source_path=policy_source,
        policy_index_db_path=policy_database,
        memory_db_path=memory_database,
        host=args.host or settings.host,
        port=args.port or settings.port,
    )
    deployment = DeploymentSettings.from_env(settings)
    if not deployment.deployment:
        try:
            app = create_app(settings=settings)
        except RuntimeError as error:
            print(str(error), file=sys.stderr)
            return 2
        uvicorn.run(
            app,
            host=settings.host,
            port=settings.port,
            workers=1,
        )
        return 0
    try:
        release = resolve_release_manifest(deployment, project_root=Path.cwd())
        with InstanceLock(deployment.instance_lock_path):
            report = run_preflight(
                deployment,
                PreflightMode.SERVE,
                project_root=Path.cwd(),
                lock_already_held=True,
            )
            if not report.passed:
                print(report_json(report), file=sys.stderr)
                return 3
            configure_json_logging(deployment.log_level)
            app = create_app(
                settings=settings,
                deployment_settings=deployment,
                release_manifest=release,
            )
            uvicorn.run(
                app,
                host=settings.host,
                port=settings.port,
                workers=1,
                timeout_graceful_shutdown=deployment.shutdown_grace_seconds,
                access_log=False,
                log_config=None,
            )
    except InstanceLockUnavailable:
        print('{"error_code":"instance_lock_held"}', file=sys.stderr)
        return 4
    except (RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


def _run_openapi_export(
    output: Path,
    *,
    business_database: Path,
    checkpoint_database: Path,
    policy_source: Path,
    policy_database: Path,
    memory_database: Path,
) -> int:
    """从真实 FastAPI 路由生成 OpenAPI JSON，供 TypeScript 类型同步。"""

    from commerce_resolve.web import create_app
    from commerce_resolve.web.settings import WebSettings

    settings = replace(
        WebSettings.from_env(),
        business_db_path=business_database,
        checkpoint_db_path=checkpoint_database,
        policy_source_path=policy_source,
        policy_index_db_path=policy_database,
        memory_db_path=memory_database,
    )
    try:
        app = create_app(settings=settings, mount_spa=False)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    app.state.services.dispose()
    print(f"OpenAPI 已导出：{output}")
    return 0


def _run_policy_index_build(source: Path, database: Path) -> int:
    """校验受控政策语料、原子构建索引并输出可审计摘要。"""

    try:
        summary = build_policy_index(source, database)
    except PolicyIndexBuildError as error:
        print(f"政策索引构建失败：{error}", file=sys.stderr)
        return 2
    print("政策索引构建成功：")
    print(f"corpus_version: {summary.corpus_version}")
    print(f"corpus_hash: {summary.corpus_hash}")
    print(f"documents: {summary.document_count}")
    print(f"sections: {summary.section_count}")
    print(f"facts: {summary.fact_count}")
    return 0


def _run_versioned_eval(args: argparse.Namespace) -> int:
    """执行统一离线 Eval，并把可复现 Artifact 写入忽略目录。"""

    from commerce_resolve.eval_runtime import (
        run_offline_evaluation,
        status_exit_code,
        write_run_artifact,
    )

    suites = tuple(args.eval_suites or ("all",))
    if "all" in suites and len(suites) != 1:
        print("all 不能与具体 Suite 同时使用。", file=sys.stderr)
        return 4
    try:
        report = run_offline_evaluation(
            Path.cwd(),
            suite_versions=suites,
            run_id=args.run_id,
        )
        run_dir = write_run_artifact(report, args.output_root)
    except (FileExistsError, OSError, ValueError) as error:
        print(f"Eval Run 失败：{error}", file=sys.stderr)
        return 4
    payload = {
        "run_id": report.manifest.run_id,
        "status": report.status,
        "result_fingerprint": report.result_fingerprint,
        "artifact": run_dir.as_posix(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return status_exit_code(report.status)


def _run_current_release_eval() -> int:
    """运行 v2.0 当前发布门禁，并显式列出不再阻断发布的历史 Suite。"""

    from commerce_resolve.eval_catalog import ARCHIVED_SUITE_VERSIONS
    from commerce_resolve.eval_runtime import (
        run_offline_evaluation,
        status_exit_code,
    )

    report = run_offline_evaluation(Path.cwd(), suite_versions=("all",))
    payload = {
        "suite": "all",
        "profile_version": report.manifest.profile_version,
        "passed": report.status == "passed",
        "reports": {
            suite.suite_version: {
                "suite_id": suite.suite_id,
                "total_scenarios": suite.total_scenarios,
                "passed_scenarios": suite.passed_scenarios,
                "passed": suite.passed,
                "safety_violations": len(suite.safety_violations),
            }
            for suite in report.suites
        },
        "archived_suites": list(ARCHIVED_SUITE_VERSIONS),
        "aggregate_metrics": report.aggregate_metrics,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return status_exit_code(report.status)


def _run_eval_compare(args: argparse.Namespace) -> int:
    """读取 Candidate 与 Baseline，输出不修改两者的比较结果。"""

    from commerce_resolve.eval_runtime import (
        compare_with_baseline,
        read_baseline,
        read_run_report,
        status_exit_code,
    )

    try:
        candidate = read_run_report(args.candidate)
        baseline = read_baseline(args.baseline)
        comparison = compare_with_baseline(candidate, baseline)
    except (OSError, ValueError) as error:
        print(f"Eval 比较失败：{error}", file=sys.stderr)
        return 4
    print(comparison.model_dump_json(indent=2))
    return status_exit_code(comparison.status)


def _run_eval_baseline_accept(args: argparse.Namespace) -> int:
    """显式接受通过的 Run，并默认拒绝覆盖已有 Baseline。"""

    from commerce_resolve.eval_runtime import accept_baseline, read_run_report

    try:
        report = read_run_report(args.run)
        baseline = accept_baseline(
            report,
            args.output,
            reason=args.reason,
            replace=args.replace,
        )
    except (FileExistsError, OSError, ValueError) as error:
        print(f"Baseline 接受失败：{error}", file=sys.stderr)
        return 4
    print(
        json.dumps(
            {
                "baseline_id": baseline.baseline_id,
                "output": args.output.as_posix(),
                "supersedes_baseline_id": baseline.supersedes_baseline_id,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _run_provider_qualify(args: argparse.Namespace) -> int:
    """显式调用现有真实模型 Adapter 完成两次合成资格运行。"""

    import os

    try:
        from commerce_resolve.adapters.openai_interpreter import (
            OpenAIQueryInterpreter,
        )
        from commerce_resolve.adapters.openai_l2_agent import OpenAIL2Agent
        from commerce_resolve.eval_runtime import status_exit_code
        from commerce_resolve.provider_evaluation import (
            load_provider_dataset,
            run_provider_qualification,
            write_provider_artifact,
        )

        dataset = load_provider_dataset(args.dataset)
        report = run_provider_qualification(
            dataset,
            interpreter=OpenAIQueryInterpreter.from_env(),
            l2_provider=OpenAIL2Agent.from_env(),
            model_name=os.getenv("LLM_MODEL", "").strip(),
            repetitions=args.repetitions,
        )
        run_dir = write_provider_artifact(report, args.output_root)
    except (FileExistsError, ModuleNotFoundError, OSError, ValueError) as error:
        print(f"Provider 资格运行失败：{error}", file=sys.stderr)
        return 4
    print(
        json.dumps(
            {
                "run_id": report.run_id,
                "status": report.status,
                "tasks": f"{report.task_passed}/{report.task_total}",
                "safety_violations": len(report.safety_violations),
                "artifact": run_dir.as_posix(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return status_exit_code(report.status)


def _run_eval_release(args: argparse.Namespace) -> int:
    """执行固定离线发布门禁，不接受用户提供任意命令。"""

    from commerce_resolve.eval_release import run_release_gate
    from commerce_resolve.eval_runtime import status_exit_code

    try:
        report, release_dir = run_release_gate(
            Path.cwd(),
            args.output_root,
            run_id=args.run_id,
            baseline_path=args.baseline,
        )
    except (FileExistsError, OSError, RuntimeError, ValueError) as error:
        print(f"Release Gate 启动失败：{error}", file=sys.stderr)
        return 4
    print(
        json.dumps(
            {
                "run_id": report.run_id,
                "status": report.status,
                "checks": f"{report.passed_checks}/{report.required_checks}",
                "artifact": release_dir.as_posix(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return status_exit_code(report.status)


def _diagnostic_manifest(manifest: object) -> dict[str, object]:
    """把 Manifest 投影为不含正文、身份摘要和 Pack hash 的诊断 JSON。"""

    from commerce_resolve.l2_models import L2ContextManifest

    validated = L2ContextManifest.model_validate(manifest)
    return {
        "manifest_id": validated.manifest_id,
        "schema_version": validated.schema_version,
        "case_id": validated.case_id,
        "step_id": validated.step_id,
        "context_policy_version": validated.context_policy_version,
        "essential_complete": validated.essential_complete,
        "counts": {
            "candidate": validated.candidate_count,
            "selected": validated.selected_count,
            "duplicate": validated.duplicate_count,
            "irrelevant": validated.irrelevant_count,
            "stale": validated.stale_count,
            "conflict": validated.conflict_count,
            "out_of_scope": validated.out_of_scope_count,
            "truncated": validated.truncated_count,
            "refreshed": validated.refresh_count,
        },
        "tokens": {
            "candidate_estimated": validated.candidate_estimated_tokens,
            "selected_estimated": validated.selected_estimated_tokens,
            "pack_estimated_input": validated.pack_estimated_input_tokens,
            "input_budget": validated.input_budget_tokens,
            "reduction_basis_points": validated.reduction_basis_points,
        },
        "failure_attribution": validated.failure_attribution,
        "public_summary": validated.public_summary.model_dump(mode="json"),
        "items": [item.model_dump(mode="json") for item in validated.items],
        "context_preparation_ms": validated.context_preparation_ms,
        "created_at": validated.created_at.isoformat(),
    }


def _run_l2_context_inspect(case_id: str, database: Path) -> int:
    """只读输出本地 Case 的脱敏 Manifest、公开 Trace 和聚合指标。"""

    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker

    from commerce_resolve.adapters.sqlalchemy_models import L2SupportCaseRow
    from commerce_resolve.adapters.sqlite_l2 import SqliteL2CaseRepository

    try:
        repository, engine = _open_business_repository(database)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2
    del repository
    l2_repository = SqliteL2CaseRepository(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    try:
        with sessions() as session:
            row = session.scalar(
                select(L2SupportCaseRow).where(L2SupportCaseRow.case_id == case_id)
            )
        if row is None:
            print("L2 Case 不存在。", file=sys.stderr)
            return 1
        case = l2_repository.get_authorized_case(
            case_id=case_id,
            subject_id=row.subject_id,
            user_id=row.user_id,
            workspace_id=row.workspace_id,
            thread_id=row.thread_id,
        )
        if case is None:
            print("L2 Case 不可读取。", file=sys.stderr)
            return 1
        manifests = l2_repository.list_manifests(
            case_id=case_id,
            user_id=row.user_id,
            workspace_id=row.workspace_id,
        )
        events = l2_repository.list_events(
            case_id=case_id,
            user_id=row.user_id,
            workspace_id=row.workspace_id,
            limit=200,
        )
        metrics = l2_repository.get_case_metrics(
            case_id=case_id,
            user_id=row.user_id,
            workspace_id=row.workspace_id,
        )
        payload = {
            "case": {
                "case_id": case.case_id,
                "status": case.status,
                "trace_state": case.trace_state,
                "context_policy_version": case.context_policy_version,
                "failure_attribution": case.failure_attribution,
            },
            "metrics": metrics.model_dump(mode="json") if metrics else None,
            "events": [event.model_dump(mode="json") for event in events],
            "manifests": [_diagnostic_manifest(item) for item in manifests],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        engine.dispose()


def _build_default_dependencies(
    interpreter_name: str,
    policy_repository: SqlitePolicyRepository,
) -> Dependencies:
    """装配演示业务与政策 Gateway，并按显式选项选择意图解释器。"""

    dependencies = build_fake_dependencies(policy_repository=policy_repository)
    if interpreter_name == "fake":
        return dependencies
    try:
        from commerce_resolve.adapters.openai_interpreter import (
            OpenAIQueryInterpreter,
        )
    except ModuleNotFoundError as error:
        if error.name != "openai":
            raise
        raise ValueError(
            "未安装 OpenAI 可选依赖，请执行：python -m pip install -e '.[openai]'"
        ) from None
    return Dependencies(
        interpreter=OpenAIQueryInterpreter.from_env(),
        order_gateway=dependencies.order_gateway,
        logistics_gateway=dependencies.logistics_gateway,
        policy_repository=policy_repository,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    dependencies: Dependencies | None = None,
    checkpoint_path: str | Path | None = None,
    business_db_path: str | Path | None = None,
    policy_source_path: str | Path | None = None,
    policy_index_path: str | Path | None = None,
    memory_db_path: str | Path | None = None,
) -> int:
    """执行查询、政策索引构建或确定性离线 Eval。

    启动时从当前工作目录的 ``.env`` 加载尚未存在的环境变量。
    ``ask`` 将会话状态持久化到 SQLite；``eval`` 使用固定 Fake 场景输出 JSON 报告。
    ``dependencies``、Checkpoint 和政策路径参数用于测试时注入确定性依赖与临时
    文件；注入依赖优先于 CLI 的解释器选项，未提供路径时使用当前工作目录下的
    默认运行时文件。
    """

    args = build_parser().parse_args(argv)
    if args.command != "eval" or args.eval_command == "qualify":
        load_dotenv(dotenv_path=DEFAULT_ENV_FILE, override=False)
    business_database = Path(business_db_path or DEFAULT_BUSINESS_DB)
    memory_database = Path(memory_db_path or DEFAULT_MEMORY_DB)
    if args.command == "ops":
        from commerce_resolve.operations.cli import run_operations_command
        from commerce_resolve.web.settings import DeploymentSettings, WebSettings

        web_settings = replace(
            WebSettings.from_env(),
            business_db_path=business_database,
            checkpoint_db_path=Path(checkpoint_path or DEFAULT_CHECKPOINT_DB),
            policy_source_path=Path(policy_source_path or DEFAULT_POLICY_SOURCE),
            policy_index_db_path=Path(policy_index_path or DEFAULT_POLICY_INDEX_DB),
            memory_db_path=memory_database,
        )
        return run_operations_command(
            args,
            DeploymentSettings.from_env(web_settings),
            project_root=Path.cwd(),
        )
    if args.command == "db":
        return _run_database_upgrade(business_database)
    if args.command == "memory":
        return _run_memory_setup(memory_database)
    if args.command == "invite":
        return _run_invitation_command(args, business_database)
    if args.command == "admin":
        return _run_admin_command(args, business_database)
    if args.command == "demo-catalog":
        return _run_demo_catalog_command(args, business_database)
    if args.command == "serve":
        return _run_web_server(
            args,
            business_database=business_database,
            checkpoint_database=Path(checkpoint_path or DEFAULT_CHECKPOINT_DB),
            policy_source=Path(policy_source_path or DEFAULT_POLICY_SOURCE),
            policy_database=Path(policy_index_path or DEFAULT_POLICY_INDEX_DB),
            memory_database=memory_database,
        )
    if args.command == "openapi":
        return _run_openapi_export(
            args.output,
            business_database=business_database,
            checkpoint_database=Path(checkpoint_path or DEFAULT_CHECKPOINT_DB),
            policy_source=Path(policy_source_path or DEFAULT_POLICY_SOURCE),
            policy_database=Path(policy_index_path or DEFAULT_POLICY_INDEX_DB),
            memory_database=memory_database,
        )
    if args.command == "l2-context":
        return _run_l2_context_inspect(
            args.case_id,
            args.database or business_database,
        )
    if args.command == "policy-index":
        return _run_policy_index_build(
            Path(policy_source_path or DEFAULT_POLICY_SOURCE),
            Path(policy_index_path or DEFAULT_POLICY_INDEX_DB),
        )
    if args.command == "eval":
        if args.eval_command is not None and args.legacy_eval_suite is not None:
            print(
                "旧 --suite 不能与 Eval 子命令混用；请把 --suite 放在 run 之后。",
                file=sys.stderr,
            )
            return 4
        if args.eval_command == "run":
            return _run_versioned_eval(args)
        if args.eval_command == "compare":
            return _run_eval_compare(args)
        if args.eval_command == "qualify":
            return _run_provider_qualify(args)
        if args.eval_command == "release":
            return _run_eval_release(args)
        if args.eval_command == "baseline":
            return _run_eval_baseline_accept(args)
        legacy_suite = args.legacy_eval_suite or "all"
        if legacy_suite == "v0.1":
            report = run_eval_suite()
            print(report.model_dump_json(indent=2))
            return 0 if report.passed else 1
        if legacy_suite == "v0.2":
            policy_report = run_policy_eval_suite()
            print(policy_report.model_dump_json(indent=2))
            return 0 if policy_report.passed else 1
        if legacy_suite == "v0.3":
            from commerce_resolve.web_evaluation import run_v03_eval_suite

            web_report = run_v03_eval_suite()
            print(web_report.model_dump_json(indent=2))
            return 0 if web_report.passed else 1
        if legacy_suite == "v0.4":
            from commerce_resolve.refund_evaluation import run_refund_eval_suite

            refund_report = run_refund_eval_suite()
            print(refund_report.model_dump_json(indent=2))
            return 0 if refund_report.passed else 1
        if legacy_suite == "v0.5":
            l2_report = run_l2_eval_suite()
            print(l2_report.model_dump_json(indent=2))
            return 0 if l2_report.passed else 1
        if legacy_suite == "v0.6":
            from commerce_resolve.conversation_evaluation import (
                run_conversation_eval_suite,
            )

            conversation_report = run_conversation_eval_suite()
            print(conversation_report.model_dump_json(indent=2))
            return 0 if conversation_report.passed else 1
        if legacy_suite == "v0.7":
            context_report = run_context_eval_suite()
            print(context_report.model_dump_json(indent=2))
            return 0 if context_report.passed else 1
        if legacy_suite == "v0.8":
            from commerce_resolve.eval_system_evaluation import (
                run_eval_system_suite,
            )

            eval_system_report = run_eval_system_suite()
            print(eval_system_report.model_dump_json(indent=2))
            return 0 if eval_system_report.passed else 1
        if legacy_suite == "v1.0":
            from commerce_resolve.operations_evaluation import (
                run_operations_eval_suite,
            )

            operations_report = run_operations_eval_suite()
            print(operations_report.model_dump_json(indent=2))
            return 0 if operations_report.passed else 1
        if legacy_suite == "v1.1":
            from commerce_resolve.service_center_evaluation import (
                run_service_center_eval_suite,
            )

            service_center_report = run_service_center_eval_suite()
            print(service_center_report.model_dump_json(indent=2))
            return 0 if service_center_report.passed else 1
        if legacy_suite == "v1.2":
            from commerce_resolve.admin_evaluation import (
                run_admin_surface_eval_suite,
            )

            admin_surface_report = run_admin_surface_eval_suite()
            print(admin_surface_report.model_dump_json(indent=2))
            return 0 if admin_surface_report.passed else 1
        if legacy_suite == "v1.3":
            from commerce_resolve.commercial_experience_evaluation import (
                run_commercial_experience_eval_suite,
            )

            commercial_report = run_commercial_experience_eval_suite()
            print(commercial_report.model_dump_json(indent=2))
            return 0 if commercial_report.passed else 1
        if legacy_suite == "v1.3.1":
            from commerce_resolve.commercial_credibility_evaluation import (
                run_commercial_credibility_eval_suite,
            )

            credibility_report = run_commercial_credibility_eval_suite()
            print(credibility_report.model_dump_json(indent=2))
            return 0 if credibility_report.passed else 1
        if legacy_suite == "v1.3.2":
            from commerce_resolve.immersive_interface_evaluation import (
                run_immersive_interface_eval_suite,
            )

            immersive_report = run_immersive_interface_eval_suite()
            print(immersive_report.model_dump_json(indent=2))
            return 0 if immersive_report.passed else 1
        if legacy_suite == "v2.0":
            from commerce_resolve.v20_product_evaluation import (
                run_v20_product_eval_suite,
            )

            product_report = run_v20_product_eval_suite()
            print(product_report.model_dump_json(indent=2))
            return 0 if product_report.passed else 1
        return _run_current_release_eval()
    database = Path(checkpoint_path or DEFAULT_CHECKPOINT_DB)
    database.parent.mkdir(parents=True, exist_ok=True)
    try:
        selected_dependencies = dependencies or _build_default_dependencies(
            args.interpreter,
            SqlitePolicyRepository(
                Path(policy_index_path or DEFAULT_POLICY_INDEX_DB),
                source_root=Path(policy_source_path or DEFAULT_POLICY_SOURCE),
            ),
        )
    except ValueError as error:
        print(f"{error}", file=sys.stderr)
        return 2
    with open_sqlite_checkpointer(database) as checkpointer:
        graph = build_workflow(
            selected_dependencies,
            checkpointer=checkpointer,
        )
        try:
            result = graph.invoke(
                {"messages": [{"role": "user", "content": args.message}]},
                config={"configurable": {"thread_id": args.thread_id}},
                context=RunContext(user_id=args.user_id),
            )
        except InterpreterUnavailableError:
            print(INTERPRETER_UNAVAILABLE_MESSAGE, file=sys.stderr)
            return 2
        except (LookupError, ValueError) as error:
            print(f"{error}", file=sys.stderr)
            return 2

    print(result["messages"][-1].content)
    return 0
