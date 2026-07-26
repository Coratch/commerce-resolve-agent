"""协调业务库、LangGraph Checkpoint 与长期 Memory 的完整工作区重置。"""

from pathlib import Path

from commerce_resolve.adapters.sqlite_workspaces import SqliteWorkspaceRepository
from commerce_resolve.business_models import UserRole
from commerce_resolve.checkpointing import open_sqlite_checkpointer
from commerce_resolve.l2_memory import clear_preferences, open_sqlite_memory_store
from commerce_resolve.workspace_models import WorkspaceResetResult


class WorkspaceResetService:
    """执行可重入的跨存储重置，并把业务库 Ready 作为完成标志。"""

    def __init__(
        self,
        repository: SqliteWorkspaceRepository,
        *,
        checkpoint_database: str | Path,
        memory_database: str | Path,
    ) -> None:
        """保存工作区仓库及两个独立 LangGraph 存储路径。"""

        self.repository = repository
        self.checkpoint_database = Path(checkpoint_database)
        self.memory_database = Path(memory_database)

    def reset(
        self,
        *,
        owner_user_id: str,
        workspace_id: str,
        actor_user_id: str,
        actor_role: UserRole,
        client_request_id: str,
    ) -> WorkspaceResetResult:
        """清除外部状态后原子重建业务基准，重复请求返回既有结果。"""

        plan = self.repository.prepare_reset(
            user_id=owner_user_id,
            workspace_id=workspace_id,
            client_request_id=client_request_id,
        )
        if not plan.already_completed:
            with open_sqlite_checkpointer(self.checkpoint_database) as checkpointer:
                checkpointer.setup()
                for thread_id in plan.thread_ids:
                    checkpointer.delete_thread(thread_id)
            with open_sqlite_memory_store(self.memory_database) as store:
                clear_preferences(
                    store,
                    user_id=owner_user_id,
                    workspace_id=workspace_id,
                )
        return self.repository.complete_reset(
            plan,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
        )
