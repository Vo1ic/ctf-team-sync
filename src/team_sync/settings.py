import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    bootstrap_token: str
    artifact_dir: Path
    max_artifact_bytes: int = 64 * 1024 * 1024

    @classmethod
    def from_env(cls):
        return cls(
            database_url=os.environ["TEAM_SYNC_DATABASE_URL"],
            bootstrap_token=os.environ["TEAM_SYNC_BOOTSTRAP_TOKEN"],
            artifact_dir=Path(os.getenv("TEAM_SYNC_ARTIFACT_DIR", "./data/artifacts")),
        )
