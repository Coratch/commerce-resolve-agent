"""定义 v2.0 演示工作区状态、重置计划与公开结果。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DemoWorkspaceStatus(BaseModel):
    """表示当前用户演示工作区的有限公开状态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_id: str
    owner_user_id: str
    dataset_version: str | None = None
    dataset_status: Literal["initializing", "ready", "resetting", "failed"] | None
    reset_generation: int = Field(ge=0)
    order_count: int = Field(ge=0)
    initialized_at: datetime | None = None


class WorkspaceResetPlan(BaseModel):
    """保存跨数据库重置协调所需的服务端可信引用。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_id: str
    owner_user_id: str
    client_request_id: str
    generation: int = Field(gt=0)
    thread_ids: tuple[str, ...]
    order_ids: dict[str, str]
    already_completed: bool = False


class WorkspaceResetResult(BaseModel):
    """返回成功重置后的版本、代数和公开订单号。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_id: str
    dataset_version: Literal["portfolio-demo-v1"]
    dataset_status: Literal["ready"] = "ready"
    reset_generation: int = Field(gt=0)
    order_ids: tuple[str, ...]
    completed_at: datetime
    already_completed: bool = False
