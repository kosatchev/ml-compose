#!/usr/bin/env python3
import datetime as dt
import fcntl
import json
import os
import re
import time
from pathlib import Path

from common import iso_utc, now_utc, parse_iso_utc, run
from policy import GPU_LABEL, PROJECT_LABEL

APP_DIR = Path(__file__).resolve().parent
LOCK_DIR = APP_DIR / "lock"
STATE_DIR = LOCK_DIR / "state"
GUARD_DIR = LOCK_DIR / "guard"

GUARD_LOCK_TIMEOUT = 15
LOCK_GRACE_SECONDS = 120


def ensure_lock_dirs():
    for d in (LOCK_DIR, STATE_DIR, GUARD_DIR):
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o755)


def guard_file_path(gpu_id: str) -> Path:
    return GUARD_DIR / f"{gpu_file_stem(gpu_id)}.guard"


def state_file_path(gpu_id: str) -> Path:
    return STATE_DIR / f"{gpu_file_stem(gpu_id)}.json"


def gpu_file_stem(gpu_id: str) -> str:
    return f"gpu-{gpu_id.replace(':', '__')}"


def project_is_running(project_name: str) -> bool | None:
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
        return None
    return bool(result.stdout.strip())


def project_has_restarting(project_name: str) -> bool | None:
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
        return None
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


def write_state_locks(gpu_ids: list[str], username: str, project_name: str, compose_dir: str):
    created_at = now_utc()
    grace_until = created_at + dt.timedelta(seconds=LOCK_GRACE_SECONDS)

    for gpu_id in gpu_ids:
        path = state_file_path(gpu_id)
        # State files are the durable record of GPU ownership between compose
        # actions; they are separate from short-lived guard file locks.
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

    # Always lock GPUs in sorted order so concurrent processes do not deadlock
    # while trying to acquire overlapping GPU sets.
    for gpu_id in sorted(gpu_ids):
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


def state_lock_is_active(data: dict) -> bool | None:
    project = data.get("project")
    if not project:
        return False
    is_running = project_is_running(project)
    has_restarting = project_has_restarting(project)
    if is_running is None or has_restarting is None:
        return None
    return is_running or has_restarting


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

        is_active = state_lock_is_active(data)
        if is_active is None:
            raise SystemExit(
                f"ERROR: unable to verify whether GPU {gpu_id} is still in use by "
                f"owner={lock_owner}, project={lock_project}"
            )

        if is_active:
            raise SystemExit(
                f"ERROR: GPU {gpu_id} is in use by owner={lock_owner}, project={lock_project}"
            )

        path = state_file_path(gpu_id)
        try:
            path.unlink()
            print(f"WARN: removed stale lock for GPU {gpu_id} (owner={lock_owner}, project={lock_project})")
        except FileNotFoundError:
            pass
