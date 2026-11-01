"""Repeatable mixed-client acceptance over a real Docker network, isolated in a new CTF."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx


def write_private(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(value, output, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file", type=Path, help="Read TEAM_SYNC_URL and bootstrap token from a local env file"
    )
    parser.add_argument("--url", help="Controller URL; defaults to TEAM_SYNC_URL or localhost:8000")
    parser.add_argument("--container-url", default="http://server:8000")
    parser.add_argument(
        "--network", help="Docker network; defaults to the running Compose server's network"
    )
    parser.add_argument("--python-image", help="Defaults to the running Compose server's image")
    parser.add_argument("--node-image", default="node:22-alpine")
    args = parser.parse_args()
    values = dict(os.environ)
    if args.env_file:
        for line in args.env_file.read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and key in {"TEAM_SYNC_URL", "TEAM_SYNC_BOOTSTRAP_TOKEN"}:
                values.setdefault(key, value.strip().strip("\"'"))
    base = args.url or values.get("TEAM_SYNC_URL", "http://localhost:8000")
    bootstrap = values["TEAM_SYNC_BOOTSTRAP_TOKEN"]
    root = Path(__file__).resolve().parents[1]
    if not args.network or not args.python_image:
        container = subprocess.check_output(
            ["docker", "compose", "ps", "-q", "server"], cwd=root, text=True
        ).strip()
        inspected = json.loads(
            subprocess.check_output(["docker", "inspect", container], text=True)
        )[0]
        args.network = args.network or next(iter(inspected["NetworkSettings"]["Networks"]))
        args.python_image = args.python_image or inspected["Image"]

    with httpx.Client(base_url=base.rstrip("/"), timeout=30) as api:
        deadline = time.monotonic() + 30
        while True:
            try:
                if api.get("/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Server readiness deadline exceeded")
            time.sleep(0.25)
        response = api.post(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {bootstrap}"},
            json={
                "name": "Interoperability acceptance " + uuid4().hex[:8],
                "owner_name": "Acceptance controller",
                "profile": "custom",
                "capabilities": {"flags": False},
                "rules": "Generated protocol acceptance data. "
                "No model inference or existing CTF data is used.",
            },
        )
        response.raise_for_status()
        created = response.json()
        project_id = created["project"]["id"]
        directory = root / ".team-sync" / "acceptance" / project_id
        directory.mkdir(parents=True, mode=0o700)
        write_private(
            directory / "access.json",
            {"url": base, "project_id": project_id, "owner_token": created["token"]},
        )
        endpoint = f"/api/v1/projects/{project_id}"
        owner_headers = {"Authorization": f"Bearer {created['token']}"}
        actors = []
        for name, skills in [
            ("Python MCP client (Debian)", ["mcp-stdio"]),
            ("Node HTTP client (Alpine)", ["http-api"]),
        ]:
            response = api.post(
                endpoint + "/actors",
                headers=owner_headers,
                json={"name": name, "kind": "agent", "role": "worker", "capabilities": skills},
            )
            response.raise_for_status()
            actors.append(response.json())

        def container_client(image, command, actor, **extra):
            environment = {
                **os.environ,
                "TEAM_SYNC_URL": args.container_url,
                "TEAM_SYNC_PROJECT_ID": project_id,
                "TEAM_SYNC_TOKEN": actor["token"],
                **extra,
            }
            names = ["TEAM_SYNC_URL", "TEAM_SYNC_PROJECT_ID", "TEAM_SYNC_TOKEN", *extra]
            invocation = [
                "docker",
                "run",
                "--rm",
                "--network",
                args.network,
                "--volume",
                f"{root / 'scripts'}:/acceptance:ro",
            ]
            for name in names:
                invocation.extend(["--env", name])
            result = subprocess.run(
                [*invocation, image, *command],
                env=environment,
                capture_output=True,
                text=True,
                timeout=180,
            )
            if result.returncode:
                raise RuntimeError(
                    "Isolated acceptance client failed; its private output was not echoed"
                )
            return json.loads(result.stdout)

        seeded = container_client(
            args.python_image,
            ["/app/.venv/bin/python", "/acceptance/acceptance-mcp.py"],
            actors[0],
            ACCEPTANCE_PHASE="seed",
            ACCEPTANCE_PARTNER=actors[1]["actor"]["id"],
        )
        http_result = container_client(
            args.node_image,
            ["node", "/acceptance/acceptance-http.mjs"],
            actors[1],
            ACCEPTANCE_TASK=seeded["task_id"],
            ACCEPTANCE_ARTIFACT=seeded["artifact_id"],
            ACCEPTANCE_MESSAGE=seeded["message_id"],
            ACCEPTANCE_PARTNER=actors[0]["actor"]["id"],
        )
        verified = container_client(
            args.python_image,
            ["/app/.venv/bin/python", "/acceptance/acceptance-mcp.py"],
            actors[0],
            ACCEPTANCE_PHASE="verify",
            ACCEPTANCE_TASK=seeded["task_id"],
            ACCEPTANCE_PARTNER=actors[1]["actor"]["id"],
        )
        report = {
            "project_id": project_id,
            "scope": "two isolated runtime containers over a Docker network",
            "native_model_sessions": False,
            "python_mcp": verified,
            "node_http": http_result,
            "private_access_file": str((directory / "access.json").relative_to(root)),
        }
        write_private(directory / "report.json", report)
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print(
            f"Acceptance stopped ({type(error).__name__}); "
            "see the private run directory for any issued access.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
