"""验证 CommerceResolve CLI 的端到端行为。"""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

from sqlalchemy import inspect

from commerce_resolve.adapters.fake import (
    FakeLogisticsGateway,
    FakeOrderGateway,
    FakeQueryInterpreter,
)
from commerce_resolve.adapters.openai_interpreter import OpenAIQueryInterpreter
from commerce_resolve.adapters.sqlite_business import (
    SqliteBusinessRepository,
    create_business_engine,
    upgrade_business_database,
)
from commerce_resolve.adapters.sqlite_l2 import SqliteL2CaseRepository
from commerce_resolve.adapters.sqlite_policy import build_policy_index
from commerce_resolve.cli import DEFAULT_ENV_FILE, main
from commerce_resolve.gateways import Dependencies, InterpreterUnavailableError
from commerce_resolve.l2_context import CONTEXT_POLICY_VERSION, build_l2_context
from commerce_resolve.l2_models import (
    L2BudgetLimits,
    L2CaseCreate,
    L2RuntimeState,
)


def test_cli_runs_the_v0_1_eval_suite(capsys) -> None:
    """验证 CLI 可以输出通过的 v0.1 Eval JSON 报告。"""

    exit_code = main(["eval", "--suite", "v0.1"])

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert report["total_scenarios"] == 15
    assert report["passed_scenarios"] == 15
    assert report["passed"] is True


def test_cli_runs_the_v0_2_policy_eval_suite(capsys) -> None:
    """验证 CLI 可以单独运行二十个政策 RAG 发布场景。"""

    exit_code = main(["eval", "--suite", "v0.2"])

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert report["total_scenarios"] == 20
    assert report["passed_scenarios"] == 20
    assert report["passed"] is True


def test_cli_defaults_to_all_eval_suites(capsys) -> None:
    """验证不指定 suite 时保留五个版本报告和总门禁。"""

    exit_code = main(["eval"])

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert report["suite"] == "all"
    assert report["passed"] is True
    assert report["reports"]["v0.1"]["total_scenarios"] == 15
    assert report["reports"]["v0.2"]["total_scenarios"] == 20
    assert report["reports"]["v0.3"]["total_scenarios"] == 20
    assert report["reports"]["v0.4"]["total_scenarios"] == 24
    assert report["reports"]["v0.5"]["total_scenarios"] == 30
    assert report["reports"]["v0.6"]["total_scenarios"] == 32
    assert report["reports"]["v0.7"]["total_scenarios"] == 36
    assert report["reports"]["v0.8"]["total_scenarios"] == 40


def test_cli_runs_the_v0_3_web_eval_suite(capsys) -> None:
    """验证 CLI 可以单独运行二十个 Web 身份与隔离场景。"""

    exit_code = main(["eval", "--suite", "v0.3"])

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 0
    assert report["total_scenarios"] == 20
    assert report["passed_scenarios"] == 20
    assert report["guest_llm_calls"] == 0
    assert report["cross_user_leaks"] == 0


def test_cli_runs_the_v0_4_refund_eval_suite(capsys) -> None:
    """验证 CLI 可以单独运行二十四个 Mock 退款发布场景。"""

    exit_code = main(["eval", "--suite", "v0.4"])

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 0
    assert report["total_scenarios"] == 24
    assert report["passed_scenarios"] == 24
    assert report["unauthorized_refund_writes"] == 0
    assert report["duplicate_refund_writes"] == 0
    assert report["safety_violations"] == 0


def test_cli_runs_the_v0_5_l2_eval_suite(capsys) -> None:
    """验证 CLI 可以单独运行三十个二线客服 Harness 场景。"""

    exit_code = main(["eval", "--suite", "v0.5"])

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert report["total_scenarios"] == 30
    assert report["passed_scenarios"] == 30
    assert report["safety_violations"] == 0
    assert report["passed"] is True


def test_cli_runs_the_v0_7_context_eval_suite(capsys) -> None:
    """验证 CLI 可以单独运行三十六个上下文与 Trace 场景。"""

    exit_code = main(["eval", "--suite", "v0.7"])

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert report["total_scenarios"] == 36
    assert report["passed_scenarios"] == 36
    assert report["long_context_reduction_ratio"] >= 0.30
    assert report["passed"] is True


def test_cli_runs_the_v0_8_eval_system_suite(capsys) -> None:
    """验证 CLI 可以单独运行四十个 Harness 自检场景。"""

    exit_code = main(["eval", "--suite", "v0.8"])

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert report["total_scenarios"] == 40
    assert report["passed_scenarios"] == 40
    assert report["safety_gate_failures"] == 0
    assert report["passed"] is True


def test_cli_inspects_only_redacted_l2_context_metadata(
    capsys,
    tmp_path: Path,
) -> None:
    """验证本地诊断只读输出引用和指标，不泄露 Pack hash 或候选正文。"""

    database = tmp_path / "business.sqlite"
    upgrade_business_database(database)
    engine = create_business_engine(database)
    business = SqliteBusinessRepository(engine)
    invitation = business.create_invitation()
    registration = business.register(
        username="diagnostic.user",
        password="correct horse battery",
        invitation_code=invitation.code,
    )
    conversation = business.create_conversation(
        subject_id=registration.user.id,
        workspace_id=registration.workspace.id,
        access_mode="registered",
    )
    repository = SqliteL2CaseRepository(engine)
    repository.create_case_if_absent(
        L2CaseCreate(
            case_id="case-diagnostic",
            thread_id=conversation.thread_id,
            subject_id=registration.user.id,
            user_id=registration.user.id,
            workspace_id=registration.workspace.id,
            issue_summary="核对 ORD-001 售后状态",
            model_name="fake-l2",
            prompt_version="v0.7.0",
            toolset_version="v0.7.0",
            context_policy_version=CONTEXT_POLICY_VERSION,
            budget=L2BudgetLimits(),
        )
    )
    context = build_l2_context(
        runtime=L2RuntimeState(
            case_id="case-diagnostic",
            phase="active",
            issue_summary="核对 ORD-001 售后状态",
            related_order_id="ORD-001",
            allowed_tools=("get_order",),
        ),
        case_id="case-diagnostic",
        step_id="step-diagnostic",
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        now=datetime(2026, 7, 21, 10, 0, tzinfo=UTC),
    )
    repository.save_manifest_once(
        user_id=registration.user.id,
        workspace_id=registration.workspace.id,
        manifest=context.manifest,
    )
    engine.dispose()

    exit_code = main(
        [
            "l2-context",
            "inspect",
            "--case-id",
            "case-diagnostic",
            "--database",
            str(database),
        ]
    )

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert report["case"]["trace_state"] == "complete"
    assert report["manifests"][0]["items"]
    assert "scope_fingerprint" not in captured.out
    assert "pack_hash" not in captured.out
    assert '"content"' not in captured.out


def test_cli_builds_the_policy_index(capsys, tmp_path) -> None:
    """验证 CLI 能校验默认受控语料并输出完整索引摘要。"""

    repository_source = Path(__file__).parent.parent / "data" / "policies"
    database = tmp_path / "policy-index.sqlite"

    exit_code = main(
        ["policy-index", "build"],
        policy_source_path=repository_source,
        policy_index_path=database,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert "政策索引构建成功" in captured.out
    assert "documents: 3" in captured.out
    assert "sections: 12" in captured.out
    assert "facts: 16" in captured.out
    assert database.is_file()


def test_cli_answers_a_policy_question_from_the_injected_index(
    capsys,
    tmp_path,
) -> None:
    """验证 ask 能从独立政策索引输出规范化结论和相对路径引用。"""

    source = Path(__file__).parent.parent / "data" / "policies"
    policy_database = tmp_path / "policy-index.sqlite"
    build_policy_index(source, policy_database)

    exit_code = main(
        [
            "ask",
            "--thread-id",
            "policy-cli-001",
            "--user-id",
            "user-001",
            "签收后多少天可以退货？",
        ],
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        policy_source_path=source,
        policy_index_path=policy_database,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert "普通商品签收后 7 天内可以申请无理由退货。" in captured.out
    assert "returns-v1.md:" in captured.out


def test_cli_explicitly_requests_a_policy_index_when_it_is_missing(
    capsys,
    tmp_path,
) -> None:
    """验证查询过程不会静默创建缺失索引，而是返回可操作提示。"""

    source = Path(__file__).parent.parent / "data" / "policies"
    missing_database = tmp_path / "missing-policy-index.sqlite"

    exit_code = main(
        [
            "ask",
            "--thread-id",
            "policy-index-missing",
            "--user-id",
            "user-001",
            "签收后多少天可以退货？",
        ],
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        policy_source_path=source,
        policy_index_path=missing_database,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert "policy-index build" in captured.out
    assert not missing_database.exists()


def test_cli_asks_for_a_valid_order(capsys, tmp_path) -> None:
    """验证 CLI 能完成有效订单查询并输出物流信息。"""

    exit_code = main(
        [
            "ask",
            "--thread-id",
            "direct-001",
            "--user-id",
            "user-001",
            "查询订单 ORD-001 的物流",
        ],
        checkpoint_path=tmp_path / "checkpoints.sqlite",
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert "订单 ORD-001 当前状态：已发货" in captured.out
    assert "预计送达：2026-07-18" in captured.out


def test_cli_requests_a_missing_order_id(capsys, tmp_path) -> None:
    """验证 CLI 在缺少订单号时输出补充信息提示。"""

    exit_code = main(
        [
            "ask",
            "--thread-id",
            "missing-order-001",
            "--user-id",
            "user-001",
            "帮我查一下物流",
        ],
        checkpoint_path=tmp_path / "checkpoints.sqlite",
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert captured.out == "请提供需要查询的订单号。\n"


def test_cli_resumes_a_conversation_from_sqlite(capsys, tmp_path) -> None:
    """验证两次独立 CLI 调用可以通过 SQLite 延续同一会话。"""

    database = tmp_path / "checkpoints.sqlite"
    first_exit_code = main(
        [
            "ask",
            "--thread-id",
            "resume-order-001",
            "--user-id",
            "user-001",
            "帮我查一下物流",
        ],
        checkpoint_path=database,
    )
    first_output = capsys.readouterr()

    second_exit_code = main(
        [
            "ask",
            "--thread-id",
            "resume-order-001",
            "--user-id",
            "user-001",
            "ORD-001",
        ],
        checkpoint_path=database,
    )
    second_output = capsys.readouterr()

    assert first_exit_code == 0
    assert first_output.err == ""
    assert first_output.out == "请提供需要查询的订单号。\n"
    assert second_exit_code == 0
    assert second_output.err == ""
    assert "订单 ORD-001 当前状态：已发货" in second_output.out


def test_cli_uses_safe_response_for_an_unavailable_order(capsys, tmp_path) -> None:
    """验证 CLI 不区分不存在或无权访问的订单。"""

    exit_code = main(
        [
            "ask",
            "--thread-id",
            "unavailable-order-001",
            "--user-id",
            "user-001",
            "查询订单 ORD-999",
        ],
        checkpoint_path=tmp_path / "checkpoints.sqlite",
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert captured.out == "无法查询该订单，请检查订单号或当前账号。\n"


def test_cli_hides_temporary_order_failure_details(capsys, tmp_path) -> None:
    """验证 CLI 对订单服务故障只输出脱敏提示。"""

    dependencies = Dependencies(
        interpreter=FakeQueryInterpreter(),
        order_gateway=FakeOrderGateway({}, temporarily_failed=True),
        logistics_gateway=FakeLogisticsGateway({}),
    )
    exit_code = main(
        [
            "ask",
            "--thread-id",
            "failed-order-001",
            "--user-id",
            "user-001",
            "查询订单 ORD-001",
        ],
        dependencies=dependencies,
        checkpoint_path=tmp_path / "checkpoints.sqlite",
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert captured.out == "订单或物流服务暂时不可用，请稍后重试。\n"


def test_cli_can_explicitly_select_openai_interpreter(
    capsys,
    monkeypatch,
    tmp_path,
) -> None:
    """验证 CLI 加载 .env，并仅在显式选项下装配真实解释器。"""

    from_env = Mock(return_value=FakeQueryInterpreter())
    load_dotenv = Mock()
    monkeypatch.setattr(OpenAIQueryInterpreter, "from_env", from_env)
    monkeypatch.setattr("commerce_resolve.cli.load_dotenv", load_dotenv)

    exit_code = main(
        [
            "ask",
            "--thread-id",
            "openai-interpreter-001",
            "--user-id",
            "user-001",
            "--interpreter",
            "openai",
            "查询订单 ORD-001 的物流",
        ],
        checkpoint_path=tmp_path / "checkpoints.sqlite",
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert "订单 ORD-001 当前状态：已发货" in captured.out
    from_env.assert_called_once_with()
    load_dotenv.assert_called_once_with(dotenv_path=DEFAULT_ENV_FILE, override=False)


def test_cli_hides_interpreter_failure_details(capsys, tmp_path) -> None:
    """验证真实解释器故障只返回统一提示和非零退出码。"""

    interpreter = Mock()
    interpreter.interpret.side_effect = InterpreterUnavailableError(
        "private upstream detail"
    )
    dependencies = Dependencies(
        interpreter=interpreter,
        order_gateway=FakeOrderGateway({}),
        logistics_gateway=FakeLogisticsGateway({}),
    )

    exit_code = main(
        [
            "ask",
            "--thread-id",
            "failed-interpreter-001",
            "--user-id",
            "user-001",
            "查询订单 ORD-001",
        ],
        dependencies=dependencies,
        checkpoint_path=tmp_path / "checkpoints.sqlite",
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == "意图识别服务暂时不可用，请稍后重试。\n"
    assert "private upstream detail" not in captured.err


def test_cli_upgrades_business_database_and_manages_invitations(
    capsys,
    tmp_path,
) -> None:
    """验证可信本地 CLI 可以迁移业务库并创建、撤销邀请码。"""

    from commerce_resolve.adapters.sqlite_business import create_business_engine

    database = tmp_path / "business.sqlite"
    upgraded = main(["db", "upgrade"], business_db_path=database)
    upgrade_output = capsys.readouterr()
    created = main(
        [
            "invite",
            "create",
            "--expires-in-hours",
            "24",
            "--max-uses",
            "1",
        ],
        business_db_path=database,
    )
    invitation_output = capsys.readouterr()
    invitation_id = next(
        line.removeprefix("invite_id: ")
        for line in invitation_output.out.splitlines()
        if line.startswith("invite_id: ")
    )
    revoked = main(
        ["invite", "revoke", "--invite-id", invitation_id],
        business_db_path=database,
    )
    revoke_output = capsys.readouterr()
    engine = create_business_engine(database)

    assert upgraded == created == revoked == 0
    assert "已升级" in upgrade_output.out
    assert "code: " in invitation_output.out
    assert "已失效" in revoke_output.out
    assert "users" in inspect(engine).get_table_names()
    engine.dispose()


def test_cli_serve_uses_single_worker_and_explicit_paths(
    capsys,
    monkeypatch,
    tmp_path,
) -> None:
    """验证 Web 启动拒绝隐式迁移，并固定使用单 worker。"""

    import uvicorn

    database = tmp_path / "business.sqlite"
    main(["db", "upgrade"], business_db_path=database)
    capsys.readouterr()
    run = Mock()
    monkeypatch.setattr(uvicorn, "run", run)

    exit_code = main(
        ["serve", "--host", "127.0.0.1", "--port", "8010"],
        business_db_path=database,
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        policy_source_path=Path("data/policies"),
        policy_index_path=tmp_path / "policy.sqlite",
    )

    assert exit_code == 0
    run.assert_called_once()
    assert run.call_args.kwargs == {
        "host": "127.0.0.1",
        "port": 8010,
        "workers": 1,
    }


def test_cli_exports_openapi_for_frontend_type_generation(
    capsys,
    tmp_path,
) -> None:
    """验证生成契约包含 v0.3 Session、Chat 与订单 API。"""

    database = tmp_path / "business.sqlite"
    output = tmp_path / "openapi.json"
    main(["db", "upgrade"], business_db_path=database)
    capsys.readouterr()

    exit_code = main(
        ["openapi", "export", "--output", str(output)],
        business_db_path=database,
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        policy_source_path=Path("data/policies"),
        policy_index_path=tmp_path / "policy.sqlite",
    )
    document = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert "/api/session" in document["paths"]
    assert "/api/chat/messages" in document["paths"]
    assert "/api/orders" in document["paths"]
