#!/usr/bin/env python3
import re
from pathlib import Path

import yaml

from common import is_under, resolve_mount_source, user_home

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
    r"^/dev/kfd$",
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

DEFAULT_POLICY = {
    "deny_privileged": True,
    "deny_docker_sock": True,
    "deny_pid_host": True,
    "deny_cgroup_host": True,
    "deny_userns_host": True,
    "warn_network_host": True,
    "warn_ipc_host": True,
    "warn_root_user": True,
    "warn_cap_add": True,
    "warn_security_opt": True,
    "warn_on_bind_outside_allowed_prefixes": True,
    "deny_sensitive_mounts": sorted(SENSITIVE_MOUNTS),
    "allow_device_exact": [
        "/dev/nvidiactl",
        "/dev/nvidia-uvm",
        "/dev/nvidia-uvm-tools",
        "/dev/nvidia-modeset",
        "/dev/kfd",
        "/dev/dri",
    ],
    "allow_device_regex": list(ALLOWED_DEVICE_REGEX),
    "allowed_abs_mount_prefixes": list(ALLOWED_ABS_MOUNT_PREFIXES),
    "forbid_other_user_homes": True,
}
def load_policy(policy_path: Path | None) -> dict:
    policy = dict(DEFAULT_POLICY)
    if policy_path is None:
        return policy

    try:
        with open(policy_path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
    except Exception as ex:
        raise SystemExit(f"ERROR: failed to load policy file '{policy_path}': {ex}")

    if not isinstance(loaded, dict):
        raise SystemExit(f"ERROR: policy file '{policy_path}' must contain a YAML mapping")

    unknown_keys = sorted(set(loaded) - set(DEFAULT_POLICY))
    if unknown_keys:
        raise SystemExit(
            f"ERROR: policy file '{policy_path}' contains unsupported keys: {', '.join(unknown_keys)}"
        )

    policy.update(loaded)
    return policy


def discover_policy_path(compose_path: Path, cwd: Path) -> Path | None:
    candidates = [
        compose_path.parent / "compose-policy.yml",
        cwd / "compose-policy.yml",
        Path(__file__).resolve().parent / "compose-policy.yml",
    ]
    seen = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists():
            return candidate
    return None


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


def validate_service(service_name: str, service: dict, working_dir: Path, username: str, policy: dict):
    errors = []
    warnings = []
    sensitive_mounts = set(policy.get("deny_sensitive_mounts", []))
    allowed_device_exact = {str(x) for x in policy.get("allow_device_exact", [])}
    allowed_mount_prefixes = list(policy.get("allowed_abs_mount_prefixes", []))
    allow_device_regex = list(policy.get("allow_device_regex", []))

    if policy.get("deny_privileged", True) and service.get("privileged") is True:
        errors.append(f"{service_name}: privileged=true is forbidden")

    if policy.get("deny_pid_host", True) and str(service.get("pid", "")).strip().lower() == "host":
        errors.append(f"{service_name}: pid=host is forbidden")

    if policy.get("deny_cgroup_host", True) and str(service.get("cgroup", "")).strip().lower() == "host":
        errors.append(f"{service_name}: cgroup=host is forbidden")

    if policy.get("deny_userns_host", True) and str(service.get("userns_mode", "")).strip().lower() == "host":
        errors.append(f"{service_name}: userns_mode=host is forbidden")

    if policy.get("warn_network_host", True) and str(service.get("network_mode", "")).strip().lower() == "host":
        warnings.append(f"{service_name}: network_mode=host is risky")

    if policy.get("warn_ipc_host", True) and str(service.get("ipc", "")).strip().lower() == "host":
        warnings.append(f"{service_name}: ipc=host is risky but sometimes needed in ML")

    if policy.get("warn_cap_add", True) and "cap_add" in service:
        warnings.append(f"{service_name}: cap_add is present")

    if policy.get("warn_security_opt", True) and "security_opt" in service:
        warnings.append(f"{service_name}: security_opt is present")

    user_value = service.get("user")
    if policy.get("warn_root_user", True) and user_value in (None, "", "0", "0:0", "root"):
        warnings.append(f"{service_name}: container runs as root or user is unspecified")

    devices = service.get("devices", [])
    for dev in devices:
        host_dev = None
        if isinstance(dev, str):
            host_dev = dev.split(":")[0]
        elif isinstance(dev, dict):
            host_dev = dev.get("source") or dev.get("path_on_host")

        if host_dev and host_dev not in allowed_device_exact and not any(re.match(rx, host_dev) for rx in allow_device_regex):
            errors.append(f"{service_name}: device '{host_dev}' is not allowed")

    for vol in service.get("volumes", []):
        if not is_bind_mount(vol):
            continue

        src = extract_bind_source(vol)
        if not src:
            continue

        src_path = resolve_mount_source(src, working_dir)

        if str(src_path) == "/var/run/docker.sock" and not policy.get("deny_docker_sock", True):
            pass
        elif str(src_path) in sensitive_mounts:
            errors.append(f"{service_name}: mount of '{src_path}' is forbidden")
            continue

        for sensitive in sensitive_mounts:
            if sensitive == "/":
                if str(src_path) == "/":
                    errors.append(f"{service_name}: mounting host root '/' is forbidden")
            else:
                if is_under(src_path, sensitive):
                    errors.append(f"{service_name}: mounting sensitive path '{src_path}' is forbidden")
                    break

        allowed = any(is_under(src_path, prefix) for prefix in allowed_mount_prefixes)
        if policy.get("warn_on_bind_outside_allowed_prefixes", True) and not allowed:
            warnings.append(f"{service_name}: bind mount '{src_path}' is outside allowed prefixes")

        home_prefix = Path("/home")
        if policy.get("forbid_other_user_homes", True) and is_under(src_path, str(home_prefix)):
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


def inject_gpu_env(doc: dict, gpu_env: dict[str, str]):
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

        env.update(gpu_env)
        svc["environment"] = env


def validate_doc(doc: dict, working_dir: Path, username: str, policy: dict):
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
        e, w = validate_service(service_name, service, working_dir, username, policy)
        errors.extend(e)
        warnings.extend(w)

    return errors, warnings
