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
from app.worker.jobs.sync_player_profiles import (
    SYNC_PLAYER_PROFILES_JOB_NAME,
    ClashPlayerProfileProvider,
    PlayerProfileData,
    SqlAlchemySyncPlayerProfilesRepository,
    SyncPlayerProfilesJob,
    SyncPlayerProfilesJobResult,
    SyncPlayerProfilesRepository,
    register_sync_player_profiles_job,
)

__all__ = [
    "SYNC_CLANS_JOB_NAME",
    "SYNC_MEMBERS_JOB_NAME",
    "SYNC_PLAYER_PROFILES_JOB_NAME",
    "ClashClanMembersProvider",
    "ClashClanSyncProvider",
    "ClashPlayerProfileProvider",
    "MemberLifecycleProcessor",
    "PlayerProfileData",
    "SqlAlchemySyncClansRepository",
    "SqlAlchemySyncMembersRepository",
    "SqlAlchemySyncPlayerProfilesRepository",
    "SyncClansJob",
    "SyncClansJobResult",
    "SyncClansRepository",
    "SyncMembersJob",
    "SyncMembersJobResult",
    "SyncMembersRepository",
    "SyncPlayerProfilesJob",
    "SyncPlayerProfilesJobResult",
    "SyncPlayerProfilesRepository",
    "register_sync_clans_job",
    "register_sync_members_job",
    "register_sync_player_profiles_job",
]
