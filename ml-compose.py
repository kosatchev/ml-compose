#!/usr/bin/env python3
import datetime as dt
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import time
import fcntl
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required: apt install python3-yaml", file=sys.stderr)
    sys.exit(2)


ALLOWED_ACTIONS = {
    "up", "down", "ps", "logs", "restart", "stop", "start", "config",
    "gpu-status", "reconcile-locks",
}

SENSITIVE_MOUNTS = {
    "/",
    "/etc",
    "/root",
    "/boot",
    "/proc",
    "/sys",
    "/run",
    "/var/run",
    "/var/run/docker.sock",
}

ALLOWED_DEVICE_REGEX = [
    r"^/dev/nvidia\d+$",
    r"^/dev/nvidiactl$",
    r"^/dev/nvidia-uvm$",
    r"^/dev/nvidia-uvm-tools$",
    r"^/dev/nvidia-modeset$",
    r"^/dev/dri(/.*)?$",
]

ALLOWED_ABS_MOUNT_PREFIXES = [
    "/home",
    "/srv/ml/datasets",
    "/srv/ml/models",
    "/srv/ml/cache",
    "/srv/ml/users",
    "/tmp",
    "/var/tmp",
]

OWNER_LABEL = "ml.owner"
PROJECT_LABEL = "ml.project"
GPU_LABEL = "ml.gpu"

LOCK_DIR = Path("/var/lock/ml-gpu")
STATE_DIR = LOCK_DIR / "state"
GUARD_DIR = LOCK_DIR / "guard"

GUARD_LOCK_TIMEOUT = 15
LOCK_GRACE_SECONDS = 120


def eprint(*args):
    print(*args, file=sys.stderr)


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def iso_utc(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso_utc(value: str) -> dt.datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return dt.datetime.fromisoformat(value).astimezone(dt.timezone.utc)


def run(cmd, check=True, capture_output=False, text=True, env=None):
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture_output,
        text=text,
        env=env,
    )


def real_user():
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        return sudo_user
    return pwd.getpwuid(os.getuid()).pw_name


def user_home(username: str) -> Path:
    return Path(pwd.getpwnam(username).pw_dir)


def sanitize_project_name(name: str) -> str:
    name = name.lower()
    name = re.sub(r"[^a-z0-9_.-]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_.-")
    return name or "project"


def parse_gpu_arg(args):
    gpu_spec = None
    cleaned = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--gpu":
            if i + 1 >= len(args):
                raise SystemExit("ERROR: missing value after --gpu")
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


def compose_file_from_args(args):
    compose_file = None
    passthrough = []
    i = 0
    while i < len(args):
        if args[i] in ("-f", "--file"):
            if i + 1 >= len(args):
                raise SystemExit("ERROR: missing value after -f/--file")
            compose_file = args[i + 1]
            i += 2
            continue
        passthrough.append(args[i])
        i += 1

    if compose_file is None:
        for candidate in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
            if Path(candidate).exists():
                compose_file = candidate
                break

    if compose_file is None:
        raise SystemExit("ERROR: no compose file found in current directory")

    return compose_file, passthrough


def resolve_mount_source(src: str, working_dir: Path) -> Path:
    p = Path(src)
    if not p.is_absolute():
        p = (working_dir / p).resolve()
    else:
        p = p.resolve()
    return p


def is_under(path: Path, prefix: str) -> bool:
    try:
        path = path.resolve()
    except Exception:
        pass
    prefix_path = Path(prefix).resolve()
    return path == prefix_path or str(path).startswith(str(prefix_path) + "/")


def is_allowed_device(dev: str) -> bool:
    return any(re.match(rx, dev) for rx in ALLOWED_DEVICE_REGEX)


def extract_bind_source(volume):
    if isinstance(volume, str):
        parts = volume.split(":")
        if len(parts) >= 2:
            src = parts[0]
            if src:
                return src
        return None

    if isinstance(volume, dict):
        if volume.get("type") == "bind":
            return volume.get("source")
        src = volume.get("source")
        if src:
            return src

    return None


def is_bind_mount(volume):
    if isinstance(volume, str):
        parts = volume.split(":")
        if len(parts) >= 2:
            src = parts[0]
            return src.startswith("/") or src.startswith("./") or src.startswith("../") or src in (".", "..")
        return False

    if isinstance(volume, dict):
        if volume.get("type") == "bind":
            return True
        src = volume.get("source")
        if src:
            return src.startswith("/") or src.startswith("./") or src.startswith("../") or src in (".", "..")
    return False


def validate_service(service_name: str, service: dict, working_dir: Path, username: str):
    errors = []
    warnings = []

    if service.get("privileged") is True:
        errors.append(f"{service_name}: privileged=true is forbidden")

    if str(service.get("pid", "")).strip().lower() == "host":
        errors.append(f"{service_name}: pid=host is forbidden")

    if str(service.get("cgroup", "")).strip().lower() == "host":
        errors.append(f"{service_name}: cgroup=host is forbidden")

    if str(service.get("userns_mode", "")).strip().lower() == "host":
        errors.append(f"{service_name}: userns_mode=host is forbidden")

    if str(service.get("network_mode", "")).strip().lower() == "host":
        warnings.append(f"{service_name}: network_mode=host is risky")

    if str(service.get("ipc", "")).strip().lower() == "host":
        warnings.append(f"{service_name}: ipc=host is risky but sometimes needed in ML")

    if "cap_add" in service:
        warnings.append(f"{service_name}: cap_add is present")

    if "security_opt" in service:
        warnings.append(f"{service_name}: security_opt is present")

    user_value = service.get("user")
    if user_value in (None, "", "0", "0:0", "root"):
        warnings.append(f"{service_name}: container runs as root or user is unspecified")

    devices = service.get("devices", [])
    for dev in devices:
        host_dev = None
        if isinstance(dev, str):
            host_dev = dev.split(":")[0]
        elif isinstance(dev, dict):
            host_dev = dev.get("source") or dev.get("path_on_host")

        if host_dev and not is_allowed_device(host_dev):
            errors.append(f"{service_name}: device '{host_dev}' is not allowed")

    for vol in service.get("volumes", []):
        if not is_bind_mount(vol):
            continue

        src = extract_bind_source(vol)
        if not src:
            continue

        src_path = resolve_mount_source(src, working_dir)

        if str(src_path) in SENSITIVE_MOUNTS:
            errors.append(f"{service_name}: mount of '{src_path}' is forbidden")
            continue

        for sensitive in SENSITIVE_MOUNTS:
            if sensitive == "/":
                if str(src_path) == "/":
                    errors.append(f"{service_name}: mounting host root '/' is forbidden")
            else:
                if is_under(src_path, sensitive):
                    errors.append(f"{service_name}: mounting sensitive path '{src_path}' is forbidden")
                    break

        allowed = any(is_under(src_path, prefix) for prefix in ALLOWED_ABS_MOUNT_PREFIXES)
        if not allowed:
            warnings.append(f"{service_name}: bind mount '{src_path}' is outside allowed prefixes")

        home_prefix = Path("/home")
        if is_under(src_path, str(home_prefix)):
            owner_home = user_home(username).resolve()
            if not is_under(src_path, str(owner_home)):
                errors.append(f"{service_name}: mount of another user's home is forbidden: '{src_path}'")

    return errors, warnings


def add_labels_to_services(doc: dict, username: str, project_name: str, gpu_ids: list[str] | None):
    services = doc.get("services", {})
    gpu_value = ",".join(gpu_ids) if gpu_ids else None

    for _, svc in services.items():
        labels = svc.get("labels", {})
        if isinstance(labels, list):
            parsed = {}
            for item in labels:
                if isinstance(item, str) and "=" in item:
                    k, v = item.split("=", 1)
                    parsed[k] = v
            labels = parsed
        elif not isinstance(labels, dict):
            labels = {}

        labels[OWNER_LABEL] = username
        labels[PROJECT_LABEL] = project_name
        if gpu_value is not None:
            labels[GPU_LABEL] = gpu_value
        svc["labels"] = labels


def inject_gpu_env(doc: dict, gpu_ids: list[str]):
    gpu_value = ",".join(gpu_ids)
    services = doc.get("services", {})
    for _, svc in services.items():
        env = svc.get("environment", {})
        if isinstance(env, list):
            parsed = {}
            for item in env:
                if isinstance(item, str) and "=" in item:
                    k, v = item.split("=", 1)
                    parsed[k] = v
            env = parsed
        elif env is None:
            env = {}
        elif not isinstance(env, dict):
            env = {}

        env["CUDA_VISIBLE_DEVICES"] = gpu_value
        svc["environment"] = env


def validate_doc(doc: dict, working_dir: Path, username: str):
    errors = []
    warnings = []

    services = doc.get("services", {})
    if not isinstance(services, dict):
        errors.append("compose: 'services' is missing or invalid")
        return errors, warnings

    for service_name, service in services.items():
        if not isinstance(service, dict):
            errors.append(f"{service_name}: definition is invalid")
            continue
        e, w = validate_service(service_name, service, working_dir, username)
        errors.extend(e)
        warnings.extend(w)

    return errors, warnings


def docker_compose_config(compose_file: str):
    cmd = ["docker", "compose", "-f", compose_file, "config"]
    result = run(cmd, check=False, capture_output=True)
    if result.returncode != 0:
        eprint(result.stdout)
        eprint(result.stderr)
        raise SystemExit("ERROR: 'docker compose config' failed")
    return result.stdout


def save_temp_compose(content: dict) -> str:
    fd, path = tempfile.mkstemp(prefix="ml-compose-", suffix=".yml")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        yaml.safe_dump(content, f, sort_keys=False)
    return path


def ensure_tools():
    for tool in ("docker", "nvidia-smi"):
        if shutil.which(tool) is None:
            raise SystemExit(f"ERROR: required tool not found: {tool}")


def ensure_docker_access():
    result = run(["docker", "info"], check=False, capture_output=True)
    if result.returncode != 0:
        raise SystemExit("ERROR: docker daemon is not accessible")


def get_gpu_ids():
    result = run(["nvidia-smi", "-L"], check=False, capture_output=True)
    if result.returncode != 0:
        raise SystemExit("ERROR: failed to query GPUs via nvidia-smi -L")

    gpu_ids = []
    for line in result.stdout.splitlines():
        m = re.match(r"^GPU\s+(\d+):", line.strip())
        if m:
            gpu_ids.append(m.group(1))
    return sorted(gpu_ids, key=int)


def ensure_lock_dirs():
    for d in (LOCK_DIR, STATE_DIR, GUARD_DIR):
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o755)


def guard_file_path(gpu_id: str) -> Path:
    return GUARD_DIR / f"gpu{gpu_id}.guard"


def state_file_path(gpu_id: str) -> Path:
    return STATE_DIR / f"gpu{gpu_id}.json"


def project_exists(project_name: str) -> bool:
    result = run(
        [
            "docker", "ps", "-a",
            "--filter", f"label={PROJECT_LABEL}={project_name}",
            "--format", "{{.ID}}",
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


def project_is_running(project_name: str) -> bool:
    result = run(
        [
            "docker", "ps",
            "--filter", f"label={PROJECT_LABEL}={project_name}",
            "--format", "{{.ID}}",
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


def project_has_restarting(project_name: str) -> bool:
    result = run(
        [
            "docker", "ps", "-a",
            "--filter", f"label={PROJECT_LABEL}={project_name}",
            "--filter", "status=restarting",
            "--format", "{{.ID}}",
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


def read_state_lock(gpu_id: str):
    path = state_file_path(gpu_id)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception:
        return None


def write_state_locks(gpu_ids: list[str], username: str, project_name: str, compose_dir: str):
    created_at = now_utc()
    grace_until = created_at + dt.timedelta(seconds=LOCK_GRACE_SECONDS)

    for gpu_id in gpu_ids:
        path = state_file_path(gpu_id)
        data = {
            "gpu": gpu_id,
            "gpus": gpu_ids,
            "owner": username,
            "project": project_name,
            "compose_dir": compose_dir,
            "created_at": iso_utc(created_at),
            "grace_until": iso_utc(grace_until),
            "host": os.uname().nodename,
            "version": 3,
        }
        tmp_path = path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)


def update_state_locks_activated(gpu_ids: list[str], project_name: str):
    activated_at = iso_utc(now_utc())
    container_ids = get_project_container_ids(project_name)

    for gpu_id in gpu_ids:
        path = state_file_path(gpu_id)
        data = read_state_lock(gpu_id)
        if not data:
            continue
        if data.get("project") != project_name:
            continue
        data["activated_at"] = activated_at
        data["container_ids"] = container_ids
        tmp_path = path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)


def remove_state_locks_if_owned(gpu_ids: list[str], username: str, project_name: str):
    for gpu_id in gpu_ids:
        path = state_file_path(gpu_id)
        data = read_state_lock(gpu_id)
        if not data:
            continue
        if data.get("owner") == username and data.get("project") == project_name:
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def get_project_container_ids(project_name: str) -> list[str]:
    result = run(
        [
            "docker", "ps", "-a",
            "--filter", f"label={PROJECT_LABEL}={project_name}",
            "--format", "{{.ID}}",
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return []
    return [x.strip() for x in result.stdout.splitlines() if x.strip()]


def get_gpus_from_existing_project(project_name: str) -> list[str]:
    result = run(
        [
            "docker", "ps", "-a",
            "--filter", f"label={PROJECT_LABEL}={project_name}",
            "--format", "{{.Labels}}",
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return []

    for line in result.stdout.splitlines():
        m = re.search(rf"{re.escape(GPU_LABEL)}=([^,\"\s]+(?:,[^,\"\s]+)*)", line)
        if m:
            return [x.strip() for x in m.group(1).split(",") if x.strip()]
    return []


def acquire_guard_locks(gpu_ids: list[str], timeout_seconds: int = GUARD_LOCK_TIMEOUT):
    ensure_lock_dirs()
    fds = []
    start = time.monotonic()

    for gpu_id in sorted(gpu_ids, key=int):
        path = guard_file_path(gpu_id)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)

        acquired = False
        try:
            while time.monotonic() - start < timeout_seconds:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    fds.append(fd)
                    break
                except BlockingIOError:
                    time.sleep(0.2)

            if not acquired:
                os.close(fd)
                raise SystemExit(f"ERROR: timeout acquiring GPU guard lock for GPU {gpu_id}")
        except Exception:
            if not acquired:
                try:
                    os.close(fd)
                except Exception:
                    pass
            for held_fd in reversed(fds):
                try:
                    fcntl.flock(held_fd, fcntl.LOCK_UN)
                finally:
                    os.close(held_fd)
            raise

    return fds


def release_guard_locks(fds):
    for fd in reversed(fds):
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def state_lock_is_fresh(data: dict) -> bool:
    grace_until = data.get("grace_until")
    if not grace_until:
        return False
    try:
        return now_utc() < parse_iso_utc(grace_until)
    except Exception:
        return False


def state_lock_is_active(data: dict) -> bool:
    project = data.get("project")
    if not project:
        return False
    return project_is_running(project) or project_has_restarting(project)


def check_and_cleanup_state_locks_or_die(gpu_ids: list[str], username: str, project_name: str):
    for gpu_id in gpu_ids:
        data = read_state_lock(gpu_id)
        if not data:
            continue

        lock_owner = data.get("owner")
        lock_project = data.get("project")

        if lock_owner == username and lock_project == project_name:
            continue

        if state_lock_is_fresh(data):
            raise SystemExit(
                f"ERROR: GPU {gpu_id} is reserved by owner={lock_owner}, project={lock_project} "
                f"(within grace period)"
            )

        if state_lock_is_active(data):
            raise SystemExit(
                f"ERROR: GPU {gpu_id} is in use by owner={lock_owner}, project={lock_project}"
            )

        path = state_file_path(gpu_id)
        try:
            path.unlink()
            print(f"WARN: removed stale lock for GPU {gpu_id} (owner={lock_owner}, project={lock_project})")
        except FileNotFoundError:
            pass


def docker_compose_action(action: str, project_name: str, compose_file: str, extra_args):
    base = ["docker", "compose", "-p", project_name, "-f", compose_file]

    if action == "up":
        cmd = base + ["up", "-d"] + extra_args
    elif action in {"down", "restart", "stop", "start", "config"}:
        cmd = base + [action] + extra_args
    elif action == "logs":
        cmd = base + ["logs"] + extra_args
    elif action == "ps":
        cmd = base + ["ps"] + extra_args
    else:
        raise SystemExit(f"ERROR: unsupported action '{action}'")

    run(cmd)


def get_nvidia_smi_summary():
    result = run(
        [
            "nvidia-smi",
            "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
            "--format=csv,noheader,nounits",
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return {}

    info = {}
    for line in result.stdout.splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) != 5:
            continue
        gpu_id, name, mem_used, mem_total, util = parts
        info[gpu_id] = {
            "name": name,
            "memory_used_mb": mem_used,
            "memory_total_mb": mem_total,
            "util_percent": util,
        }
    return info


def cmd_gpu_status():
    ensure_lock_dirs()
    gpu_ids = get_gpu_ids()
    smi = get_nvidia_smi_summary()

    print("GPU STATUS")
    for gpu_id in gpu_ids:
        data = read_state_lock(gpu_id)
        smi_info = smi.get(gpu_id, {})
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

    for path in sorted(STATE_DIR.glob("gpu*.json")):
        m = re.match(r"gpu(\d+)\.json$", path.name)
        if not m:
            continue
        gpu_id = m.group(1)
        data = read_state_lock(gpu_id)
        if not data:
            try:
                path.unlink()
                removed += 1
                print(f"Removed unreadable lock: {path}")
            except FileNotFoundError:
                pass
            continue

        if state_lock_is_fresh(data):
            continue

        if state_lock_is_active(data):
            continue

        try:
            path.unlink()
            removed += 1
            print(f"Removed stale lock for GPU {gpu_id}: owner={data.get('owner')}, project={data.get('project')}")
        except FileNotFoundError:
            pass

    print(f"Reconcile complete. Removed locks: {removed}")


def usage():
    print(
        "Usage:\n"
        "  sudo ml-compose up --gpu <id[,id,...]|all> [docker compose args]\n"
        "  sudo ml-compose down\n"
        "  sudo ml-compose ps\n"
        "  sudo ml-compose logs [-f]\n"
        "  sudo ml-compose restart\n"
        "  sudo ml-compose stop\n"
        "  sudo ml-compose start\n"
        "  sudo ml-compose config\n"
        "  sudo ml-compose gpu-status\n"
        "  sudo ml-compose reconcile-locks\n"
    )


def main():
    ensure_tools()
    ensure_docker_access()

    if len(sys.argv) < 2:
        usage()
        sys.exit(2)

    action = sys.argv[1]
    if action not in ALLOWED_ACTIONS:
        usage()
        raise SystemExit(f"ERROR: unsupported action '{action}'")

    if action == "gpu-status":
        cmd_gpu_status()
        return

    if action == "reconcile-locks":
        cmd_reconcile_locks()
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

    gpu_spec, remaining_args = parse_gpu_arg(sys.argv[2:])
    compose_file, extra_args = compose_file_from_args(remaining_args)

    valid_gpu_ids = get_gpu_ids()
    requested_gpu_ids = parse_gpu_spec(gpu_spec, valid_gpu_ids)

    if action == "up" and not requested_gpu_ids:
        raise SystemExit("ERROR: 'up' requires --gpu <id[,id,...]|all>")

    if action != "up" and gpu_spec is not None:
        eprint("WARN: --gpu is ignored for this action")

    compose_path = (cwd / compose_file).resolve() if not Path(compose_file).is_absolute() else Path(compose_file).resolve()

    if not compose_path.exists():
        raise SystemExit(f"ERROR: compose file not found: {compose_path}")

    if not is_under(compose_path, str(cwd)):
        raise SystemExit("ERROR: compose file must be inside the current working directory")

    project_name = sanitize_project_name(f"{username}_{cwd.name}")

    rendered = docker_compose_config(str(compose_path))
    try:
        doc = yaml.safe_load(rendered)
    except Exception as ex:
        raise SystemExit(f"ERROR: failed to parse rendered compose config: {ex}")

    if not isinstance(doc, dict):
        raise SystemExit("ERROR: rendered compose config is invalid")

    errors, warnings = validate_doc(doc, cwd, username)

    if action == "up":
        inject_gpu_env(doc, requested_gpu_ids)
        add_labels_to_services(doc, username, project_name, requested_gpu_ids)
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
        if action == "up":
            guard_fds = acquire_guard_locks(requested_gpu_ids)
            check_and_cleanup_state_locks_or_die(requested_gpu_ids, username, project_name)
            write_state_locks(requested_gpu_ids, username, project_name, str(cwd))

            try:
                docker_compose_action("up", project_name, temp_compose, extra_args)
            except Exception:
                remove_state_locks_if_owned(requested_gpu_ids, username, project_name)
                raise

            update_state_locks_activated(requested_gpu_ids, project_name)
            print(f"Locked GPUs for project '{project_name}': {','.join(requested_gpu_ids)}")

        elif action == "down":
            project_gpu_ids_before = get_gpus_from_existing_project(project_name)
            if project_gpu_ids_before:
                guard_fds = acquire_guard_locks(project_gpu_ids_before)

            docker_compose_action(action, project_name, temp_compose, extra_args)

            if project_gpu_ids_before:
                remove_state_locks_if_owned(project_gpu_ids_before, username, project_name)

        else:
            docker_compose_action(action, project_name, temp_compose, extra_args)

    finally:
        if guard_fds:
            release_guard_locks(guard_fds)
        try:
            os.unlink(temp_compose)
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()