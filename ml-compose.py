#!/usr/bin/env python3
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required: apt install python3-yaml", file=sys.stderr)
    sys.exit(2)

from common import eprint, is_under, real_user, run, sanitize_project_name, user_home
from compose_cli import parse_compose_cli_args, strip_compose_global_args
from compose_runtime import docker_compose_config, save_temp_compose
from gpu_locks import (
    STATE_DIR,
    acquire_guard_locks,
    check_and_cleanup_state_locks_or_die,
    ensure_lock_dirs,
    get_gpus_from_existing_project,
    read_state_lock,
    release_guard_locks,
    remove_state_locks_if_owned,
    state_lock_is_active,
    state_lock_is_fresh,
    update_state_locks_activated,
    write_state_locks,
)
from gpu_backend import (
    build_gpu_selection,
    detect_gpu_backend,
    ensure_backend_tools,
    get_gpu_ids,
    get_gpu_summary,
    parse_gpu_backend_arg,
)
from policy import (
    discover_policy_path,
    add_labels_to_services,
    inject_gpu_env,
    load_policy,
    validate_doc,
)

ALLOWED_ACTIONS = {
    "up", "build", "pull", "down", "ps", "images", "logs", "restart", "stop", "start", "config",
    "gpu-status", "reconcile-locks",
}


def parse_gpu_arg(args):
    gpu_spec = None
    cleaned = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-g", "--gpu"):
            if i + 1 >= len(args):
                raise SystemExit(f"ERROR: missing value after {arg}")
            gpu_spec = args[i + 1]
            i += 2
            continue
        if arg.startswith("--gpu="):
            gpu_spec = arg.split("=", 1)[1]
            i += 1
            continue
        cleaned.append(arg)
        i += 1
    return gpu_spec, cleaned


def parse_gpu_spec(gpu_spec: str | None, valid_gpu_ids: list[str]) -> list[str]:
    if gpu_spec is None:
        return []

    gpu_spec = gpu_spec.strip().lower()
    if gpu_spec == "none":
        return []
    if gpu_spec == "all":
        return sorted(valid_gpu_ids, key=int)

    parts = [x.strip() for x in gpu_spec.split(",") if x.strip()]
    if not parts:
        raise SystemExit("ERROR: empty GPU list")

    result = []
    seen = set()

    for p in parts:
        if not re.fullmatch(r"\d+", p):
            raise SystemExit(f"ERROR: invalid GPU id '{p}'")
        if p not in valid_gpu_ids:
            raise SystemExit(
                f"ERROR: GPU '{p}' not found. Available GPUs: {', '.join(valid_gpu_ids)}"
            )
        if p not in seen:
            seen.add(p)
            result.append(p)

    return sorted(result, key=int)


def ensure_tools():
    for tool in ("docker",):
        if shutil.which(tool) is None:
            raise SystemExit(f"ERROR: required tool not found: {tool}")


def ensure_docker_access():
    result = run(["docker", "info"], check=False, capture_output=True)
    if result.returncode != 0:
        raise SystemExit("ERROR: docker daemon is not accessible")


def docker_compose_action(action: str, project_name: str, compose_files: list[str], compose_global_args: list[str], extra_args):
    # Always force a concrete project name so labels, compose state, and GPU
    # lock ownership all refer to the same namespace.
    base = ["docker", "compose", "-p", project_name]
    for arg in compose_global_args:
        base.append(arg)
    for compose_file in compose_files:
        base.extend(["-f", compose_file])

    if action == "up":
        cmd = base + ["up", "-d"] + extra_args
    elif action in {"build", "pull", "down", "restart", "stop", "start", "config"}:
        cmd = base + [action] + extra_args
    elif action == "logs":
        cmd = base + ["logs"] + extra_args
    elif action == "ps":
        cmd = base + ["ps"] + extra_args
    elif action == "images":
        cmd = base + ["images"] + extra_args
    else:
        raise SystemExit(f"ERROR: unsupported action '{action}'")

    run(cmd)


def is_global_ps_action(args: list[str]) -> bool:
    return "-a" in args or "--all" in args


def ensure_no_compose_specific_args_for_global_ps(args: list[str]):
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"-p", "--project-name"}:
            raise SystemExit("ERROR: global 'ps -a' does not support compose-specific -f/--file or -p/--project-name")
        if arg in {"-f", "--file"}:
            if i + 1 < len(args):
                i += 2
                continue
            raise SystemExit("ERROR: global 'ps -a' does not support compose file selection via -f/--file")
        if arg.startswith("--file=") or arg.startswith("--project-name="):
            raise SystemExit("ERROR: global 'ps -a' does not support compose-specific -f/--file or -p/--project-name")
        i += 1


def cmd_global_ps(args: list[str]):
    run(["docker", "ps"] + args)


def is_global_images_action(args: list[str]) -> bool:
    return "-a" in args or "--all" in args


def ensure_no_compose_specific_args_for_global_images(args: list[str]):
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"-p", "--project-name"}:
            raise SystemExit("ERROR: global 'images -a' does not support compose-specific -f/--file or -p/--project-name")
        if arg in {"-f", "--file"}:
            if i + 1 < len(args):
                i += 2
                continue
            raise SystemExit("ERROR: global 'images -a' does not support compose file selection via -f/--file")
        if arg.startswith("--file=") or arg.startswith("--project-name="):
            raise SystemExit("ERROR: global 'images -a' does not support compose-specific -f/--file or -p/--project-name")
        i += 1


def cmd_global_images(args: list[str]):
    run(["docker", "images"] + args)


def cmd_gpu_status(gpu_backend: str):
    ensure_lock_dirs()
    gpu_selection = build_gpu_selection(gpu_backend, get_gpu_ids(gpu_backend))
    summary = get_gpu_summary(gpu_backend)

    print(f"GPU STATUS ({gpu_backend})")
    for gpu_id, lock_id in zip(gpu_selection.visible_ids, gpu_selection.lock_ids):
        data = read_state_lock(lock_id)
        smi_info = summary.get(gpu_id, {})
        line = (
            f"GPU {gpu_id}: "
            f"name={smi_info.get('name', '?')}, "
            f"util={smi_info.get('util_percent', '?')}%, "
            f"mem={smi_info.get('memory_used_mb', '?')}/{smi_info.get('memory_total_mb', '?')} MB"
        )
        print(line)

        if data:
            project = data.get("project")
            owner = data.get("owner")
            fresh = state_lock_is_fresh(data)
            active = state_lock_is_active(data)
            print(
                f"  lock: owner={owner}, project={project}, fresh={fresh}, active={active}, "
                f"created_at={data.get('created_at')}, grace_until={data.get('grace_until')}"
            )
        else:
            print("  lock: free")


def cmd_reconcile_locks():
    ensure_lock_dirs()
    removed = 0

    # Reconcile only state files; guard locks are transient file locks and do
    # not need manual cleanup.
    for path in sorted(STATE_DIR.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = None

        if not data:
            try:
                path.unlink()
                removed += 1
                print(f"Removed unreadable lock: {path}")
            except FileNotFoundError:
                pass
            continue

        gpu_id = data.get("gpu", path.stem)
        if state_lock_is_fresh(data):
            continue

        is_active = state_lock_is_active(data)
        if is_active is None:
            raise SystemExit(
                f"ERROR: unable to verify whether GPU {gpu_id} is still in use by "
                f"owner={data.get('owner')}, project={data.get('project')}"
            )

        if is_active:
            continue

        try:
            path.unlink()
            removed += 1
            print(
                f"Removed stale lock for GPU {gpu_id}: "
                f"owner={data.get('owner')}, project={data.get('project')}"
            )
        except FileNotFoundError:
            pass

    print(f"Reconcile complete. Removed locks: {removed}")


def usage():
    print(
        "Usage:\n"
        "  sudo ml-compose up [-g|--gpu <none|id[,id,...]|all>] [-G|--gpu-backend auto|nvidia|amd] [-p NAME] [docker compose args]\n"
        "  sudo ml-compose build [-p NAME] [docker compose build args]\n"
        "  sudo ml-compose pull [-p NAME] [docker compose pull args]\n"
        "  sudo ml-compose down [-p NAME]\n"
        "  sudo ml-compose ps [-p NAME]\n"
        "  sudo ml-compose ps -a [docker ps args]\n"
        "  sudo ml-compose images [-p NAME]\n"
        "  sudo ml-compose images -a [docker images args]\n"
        "  sudo ml-compose logs [-f] [-p NAME]\n"
        "  sudo ml-compose restart [-p NAME]\n"
        "  sudo ml-compose stop [-p NAME]\n"
        "  sudo ml-compose start [-p NAME]\n"
        "  sudo ml-compose config [-p NAME]\n"
        "  sudo ml-compose gpu-status\n"
        "  sudo ml-compose reconcile-locks\n"
        "\n"
        "By default the project name is generated automatically; use -p/--project-name to override it.\n"
    )


def main():
    ensure_tools()

    if len(sys.argv) < 2:
        usage()
        sys.exit(2)

    action = sys.argv[1]
    if action not in ALLOWED_ACTIONS:
        usage()
        raise SystemExit(f"ERROR: unsupported action '{action}'")

    if action in {"up", "build", "pull", "down", "ps", "images", "logs", "restart", "stop", "start", "config"}:
        ensure_docker_access()

    raw_args = sys.argv[2:]
    gpu_backend_pref, raw_args = parse_gpu_backend_arg(raw_args)
    raw_gpu_spec, _ = parse_gpu_arg(raw_args)
    explicit_no_gpu = raw_gpu_spec is not None and raw_gpu_spec.strip().lower() == "none"
    gpu_backend = detect_gpu_backend(gpu_backend_pref) if action == "gpu-status" or (action == "up" and raw_gpu_spec is not None and not explicit_no_gpu) else "none"
    if gpu_backend in {"nvidia", "amd"}:
        ensure_backend_tools(gpu_backend)

    if action == "gpu-status":
        if gpu_backend == "none":
            raise SystemExit("ERROR: no supported GPU backend detected on this host")
        cmd_gpu_status(gpu_backend)
        return

    if action == "reconcile-locks":
        ensure_docker_access()
        cmd_reconcile_locks()
        return

    if action == "ps" and is_global_ps_action(raw_args):
        gpu_spec, global_ps_args = parse_gpu_arg(raw_args)
        global_ps_args, removed_compose_args = strip_compose_global_args(global_ps_args)
        if gpu_spec:
            eprint("WARN: --gpu is ignored for this action")
        if gpu_backend_pref != "auto":
            eprint("WARN: --gpu-backend is ignored for this action")
        if removed_compose_args:
            eprint("WARN: compose-global arguments are ignored for this action")
        ensure_no_compose_specific_args_for_global_ps(global_ps_args)
        cmd_global_ps(global_ps_args)
        return

    if action == "images" and is_global_images_action(raw_args):
        gpu_spec, global_images_args = parse_gpu_arg(raw_args)
        global_images_args, removed_compose_args = strip_compose_global_args(global_images_args)
        if gpu_spec:
            eprint("WARN: --gpu is ignored for this action")
        if gpu_backend_pref != "auto":
            eprint("WARN: --gpu-backend is ignored for this action")
        if removed_compose_args:
            eprint("WARN: compose-global arguments are ignored for this action")
        ensure_no_compose_specific_args_for_global_images(global_images_args)
        cmd_global_images(global_images_args)
        return

    username = real_user()
    cwd = Path.cwd().resolve()
    user_home_dir = user_home(username).resolve()

    allowed_workdirs = [
        user_home_dir,
        Path(f"/srv/ml/users/{username}").resolve(),
    ]
    if not any(is_under(cwd, str(p)) for p in allowed_workdirs):
        raise SystemExit(
            f"ERROR: working directory '{cwd}' is not allowed; use your home or /srv/ml/users/{username}"
        )

    gpu_spec, remaining_args = parse_gpu_arg(raw_args)
    compose_cli = parse_compose_cli_args(remaining_args)
    compose_files = compose_cli.compose_files
    compose_global_args = compose_cli.compose_global_args
    extra_args = compose_cli.action_args
    project_name_override = compose_cli.project_name_override

    up_gpu_selection = None
    if action == "up":
        if gpu_spec is not None and gpu_spec.strip().lower() != "none" and gpu_backend == "none":
            raise SystemExit("ERROR: no supported GPU backend detected on this host")

        if gpu_spec is not None:
            valid_gpu_ids = get_gpu_ids(gpu_backend)
            requested_gpu_ids = parse_gpu_spec(gpu_spec, valid_gpu_ids)
            if requested_gpu_ids:
                up_gpu_selection = build_gpu_selection(gpu_backend, requested_gpu_ids)

    if action != "up" and gpu_spec is not None:
        eprint("WARN: --gpu is ignored for this action")
    if action != "up" and gpu_backend_pref != "auto":
        eprint("WARN: --gpu-backend is ignored for this action")
    if action == "up" and gpu_spec is None and gpu_backend_pref != "auto":
        eprint("WARN: --gpu-backend is ignored unless --gpu is also set")
    if action == "up" and gpu_spec is not None and gpu_spec.strip().lower() == "none" and gpu_backend_pref != "auto":
        eprint("WARN: --gpu-backend is ignored when --gpu none is used")

    compose_paths = []
    for compose_file in compose_files:
        compose_path = (cwd / compose_file).resolve() if not Path(compose_file).is_absolute() else Path(compose_file).resolve()
        if not compose_path.exists():
            raise SystemExit(f"ERROR: compose file not found: {compose_path}")
        if not is_under(compose_path, str(cwd)):
            raise SystemExit(f"ERROR: compose file must be inside the current working directory: {compose_path}")
        compose_paths.append(compose_path)

    base_compose_path = compose_paths[0]

    policy_path = discover_policy_path(base_compose_path, cwd)
    policy = load_policy(policy_path)
    if policy_path is not None:
        print(f"POLICY: {policy_path}")

    if project_name_override:
        project_name = sanitize_project_name(project_name_override)
    else:
        # Default project names stay deterministic for the same working tree,
        # but avoid collisions between similarly named directories.
        project_suffix = hashlib.sha1(str(cwd).encode("utf-8")).hexdigest()[:8]
        project_name = sanitize_project_name(f"{username}_{cwd.name}_{project_suffix}")

    # Validate the fully rendered Compose model rather than raw YAML so merges,
    # env expansion, and multi-file overrides are checked as Docker sees them.
    rendered = docker_compose_config(
        [str(path) for path in compose_paths],
        compose_global_args,
        project_name=project_name,
    )
    try:
        doc = yaml.safe_load(rendered)
    except Exception as ex:
        raise SystemExit(f"ERROR: failed to parse rendered compose config: {ex}")

    if not isinstance(doc, dict):
        raise SystemExit("ERROR: rendered compose config is invalid")

    errors, warnings = validate_doc(doc, base_compose_path.parent, username, policy)

    if action == "up":
        if up_gpu_selection is not None:
            inject_gpu_env(doc, up_gpu_selection.env)
            add_labels_to_services(doc, username, project_name, up_gpu_selection.lock_ids)
        else:
            add_labels_to_services(doc, username, project_name, [])
    else:
        existing_gpu_ids = get_gpus_from_existing_project(project_name)
        add_labels_to_services(doc, username, project_name, existing_gpu_ids)

    if warnings:
        print("WARNINGS:")
        for w in warnings:
            print(f"  - {w}")

    if errors:
        print("ERRORS:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    temp_compose = save_temp_compose(doc)
    guard_fds = []

    try:
        if action == "up" and up_gpu_selection is not None:
            # Guard locks serialize state transitions for the same GPU set
            # before we inspect or modify persistent state files.
            guard_fds = acquire_guard_locks(up_gpu_selection.lock_ids)
            check_and_cleanup_state_locks_or_die(up_gpu_selection.lock_ids, username, project_name)
            write_state_locks(up_gpu_selection.lock_ids, username, project_name, str(cwd))

            try:
                docker_compose_action("up", project_name, [temp_compose], compose_global_args, extra_args)
            except Exception:
                remove_state_locks_if_owned(up_gpu_selection.lock_ids, username, project_name)
                raise

            update_state_locks_activated(up_gpu_selection.lock_ids, project_name)
            print(f"Locked GPUs for project '{project_name}': {','.join(up_gpu_selection.visible_ids)}")

        elif action == "up":
            docker_compose_action("up", project_name, [temp_compose], compose_global_args, extra_args)

        elif action == "down":
            project_gpu_ids_before = get_gpus_from_existing_project(project_name)
            if project_gpu_ids_before:
                guard_fds = acquire_guard_locks(project_gpu_ids_before)

            docker_compose_action(action, project_name, [temp_compose], compose_global_args, extra_args)

            if project_gpu_ids_before:
                remove_state_locks_if_owned(project_gpu_ids_before, username, project_name)

        else:
            docker_compose_action(action, project_name, [temp_compose], compose_global_args, extra_args)

    finally:
        if guard_fds:
            release_guard_locks(guard_fds)
        try:
            Path(temp_compose).unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
