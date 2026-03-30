#!/usr/bin/env python3
import os
import tempfile

import yaml

from common import eprint, run


def docker_compose_config(
    compose_files: list[str],
    compose_global_args: list[str] | None = None,
    project_name: str | None = None,
):
    cmd = ["docker", "compose"]
    if project_name:
        cmd.extend(["-p", project_name])
    for arg in compose_global_args or []:
        cmd.append(arg)
    for compose_file in compose_files:
        cmd.extend(["-f", compose_file])
    cmd.append("config")
    result = run(cmd, check=False, capture_output=True)
    if result.returncode != 0:
        eprint(result.stdout)
        eprint(result.stderr)
        raise SystemExit("ERROR: 'docker compose config' failed")
    return result.stdout


def save_temp_compose(content: dict) -> str:
    # The wrapper mutates the rendered Compose document (labels, GPU env), so
    # execution happens through a temporary materialized file.
    fd, path = tempfile.mkstemp(prefix="ml-compose-", suffix=".yml")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        yaml.safe_dump(content, f, sort_keys=False)
    return path
