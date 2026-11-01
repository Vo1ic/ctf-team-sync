"""Portable private connection files; no shell sourcing or host-specific login."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID


def load_connection(access_file=None):
    path = access_file or os.environ.get("TEAM_SYNC_ACCESS_FILE")
    if path:
        source = Path(path).expanduser()
        if source.stat().st_size > 64 * 1024:
            raise ValueError("Connection file exceeds the supported size")
        values = json.loads(source.read_text(encoding="utf-8"))
        if (
            not isinstance(values, dict)
            or type(values.get("schema_version", 1)) is not int
            or values.get("schema_version", 1) != 1
        ):
            raise ValueError("Unsupported connection file")
        url, project, token = (values.get(key) for key in ("url", "project_id", "token"))
    else:
        url, project, token = (
            os.environ.get(key)
            for key in ("TEAM_SYNC_URL", "TEAM_SYNC_PROJECT_ID", "TEAM_SYNC_TOKEN")
        )
    if not all(isinstance(value, str) and value.strip() for value in (url, project, token)):
        raise ValueError("A server URL, project ID, and credential are required")
    url = url.rstrip("/")
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or any((parsed.username, parsed.password, parsed.query, parsed.fragment))
    ):
        raise ValueError("Invalid server URL")
    return {"url": url, "project_id": str(UUID(project)), "token": token.strip()}
