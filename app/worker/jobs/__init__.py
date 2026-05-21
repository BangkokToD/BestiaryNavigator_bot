"""Worker jobs приложения."""

from app.worker.jobs.sync_clans import (
    SYNC_CLANS_JOB_NAME,
    ClashClanSyncProvider,
    SqlAlchemySyncClansRepository,
    SyncClansJob,
    SyncClansJobResult,
    SyncClansRepository,
    register_sync_clans_job,
)
from app.worker.jobs.sync_members import (
    SYNC_MEMBERS_JOB_NAME,
    ClashClanMembersProvider,
    MemberLifecycleProcessor,
    SqlAlchemySyncMembersRepository,
    SyncMembersJob,
    SyncMembersJobResult,
    SyncMembersRepository,
    register_sync_members_job,
)

__all__ = [
    "SYNC_CLANS_JOB_NAME",
    "SYNC_MEMBERS_JOB_NAME",
    "ClashClanMembersProvider",
    "ClashClanSyncProvider",
    "MemberLifecycleProcessor",
    "SqlAlchemySyncClansRepository",
    "SqlAlchemySyncMembersRepository",
    "SyncClansJob",
    "SyncClansJobResult",
    "SyncClansRepository",
    "SyncMembersJob",
    "SyncMembersJobResult",
    "SyncMembersRepository",
    "register_sync_clans_job",
    "register_sync_members_job",
]
