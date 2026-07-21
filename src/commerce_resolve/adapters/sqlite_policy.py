"""使用受控 Markdown/JSON 语料构建并读取 SQLite 政策索引。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import ValidationError

from commerce_resolve.gateways import PolicyRepositoryUnavailableError
from commerce_resolve.models import (
    PolicyCitation,
    PolicyDocumentDefinition,
    PolicyEvidenceRef,
    PolicyFact,
    PolicyFactDefinition,
    PolicyIndexSummary,
    PolicyManifest,
    PolicyQuery,
    PolicySearchResult,
    PolicySectionDefinition,
)

POLICY_INDEX_SCHEMA_VERSION = 2
DEFAULT_RETRIEVAL_LIMIT = 6
MIN_TOKEN_COVERAGE = 0.08
SECTION_HEADING_PATTERN = re.compile(r"^## \[([a-z0-9-]+)\] (.+)$")
TEXT_PART_PATTERN = re.compile(
    r"[a-z0-9]+(?:-[a-z0-9]+)*|[\u3400-\u4dbf\u4e00-\u9fff]+",
    re.IGNORECASE,
)
CHINESE_PATTERN = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff]+$")

TOPIC_ALIASES = {
    "return": ("退货", "退回商品", "无理由退货"),
    "refund": ("退款", "退款到账", "原路退回"),
    "exchange": ("换货", "更换商品", "换新"),
}
ASPECT_ALIASES = {
    "window": ("期限", "多少天", "有效期"),
    "conditions": ("条件", "要求", "是否可以"),
    "shipping_fee": ("运费", "邮费", "谁承担"),
    "exception": ("例外", "不支持", "特殊商品"),
    "process": ("流程", "怎么申请", "办理方式"),
    "timing": ("多久到账", "处理时间", "工作日"),
    "method": ("退款方式", "原支付方式", "退到哪里"),
}


class PolicyIndexBuildError(ValueError):
    """表示政策语料或索引构建不满足确定性契约。"""


@dataclass(frozen=True)
class _ParsedSection:
    """保存从 Markdown 定位出的章节正文和行范围。"""

    section_id: str
    heading: str
    line_start: int
    line_end: int
    content: str
    content_hash: str


def analyze_policy_text(text: str) -> tuple[str, ...]:
    """把中文转换为字符 bigram，并保留规范化英文和数字词。"""

    normalized = unicodedata.normalize("NFKC", text).lower()
    tokens: list[str] = []
    for part in TEXT_PART_PATTERN.findall(normalized):
        if CHINESE_PATTERN.fullmatch(part):
            if len(part) == 1:
                tokens.append(part)
            else:
                tokens.extend(part[index : index + 2] for index in range(len(part) - 1))
            continue
        tokens.append(part)
    return tuple(dict.fromkeys(tokens))


def _hash_text(text: str) -> str:
    """返回稳定的 UTF-8 SHA-256 十六进制摘要。"""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_markdown_sections(text: str) -> dict[str, _ParsedSection]:
    """按稳定二级标题标识提取 Markdown 章节和精确行范围。"""

    lines = text.splitlines()
    headings: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines):
        match = SECTION_HEADING_PATTERN.fullmatch(line)
        if match is not None:
            headings.append((index, match.group(1), match.group(2)))

    sections: dict[str, _ParsedSection] = {}
    for position, (start_index, section_id, heading) in enumerate(headings):
        if section_id in sections:
            raise PolicyIndexBuildError(f"重复的 Markdown 章节标识：{section_id}")
        next_index = (
            headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        )
        content = "\n".join(lines[start_index:next_index]).rstrip()
        sections[section_id] = _ParsedSection(
            section_id=section_id,
            heading=heading,
            line_start=start_index + 1,
            line_end=next_index,
            content=content,
            content_hash=_hash_text(content),
        )
    return sections


def _load_manifest(source_root: Path) -> tuple[PolicyManifest, bytes]:
    """读取并使用 Pydantic 校验政策根清单。"""

    manifest_path = source_root / "manifest.json"
    try:
        payload = manifest_path.read_bytes()
    except OSError as error:
        raise PolicyIndexBuildError("无法读取政策 manifest.json") from error
    try:
        return PolicyManifest.model_validate_json(payload), payload
    except ValidationError as error:
        raise PolicyIndexBuildError("政策 manifest.json 不符合 Schema") from error


def _resolve_document_path(source_root: Path, relative_path: str) -> Path:
    """解析政策文档路径并拒绝目录逃逸或绝对路径。"""

    relative = Path(relative_path)
    if relative.is_absolute():
        raise PolicyIndexBuildError("政策文档路径必须是相对路径")
    root = source_root.resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise PolicyIndexBuildError("政策文档路径不能离开语料目录")
    if not candidate.is_file():
        raise PolicyIndexBuildError(f"政策文档不存在：{relative_path}")
    return candidate


def _validate_unique_identifiers(manifest: PolicyManifest) -> None:
    """拒绝重复文档版本、章节或事实标识。"""

    document_keys: set[tuple[str, str]] = set()
    section_ids: set[str] = set()
    fact_ids: set[str] = set()
    for document in manifest.documents:
        document_key = (document.document_id, document.version)
        if document_key in document_keys:
            raise PolicyIndexBuildError("重复的政策文档版本")
        document_keys.add(document_key)
        for section in document.sections:
            if section.section_id in section_ids:
                raise PolicyIndexBuildError(f"重复的政策章节标识：{section.section_id}")
            section_ids.add(section.section_id)
            for fact in section.facts:
                if fact.fact_id in fact_ids:
                    raise PolicyIndexBuildError(f"重复的政策事实标识：{fact.fact_id}")
                fact_ids.add(fact.fact_id)


def _validate_document_sections(
    document: PolicyDocumentDefinition,
    parsed_sections: dict[str, _ParsedSection],
) -> None:
    """校验 manifest 与 Markdown 章节、标题和规范化事实一致。"""

    expected_ids = {section.section_id for section in document.sections}
    if expected_ids != set(parsed_sections):
        raise PolicyIndexBuildError(
            f"文档 {document.path} 的 Markdown 章节与 manifest 不一致"
        )
    for section in document.sections:
        parsed = parsed_sections[section.section_id]
        if parsed.heading != section.heading:
            raise PolicyIndexBuildError(
                f"章节 {section.section_id} 的标题与 manifest 不一致"
            )
        for fact in section.facts:
            if fact.claim_text not in parsed.content:
                raise PolicyIndexBuildError(
                    f"事实 {fact.fact_id} 的 claim_text 不在对应原文中"
                )


def _serialize_models(models: tuple[object, ...]) -> str:
    """把 Pydantic 模型元组序列化为稳定 JSON。"""

    payload = [model.model_dump(mode="json") for model in models]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _create_index_schema(connection: sqlite3.Connection) -> None:
    """创建政策元数据表、章节表与 FTS5 虚拟表。"""

    connection.executescript(
        """
        CREATE TABLE index_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE policy_documents (
            document_id TEXT NOT NULL,
            title TEXT NOT NULL,
            version TEXT NOT NULL,
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            region TEXT NOT NULL,
            status TEXT NOT NULL,
            source_path TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            PRIMARY KEY (document_id, version)
        );

        CREATE TABLE policy_sections (
            section_pk INTEGER PRIMARY KEY,
            document_id TEXT NOT NULL,
            version TEXT NOT NULL,
            section_id TEXT NOT NULL UNIQUE,
            heading TEXT NOT NULL,
            line_start INTEGER NOT NULL,
            line_end INTEGER NOT NULL,
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            topic TEXT NOT NULL,
            aspects_json TEXT NOT NULL,
            aliases_json TEXT NOT NULL,
            facts_json TEXT NOT NULL,
            search_tokens_json TEXT NOT NULL,
            FOREIGN KEY (document_id, version)
                REFERENCES policy_documents(document_id, version)
        );

        CREATE VIRTUAL TABLE policy_sections_fts USING fts5(
            section_pk UNINDEXED,
            heading_terms,
            alias_terms,
            fact_terms,
            content_terms,
            tokenize='unicode61'
        );
        """
    )


def _insert_document(
    connection: sqlite3.Connection,
    document: PolicyDocumentDefinition,
    source_text: str,
) -> None:
    """写入经过校验的政策文档版本元数据。"""

    connection.execute(
        """
        INSERT INTO policy_documents (
            document_id, title, version, effective_from, effective_to,
            region, status, source_path, content_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            document.document_id,
            document.title,
            document.version,
            document.effective_from.isoformat(),
            document.effective_to.isoformat()
            if document.effective_to is not None
            else None,
            document.region,
            document.status,
            document.path,
            _hash_text(source_text),
        ),
    )


def _insert_section(
    connection: sqlite3.Connection,
    document: PolicyDocumentDefinition,
    section: PolicySectionDefinition,
    parsed: _ParsedSection,
) -> None:
    """写入政策章节、结构化事实和预分词全文索引。"""

    heading_tokens = analyze_policy_text(section.heading)
    controlled_aliases = [*TOPIC_ALIASES[section.topic]]
    for aspect in section.aspects:
        controlled_aliases.extend(ASPECT_ALIASES[aspect])
    alias_tokens = analyze_policy_text(
        " ".join((*section.aliases, *controlled_aliases))
    )
    fact_tokens = analyze_policy_text(
        " ".join(
            [
                item
                for fact in section.facts
                for item in (
                    fact.claim_text,
                    fact.rule_key,
                    fact.normalized_value,
                )
            ]
        )
    )
    content_tokens = analyze_policy_text(parsed.content)
    search_tokens = tuple(
        dict.fromkeys((*heading_tokens, *alias_tokens, *fact_tokens, *content_tokens))
    )
    cursor = connection.execute(
        """
        INSERT INTO policy_sections (
            document_id, version, section_id, heading, line_start, line_end,
            content, content_hash, topic, aspects_json, aliases_json,
            facts_json, search_tokens_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            document.document_id,
            document.version,
            section.section_id,
            section.heading,
            parsed.line_start,
            parsed.line_end,
            parsed.content,
            parsed.content_hash,
            section.topic,
            json.dumps(section.aspects, ensure_ascii=False),
            json.dumps(section.aliases, ensure_ascii=False),
            _serialize_models(section.facts),
            json.dumps(search_tokens, ensure_ascii=False),
        ),
    )
    section_pk = cursor.lastrowid
    connection.execute(
        """
        INSERT INTO policy_sections_fts (
            section_pk, heading_terms, alias_terms, fact_terms, content_terms
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            section_pk,
            " ".join(heading_tokens),
            " ".join(alias_tokens),
            " ".join(fact_tokens),
            " ".join(content_tokens),
        ),
    )


def _corpus_hash(manifest_payload: bytes, sources: list[tuple[str, bytes]]) -> str:
    """根据 manifest 和排序后的文档内容计算语料摘要。"""

    digest = hashlib.sha256()
    digest.update(manifest_payload)
    for relative_path, payload in sorted(sources):
        digest.update(relative_path.encode("utf-8"))
        digest.update(payload)
    return digest.hexdigest()


def calculate_policy_corpus_hash(source_root: str | Path) -> str:
    """计算当前受控语料摘要，用于识别索引与事实来源是否一致。"""

    source = Path(source_root)
    manifest, manifest_payload = _load_manifest(source)
    source_payloads: list[tuple[str, bytes]] = []
    for document in manifest.documents:
        document_path = _resolve_document_path(source, document.path)
        try:
            payload = document_path.read_bytes()
        except OSError as error:
            raise PolicyIndexBuildError(f"无法读取政策文档：{document.path}") from error
        source_payloads.append((document.path, payload))
    return _corpus_hash(manifest_payload, source_payloads)


def build_policy_index(
    source_root: str | Path,
    database_path: str | Path,
) -> PolicyIndexSummary:
    """校验受控语料并原子构建可重建的 SQLite FTS5 索引。"""

    source = Path(source_root)
    database = Path(database_path)
    manifest, manifest_payload = _load_manifest(source)
    _validate_unique_identifiers(manifest)

    prepared_documents: list[
        tuple[PolicyDocumentDefinition, str, dict[str, _ParsedSection]]
    ] = []
    source_payloads: list[tuple[str, bytes]] = []
    for document in manifest.documents:
        document_path = _resolve_document_path(source, document.path)
        try:
            payload = document_path.read_bytes()
            text = payload.decode("utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise PolicyIndexBuildError(
                f"无法读取 UTF-8 政策文档：{document.path}"
            ) from error
        parsed_sections = _extract_markdown_sections(text)
        _validate_document_sections(document, parsed_sections)
        prepared_documents.append((document, text, parsed_sections))
        source_payloads.append((document.path, payload))

    corpus_hash = _corpus_hash(manifest_payload, source_payloads)
    database.parent.mkdir(parents=True, exist_ok=True)
    temporary = database.with_name(f"{database.name}.tmp")
    temporary.unlink(missing_ok=True)
    section_count = sum(len(item.sections) for item in manifest.documents)
    fact_count = sum(
        len(section.facts)
        for document in manifest.documents
        for section in document.sections
    )
    try:
        with sqlite3.connect(temporary) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                _create_index_schema(connection)
            except sqlite3.OperationalError as error:
                raise PolicyIndexBuildError(
                    "当前 Python SQLite 不支持 FTS5，无法构建政策索引"
                ) from error
            metadata = {
                "schema_version": str(POLICY_INDEX_SCHEMA_VERSION),
                "corpus_version": manifest.corpus_version,
                "corpus_hash": corpus_hash,
                "built_at": datetime.now(UTC).isoformat(),
            }
            connection.executemany(
                "INSERT INTO index_metadata(key, value) VALUES (?, ?)",
                metadata.items(),
            )
            for document, text, parsed_sections in prepared_documents:
                _insert_document(connection, document, text)
                for section in document.sections:
                    _insert_section(
                        connection,
                        document,
                        section,
                        parsed_sections[section.section_id],
                    )
            connection.commit()
        os.replace(temporary, database)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    return PolicyIndexSummary(
        schema_version=POLICY_INDEX_SCHEMA_VERSION,
        corpus_version=manifest.corpus_version,
        corpus_hash=corpus_hash,
        document_count=len(manifest.documents),
        section_count=section_count,
        fact_count=fact_count,
    )


def _query_tokens(question: str, query: PolicyQuery) -> tuple[str, ...]:
    """将原问题、受控主题别名和模型检索词转换为安全 token。"""

    pieces = [question, *query.search_terms, *TOPIC_ALIASES[query.topic]]
    for aspect in query.aspects:
        pieces.extend(ASPECT_ALIASES[aspect])
    return analyze_policy_text(" ".join(pieces))


def _fts_query(tokens: tuple[str, ...]) -> str:
    """把已清洗 token 转换为只含引号和 OR 的 FTS5 查询。"""

    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


def _read_metadata(connection: sqlite3.Connection) -> dict[str, str]:
    """读取并校验当前索引的必要版本元数据。"""

    rows = connection.execute("SELECT key, value FROM index_metadata").fetchall()
    metadata = {str(row[0]): str(row[1]) for row in rows}
    if metadata.get("schema_version") != str(POLICY_INDEX_SCHEMA_VERSION):
        raise PolicyRepositoryUnavailableError("政策索引版本不兼容，请重新构建")
    if not metadata.get("corpus_version") or not metadata.get("corpus_hash"):
        raise PolicyRepositoryUnavailableError("政策索引元数据不完整，请重新构建")
    return metadata


class SqlitePolicyRepository:
    """从只读 SQLite FTS5 索引检索并解析政策事实。"""

    def __init__(
        self,
        database_path: str | Path,
        *,
        source_root: str | Path | None = None,
    ) -> None:
        """保存索引和可选语料路径，查询时使用只读连接并校验新鲜度。"""

        self._database_path = Path(database_path)
        self._source_root = Path(source_root) if source_root is not None else None
        self.calls: list[tuple[str, PolicyQuery, date, int]] = []

    def _validate_current_corpus(self, metadata: dict[str, str]) -> None:
        """在提供语料目录时拒绝使用内容摘要不一致的陈旧索引。"""

        if self._source_root is None:
            return
        try:
            current_hash = calculate_policy_corpus_hash(self._source_root)
        except PolicyIndexBuildError as error:
            raise PolicyRepositoryUnavailableError(
                "政策语料不可用，请检查语料并重新构建索引"
            ) from error
        if current_hash != metadata["corpus_hash"]:
            raise PolicyRepositoryUnavailableError("政策索引已过期，请重新构建")

    def _connect(self) -> sqlite3.Connection:
        """打开只读 SQLite 连接，并将缺失或损坏索引映射为领域错误。"""

        try:
            connection = sqlite3.connect(
                f"file:{self._database_path.resolve()}?mode=ro",
                uri=True,
            )
            connection.row_factory = sqlite3.Row
            return connection
        except sqlite3.Error as error:
            raise PolicyRepositoryUnavailableError(
                "政策索引不可用，请先执行 policy-index build"
            ) from error

    def search(
        self,
        question: str,
        query: PolicyQuery,
        as_of: date,
        *,
        limit: int = DEFAULT_RETRIEVAL_LIMIT,
    ) -> PolicySearchResult:
        """按有效日期、区域、主题和 BM25 排序返回政策证据引用。"""

        self.calls.append((question, query, as_of, limit))
        tokens = _query_tokens(question, query)
        if not tokens:
            return PolicySearchResult(
                corpus_version="unknown",
                corpus_hash="unknown",
                evidence_refs=(),
            )
        try:
            with self._connect() as connection:
                metadata = _read_metadata(connection)
                self._validate_current_corpus(metadata)
                rows = connection.execute(
                    """
                    SELECT
                        s.document_id,
                        s.version,
                        s.section_id,
                        s.content_hash,
                        s.aspects_json,
                        s.facts_json,
                        s.search_tokens_json,
                        bm25(policy_sections_fts, 0.0, 4.0, 3.0, 2.0, 1.0)
                            AS score
                    FROM policy_sections_fts
                    JOIN policy_sections AS s
                      ON s.section_pk = policy_sections_fts.section_pk
                    JOIN policy_documents AS d
                      ON d.document_id = s.document_id AND d.version = s.version
                    WHERE policy_sections_fts MATCH ?
                      AND s.topic = ?
                      AND d.region = ?
                      AND d.status = 'published'
                      AND d.effective_from <= ?
                      AND (d.effective_to IS NULL OR d.effective_to >= ?)
                    ORDER BY score
                    LIMIT ?
                    """,
                    (
                        _fts_query(tokens),
                        query.topic,
                        query.region,
                        as_of.isoformat(),
                        as_of.isoformat(),
                        max(limit * 4, limit),
                    ),
                ).fetchall()
        except PolicyRepositoryUnavailableError:
            raise
        except sqlite3.Error as error:
            raise PolicyRepositoryUnavailableError("政策索引查询暂时失败") from error

        requested_aspects = set(query.aspects)
        query_token_set = set(tokens)
        evidence: list[PolicyEvidenceRef] = []
        for row in rows:
            section_aspects = set(json.loads(row["aspects_json"]))
            if not requested_aspects.intersection(section_aspects):
                continue
            section_tokens = set(json.loads(row["search_tokens_json"]))
            coverage = len(query_token_set.intersection(section_tokens)) / len(
                query_token_set
            )
            if coverage < MIN_TOKEN_COVERAGE:
                continue
            facts = tuple(
                PolicyFactDefinition.model_validate(item)
                for item in json.loads(row["facts_json"])
            )
            evidence.append(
                PolicyEvidenceRef(
                    document_id=row["document_id"],
                    version=row["version"],
                    section_id=row["section_id"],
                    fact_ids=tuple(fact.fact_id for fact in facts),
                    score=float(row["score"]),
                    token_coverage=coverage,
                    content_hash=row["content_hash"],
                )
            )
            if len(evidence) >= limit:
                break
        return PolicySearchResult(
            corpus_version=metadata["corpus_version"],
            corpus_hash=metadata["corpus_hash"],
            evidence_refs=tuple(evidence),
        )

    def resolve_fact(
        self,
        fact_id: str,
        expected_hash: str,
    ) -> PolicyFact | None:
        """按事实标识解析原文，并拒绝内容哈希变化或无效引用。"""

        try:
            with self._connect() as connection:
                metadata = _read_metadata(connection)
                self._validate_current_corpus(metadata)
                rows = connection.execute(
                    """
                    SELECT
                        s.topic,
                        s.aspects_json,
                        s.facts_json,
                        s.content,
                        s.content_hash,
                        s.section_id,
                        s.heading,
                        s.line_start,
                        s.line_end,
                        d.document_id,
                        d.title,
                        d.version,
                        d.effective_from,
                        d.effective_to,
                        d.source_path
                    FROM policy_sections AS s
                    JOIN policy_documents AS d
                      ON d.document_id = s.document_id AND d.version = s.version
                    """
                ).fetchall()
        except PolicyRepositoryUnavailableError:
            raise
        except sqlite3.Error as error:
            raise PolicyRepositoryUnavailableError("政策事实解析暂时失败") from error

        for row in rows:
            if row["content_hash"] != expected_hash:
                continue
            definitions = (
                PolicyFactDefinition.model_validate(item)
                for item in json.loads(row["facts_json"])
            )
            for definition in definitions:
                if definition.fact_id != fact_id:
                    continue
                if definition.claim_text not in row["content"]:
                    return None
                citation = PolicyCitation(
                    document_id=row["document_id"],
                    title=row["title"],
                    version=row["version"],
                    effective_from=date.fromisoformat(row["effective_from"]),
                    effective_to=date.fromisoformat(row["effective_to"])
                    if row["effective_to"]
                    else None,
                    section_id=row["section_id"],
                    heading=row["heading"],
                    source_relative_path=row["source_path"],
                    line_start=int(row["line_start"]),
                    line_end=int(row["line_end"]),
                    content_hash=row["content_hash"],
                )
                return PolicyFact(
                    fact_id=definition.fact_id,
                    claim_text=definition.claim_text,
                    rule_key=definition.rule_key,
                    normalized_value=definition.normalized_value,
                    scope=definition.scope,
                    required_dimensions=definition.required_dimensions,
                    topic=row["topic"],
                    aspects=tuple(json.loads(row["aspects_json"])),
                    citation=citation,
                )
        return None
