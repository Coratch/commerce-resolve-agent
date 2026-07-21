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
            "all",
        ),
        default="all",
        help="eval suite; defaults to all",
    )
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

    from commerce_resolve.web import create_app
    from commerce_resolve.web.settings import WebSettings

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

    load_dotenv(dotenv_path=DEFAULT_ENV_FILE, override=False)
    args = build_parser().parse_args(argv)
    business_database = Path(business_db_path or DEFAULT_BUSINESS_DB)
    memory_database = Path(memory_db_path or DEFAULT_MEMORY_DB)
    if args.command == "db":
        return _run_database_upgrade(business_database)
    if args.command == "memory":
        return _run_memory_setup(memory_database)
    if args.command == "invite":
        return _run_invitation_command(args, business_database)
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
        if args.suite == "v0.1":
            report = run_eval_suite()
            print(report.model_dump_json(indent=2))
            return 0 if report.passed else 1
        if args.suite == "v0.2":
            policy_report = run_policy_eval_suite()
            print(policy_report.model_dump_json(indent=2))
            return 0 if policy_report.passed else 1
        if args.suite == "v0.3":
            from commerce_resolve.web_evaluation import run_v03_eval_suite

            web_report = run_v03_eval_suite()
            print(web_report.model_dump_json(indent=2))
            return 0 if web_report.passed else 1
        if args.suite == "v0.4":
            from commerce_resolve.refund_evaluation import run_refund_eval_suite

            refund_report = run_refund_eval_suite()
            print(refund_report.model_dump_json(indent=2))
            return 0 if refund_report.passed else 1
        if args.suite == "v0.5":
            l2_report = run_l2_eval_suite()
            print(l2_report.model_dump_json(indent=2))
            return 0 if l2_report.passed else 1
        if args.suite == "v0.6":
            from commerce_resolve.conversation_evaluation import (
                run_conversation_eval_suite,
            )

            conversation_report = run_conversation_eval_suite()
            print(conversation_report.model_dump_json(indent=2))
            return 0 if conversation_report.passed else 1
        if args.suite == "v0.7":
            context_report = run_context_eval_suite()
            print(context_report.model_dump_json(indent=2))
            return 0 if context_report.passed else 1
        report = run_eval_suite()
        policy_report = run_policy_eval_suite()
        from commerce_resolve.conversation_evaluation import (
            run_conversation_eval_suite,
        )
        from commerce_resolve.refund_evaluation import run_refund_eval_suite
        from commerce_resolve.web_evaluation import run_v03_eval_suite

        web_report = run_v03_eval_suite()
        refund_report = run_refund_eval_suite()
        l2_report = run_l2_eval_suite()
        conversation_report = run_conversation_eval_suite()
        context_report = run_context_eval_suite()
        combined = {
            "suite": "all",
            "passed": (
                report.passed
                and policy_report.passed
                and web_report.passed
                and refund_report.passed
                and l2_report.passed
                and conversation_report.passed
                and context_report.passed
            ),
            "reports": {
                "v0.1": report.model_dump(mode="json"),
                "v0.2": policy_report.model_dump(mode="json"),
                "v0.3": web_report.model_dump(mode="json"),
                "v0.4": refund_report.model_dump(mode="json"),
                "v0.5": l2_report.model_dump(mode="json"),
                "v0.6": conversation_report.model_dump(mode="json"),
                "v0.7": context_report.model_dump(mode="json"),
            },
        }
        print(json.dumps(combined, ensure_ascii=False, indent=2))
        return 0 if combined["passed"] else 1
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
