"""验证受控政策语料、SQLite FTS5 索引和只读检索契约。"""

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from commerce_resolve.adapters.sqlite_policy import (
    PolicyIndexBuildError,
    SqlitePolicyRepository,
    analyze_policy_text,
    build_policy_index,
)
from commerce_resolve.gateways import PolicyRepositoryUnavailableError
from commerce_resolve.models import PolicyQuery

SOURCE_POLICIES = Path(__file__).parent.parent / "data" / "policies"
AS_OF = date(2026, 7, 17)


def _copy_policies(destination: Path) -> Path:
    """复制主政策语料，允许单个测试安全修改临时文件。"""

    target = destination / "policies"
    shutil.copytree(SOURCE_POLICIES, target)
    return target


def _load_manifest(source: Path) -> dict[str, object]:
    """读取临时语料的原始 manifest 字典。"""

    return json.loads((source / "manifest.json").read_text(encoding="utf-8"))


def _write_manifest(source: Path, payload: dict[str, object]) -> None:
    """把修改后的临时 manifest 写回 UTF-8 JSON。"""

    (source / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def test_chinese_analyzer_keeps_two_character_policy_terms() -> None:
    """验证中文 bigram 能索引“退货”和“退款”等二字政策词。"""

    tokens = analyze_policy_text("退货退款 Policy-V1")

    assert "退货" in tokens
    assert "货退" in tokens
    assert "退款" in tokens
    assert "policy-v1" in tokens


def test_build_and_search_policy_index(tmp_path: Path) -> None:
    """验证主语料可重建，并召回单来源、多来源和无区域证据场景。"""

    database = tmp_path / "policy-index.sqlite"
    summary = build_policy_index(SOURCE_POLICIES, database)
    repository = SqlitePolicyRepository(database)

    return_result = repository.search(
        "签收后多少天可以退货",
        PolicyQuery(
            topic="return",
            aspects=("window",),
            search_terms=("退货期限",),
        ),
        AS_OF,
    )
    exchange_result = repository.search(
        "换货期限和条件",
        PolicyQuery(
            topic="exchange",
            aspects=("window", "conditions"),
            search_terms=("换货期限", "换货条件"),
        ),
        AS_OF,
    )
    overseas_result = repository.search(
        "海外门店退货期限",
        PolicyQuery(
            topic="return",
            aspects=("window",),
            region="overseas",
        ),
        AS_OF,
    )

    assert summary.document_count == 3
    assert summary.section_count == 12
    assert summary.fact_count == 16
    assert len(summary.corpus_hash) == 64
    assert [item.section_id for item in return_result.evidence_refs] == [
        "return-window"
    ]
    assert {item.section_id for item in exchange_result.evidence_refs} == {
        "exchange-window",
        "exchange-conditions",
    }
    assert overseas_result.evidence_refs == ()


def test_resolved_fact_has_a_verifiable_relative_citation(tmp_path: Path) -> None:
    """验证检索事实可以按 hash 解析回相对路径和原文行范围。"""

    database = tmp_path / "policy-index.sqlite"
    build_policy_index(SOURCE_POLICIES, database)
    repository = SqlitePolicyRepository(database)
    search_result = repository.search(
        "退款多久到账",
        PolicyQuery(topic="refund", aspects=("timing",)),
        AS_OF,
    )
    evidence = search_result.evidence_refs[0]

    fact = repository.resolve_fact(
        "refund.timing.standard",
        evidence.content_hash,
    )

    assert fact is not None
    assert fact.claim_text == (
        "退款申请审核通过后，款项将在 3 至 5 个工作日内原路退回。"
    )
    assert fact.citation.source_relative_path == "refunds-v1.md"
    assert fact.citation.line_start <= fact.citation.line_end
    assert fact.citation.content_hash == evidence.content_hash
    assert repository.resolve_fact("refund.timing.standard", "changed") is None


def test_build_rejects_claim_not_present_in_source(tmp_path: Path) -> None:
    """验证 manifest 事实无法在政策原文定位时拒绝整个构建。"""

    source = _copy_policies(tmp_path)
    manifest = _load_manifest(source)
    documents = manifest["documents"]
    assert isinstance(documents, list)
    documents[0]["sections"][0]["facts"][0]["claim_text"] = "原文不存在的结论。"
    _write_manifest(source, manifest)

    with pytest.raises(PolicyIndexBuildError, match="claim_text"):
        build_policy_index(source, tmp_path / "policy-index.sqlite")


def test_build_rejects_document_path_escape(tmp_path: Path) -> None:
    """验证 manifest 不能读取政策语料根目录之外的文件。"""

    source = _copy_policies(tmp_path)
    manifest = _load_manifest(source)
    documents = manifest["documents"]
    assert isinstance(documents, list)
    documents[0]["path"] = "../outside.md"
    _write_manifest(source, manifest)

    with pytest.raises(PolicyIndexBuildError, match="不能离开"):
        build_policy_index(source, tmp_path / "policy-index.sqlite")


def test_failed_rebuild_preserves_previous_index(tmp_path: Path) -> None:
    """验证无效新语料不会破坏已经发布的可查询索引。"""

    source = _copy_policies(tmp_path)
    database = tmp_path / "policy-index.sqlite"
    build_policy_index(source, database)
    manifest = _load_manifest(source)
    documents = manifest["documents"]
    assert isinstance(documents, list)
    documents[0]["sections"][0]["heading"] = "错误标题"
    _write_manifest(source, manifest)

    with pytest.raises(PolicyIndexBuildError, match="标题"):
        build_policy_index(source, database)

    result = SqlitePolicyRepository(database).search(
        "退货期限",
        PolicyQuery(topic="return", aspects=("window",)),
        AS_OF,
    )
    assert result.evidence_refs[0].section_id == "return-window"


def test_search_only_uses_currently_effective_versions(tmp_path: Path) -> None:
    """验证尚未生效的文档不会进入当前日期检索结果。"""

    source = _copy_policies(tmp_path)
    manifest = _load_manifest(source)
    documents = manifest["documents"]
    assert isinstance(documents, list)
    documents[0]["effective_from"] = "2030-01-01"
    _write_manifest(source, manifest)
    database = tmp_path / "policy-index.sqlite"
    build_policy_index(source, database)

    result = SqlitePolicyRepository(database).search(
        "退货期限",
        PolicyQuery(topic="return", aspects=("window",)),
        AS_OF,
    )

    assert result.evidence_refs == ()


def test_repository_rejects_an_index_after_source_changes(tmp_path: Path) -> None:
    """验证受控语料变化后，运行时不会继续使用陈旧索引回答。"""

    source = _copy_policies(tmp_path)
    database = tmp_path / "policy-index.sqlite"
    build_policy_index(source, database)
    repository = SqlitePolicyRepository(database, source_root=source)
    policy_file = source / "returns-v1.md"
    policy_file.write_text(
        f"{policy_file.read_text(encoding='utf-8')}\n",
        encoding="utf-8",
    )

    with pytest.raises(PolicyRepositoryUnavailableError, match="索引已过期"):
        repository.search(
            "退货期限",
            PolicyQuery(topic="return", aspects=("window",)),
            AS_OF,
        )
