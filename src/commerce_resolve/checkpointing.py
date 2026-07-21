"""集中创建具有显式领域类型白名单的 SQLite Checkpointer。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

ALLOWED_DOMAIN_TYPES = (
    ("commerce_resolve.models", "OrderView"),
    ("commerce_resolve.models", "ShipmentView"),
    ("commerce_resolve.models", "PolicyQuery"),
    ("commerce_resolve.models", "PolicyEvidenceRef"),
    ("commerce_resolve.models", "PolicyCitation"),
    ("commerce_resolve.models", "PolicyConflict"),
    ("commerce_resolve.models", "RefundReason"),
    ("commerce_resolve.models", "RefundContext"),
    ("commerce_resolve.models", "RefundEligibility"),
    ("commerce_resolve.models", "RefundPreview"),
    ("commerce_resolve.models", "RefundExecutionResult"),
    ("commerce_resolve.models", "RefundVerification"),
    ("commerce_resolve.l2_models", "L2BudgetLimits"),
    ("commerce_resolve.l2_models", "L2BudgetState"),
    ("commerce_resolve.l2_models", "L2UpgradePreview"),
    ("commerce_resolve.l2_models", "GetOrderCall"),
    ("commerce_resolve.l2_models", "GetShipmentCall"),
    ("commerce_resolve.l2_models", "GetRefundStatusCall"),
    ("commerce_resolve.l2_models", "SearchPolicyCall"),
    ("commerce_resolve.l2_models", "ListConfirmedPreferencesCall"),
    ("commerce_resolve.l2_models", "ToolCallDecision"),
    ("commerce_resolve.l2_models", "AskUserDecision"),
    ("commerce_resolve.l2_models", "ProposeRefundDecision"),
    ("commerce_resolve.l2_models", "ProposeMemoryDecision"),
    ("commerce_resolve.l2_models", "AnswerDecision"),
    ("commerce_resolve.l2_models", "StopDecision"),
    ("commerce_resolve.l2_models", "L2Observation"),
    ("commerce_resolve.l2_models", "OrderObservationSource"),
    ("commerce_resolve.l2_models", "ShipmentObservationSource"),
    ("commerce_resolve.l2_models", "RefundObservationSource"),
    ("commerce_resolve.l2_models", "PolicyObservationFact"),
    ("commerce_resolve.l2_models", "PolicyObservationSource"),
    ("commerce_resolve.l2_models", "PreferenceObservationSource"),
    ("commerce_resolve.l2_models", "MemoryProposal"),
    ("commerce_resolve.l2_models", "L2RuntimeState"),
)


def create_domain_serializer() -> JsonPlusSerializer:
    """创建仅允许恢复已知 CommerceResolve 领域模型的序列化器。"""

    return JsonPlusSerializer(
        allowed_msgpack_modules=ALLOWED_DOMAIN_TYPES,
    )


@contextmanager
def open_sqlite_checkpointer(database_path: str | Path) -> Iterator[SqliteSaver]:
    """打开 SQLite Checkpointer，并只允许反序列化已知领域模型。"""

    database = Path(database_path)
    database.parent.mkdir(parents=True, exist_ok=True)
    serializer = create_domain_serializer()
    connection = sqlite3.connect(str(database), check_same_thread=False)
    try:
        yield SqliteSaver(connection, serde=serializer)
    finally:
        connection.close()
