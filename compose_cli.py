#!/usr/bin/env python3
from dataclasses import dataclass
from pathlib import Path


COMPOSE_GLOBAL_OPTS_WITH_VALUE = {
    "--ansi",
    "--env-file",
    "--parallel",
    "--profile",
    "--progress",
    "--project-directory",
}

COMPOSE_GLOBAL_OPTS_EQ_PREFIXES = (
    "--ansi=",
    "--env-file=",
    "--parallel=",
    "--profile=",
    "--progress=",
    "--project-directory=",
)

COMPOSE_GLOBAL_FLAGS = ("--all-resources", "--compatibility", "--dry-run")


@dataclass(frozen=True)
class ComposeCliArgs:
    compose_files: list[str]
    compose_global_args: list[str]
    action_args: list[str]
    project_name_override: str | None


def parse_compose_cli_args(args: list[str]) -> ComposeCliArgs:
    compose_files = []
    compose_global_args = []
    action_args = []
    project_name_override = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-f", "--file"):
            if i + 1 >= len(args):
                raise SystemExit("ERROR: missing value after -f/--file")
            compose_files.append(args[i + 1])
            i += 2
            continue
        if arg.startswith("--file="):
            compose_files.append(arg.split("=", 1)[1])
            i += 1
            continue
        if arg in ("-p", "--project-name"):
            if i + 1 >= len(args):
                raise SystemExit(f"ERROR: missing value after {arg}")
            project_name_override = args[i + 1]
            i += 2
            continue
        if arg.startswith("--project-name="):
            project_name_override = arg.split("=", 1)[1]
            i += 1
            continue
        # Compose-global options must stay before the subcommand when we later
        # reconstruct `docker compose ...`, so they are separated here.
        if arg in COMPOSE_GLOBAL_OPTS_WITH_VALUE:
            if i + 1 >= len(args):
                raise SystemExit(f"ERROR: missing value after {arg}")
            compose_global_args.extend([arg, args[i + 1]])
            i += 2
            continue
        if any(arg.startswith(prefix) for prefix in COMPOSE_GLOBAL_OPTS_EQ_PREFIXES):
            compose_global_args.append(arg)
            i += 1
            continue
        if arg in COMPOSE_GLOBAL_FLAGS:
            compose_global_args.append(arg)
            i += 1
            continue
        action_args.append(arg)
        i += 1

    if not compose_files:
        # Mirror the usual Compose file discovery order for the current dir.
        for candidate in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
            if Path(candidate).exists():
                compose_files.append(candidate)
                break

    if not compose_files:
        raise SystemExit("ERROR: no compose file found in current directory")

    return ComposeCliArgs(
        compose_files=compose_files,
        compose_global_args=compose_global_args,
        action_args=action_args,
        project_name_override=project_name_override,
    )
