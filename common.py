#!/usr/bin/env python3
import datetime as dt
import os
import pwd
import re
import subprocess
import sys
import traceback
from pathlib import Path


def eprint(*args):
    print(*args, file=sys.stderr)


def debug_enabled() -> bool:
    return os.environ.get("ML_COMPOSE_DEBUG", "").strip() == "1"


def print_cli_error(message: str, detail: str | None = None, hint: str | None = None, example: str | None = None):
    eprint(f"ERROR: {message}")
    if detail:
        eprint(f"DETAIL: {detail}")
    if hint:
        eprint(f"HINT: {hint}")
    if example:
        eprint(f"EXAMPLE: {example}")


def exit_cli_error(
    message: str,
    detail: str | None = None,
    hint: str | None = None,
    example: str | None = None,
    code: int = 1,
):
    print_cli_error(message, detail=detail, hint=hint, example=example)
    raise SystemExit(code)


def print_unexpected_error(ex: Exception):
    print_cli_error(
        "unexpected internal error",
        detail=str(ex) or ex.__class__.__name__,
        hint="rerun with ML_COMPOSE_DEBUG=1 or contact the administrator",
    )


def maybe_print_debug_traceback():
    if debug_enabled():
        traceback.print_exc()


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
    # Compose project names must stay within a conservative character set
    # that works across CLI validation paths: lowercase letters, digits,
    # hyphens, and underscores.
    name = re.sub(r"[^a-z0-9_-]+", "-", name)
    name = re.sub(r"-+", "-", name)
    name = re.sub(r"_+", "_", name).strip("_-")
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
