#!/usr/bin/env python3
import datetime as dt
import os
import pwd
import re
import subprocess
import sys
from pathlib import Path


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
