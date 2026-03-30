#!/usr/bin/env python3
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from common import run

GPU_BACKENDS = {"auto", "nvidia", "amd"}


@dataclass(frozen=True)
class GpuSelection:
    backend: str
    visible_ids: list[str]
    lock_ids: list[str]
    env: dict[str, str]


def parse_gpu_backend_arg(args):
    backend = "auto"
    cleaned = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--gpu-backend":
            if i + 1 >= len(args):
                raise SystemExit("ERROR: missing value after --gpu-backend")
            backend = args[i + 1].strip().lower()
            i += 2
            continue
        if arg.startswith("--gpu-backend="):
            backend = arg.split("=", 1)[1].strip().lower()
            i += 1
            continue
        cleaned.append(arg)
        i += 1

    if backend not in GPU_BACKENDS:
        raise SystemExit(
            f"ERROR: unsupported GPU backend '{backend}'. Use one of: auto, nvidia, amd"
        )

    return backend, cleaned


def detect_gpu_backend(preferred: str = "auto") -> str:
    if preferred != "auto":
        if preferred == "nvidia" and shutil.which("nvidia-smi") is None:
            raise SystemExit("ERROR: GPU backend 'nvidia' requested but nvidia-smi is not available")
        if preferred == "amd" and not has_amd_runtime():
            raise SystemExit("ERROR: GPU backend 'amd' requested but AMD GPU runtime was not detected")
        return preferred

    if shutil.which("nvidia-smi") is not None:
        return "nvidia"
    if has_amd_runtime():
        return "amd"
    return "none"


def has_amd_runtime() -> bool:
    return Path("/dev/kfd").exists() or bool(list_render_nodes())


def ensure_backend_tools(backend: str):
    if backend == "nvidia" and shutil.which("nvidia-smi") is None:
        raise SystemExit("ERROR: required tool not found for NVIDIA backend: nvidia-smi")


def list_render_nodes() -> list[Path]:
    return sorted(Path("/dev/dri").glob("renderD*"))


def get_gpu_ids(backend: str) -> list[str]:
    if backend == "nvidia":
        result = run(["nvidia-smi", "-L"], check=False, capture_output=True)
        if result.returncode != 0:
            raise SystemExit("ERROR: failed to query GPUs via nvidia-smi -L")

        gpu_ids = []
        for line in result.stdout.splitlines():
            match = re.match(r"^GPU\s+(\d+):", line.strip())
            if match:
                gpu_ids.append(match.group(1))
        return sorted(gpu_ids, key=int)

    if backend == "amd":
        render_nodes = list_render_nodes()
        if not render_nodes:
            raise SystemExit("ERROR: failed to detect AMD GPUs via /dev/dri/renderD*")
        return [str(idx) for idx, _ in enumerate(render_nodes)]

    raise SystemExit("ERROR: no supported GPU backend detected on this host")


def namespaced_gpu_ids(backend: str, gpu_ids: list[str]) -> list[str]:
    return [f"{backend}:{gpu_id}" for gpu_id in gpu_ids]


def build_gpu_selection(backend: str, visible_ids: list[str]) -> GpuSelection:
    # User-facing GPU IDs stay simple (`0,1`), while internal lock IDs are
    # backend-namespaced (`nvidia:0`, `amd:0`) to avoid mixed-host collisions.
    return GpuSelection(
        backend=backend,
        visible_ids=list(visible_ids),
        lock_ids=namespaced_gpu_ids(backend, visible_ids),
        env=get_gpu_env(backend, visible_ids),
    )


def get_gpu_env(backend: str, gpu_ids: list[str]) -> dict[str, str]:
    value = ",".join(gpu_ids)
    if backend == "nvidia":
        return {"CUDA_VISIBLE_DEVICES": value}
    if backend == "amd":
        return {
            "HIP_VISIBLE_DEVICES": value,
            "ROCR_VISIBLE_DEVICES": value,
        }
    return {}


def get_gpu_summary(backend: str) -> dict[str, dict[str, str]]:
    if backend == "nvidia":
        return get_nvidia_smi_summary()
    if backend == "amd":
        return get_amd_summary()
    return {}


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


def get_amd_summary() -> dict[str, dict[str, str]]:
    summary = {}
    render_nodes = list_render_nodes()
    rocm_smi = shutil.which("rocm-smi")
    rocm_info = {}
    if rocm_smi is not None:
        rocm_info = get_rocm_smi_summary(rocm_smi)

    for idx, render_node in enumerate(render_nodes):
        gpu_id = str(idx)
        summary[gpu_id] = {
            "name": rocm_info.get(gpu_id, {}).get("name", render_node.name),
            "memory_used_mb": rocm_info.get(gpu_id, {}).get("memory_used_mb", "?"),
            "memory_total_mb": rocm_info.get(gpu_id, {}).get("memory_total_mb", "?"),
            "util_percent": rocm_info.get(gpu_id, {}).get("util_percent", "?"),
        }
    return summary


def get_rocm_smi_summary(rocm_smi_path: str) -> dict[str, dict[str, str]]:
    result = run(
        [rocm_smi_path, "--showproductname", "--showuse", "--showmemuse", "--json"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return {}

    try:
        payload = json.loads(result.stdout)
    except Exception:
        return {}

    summary = {}
    card_keys = sorted(k for k in payload.keys() if re.match(r"card\d+$", k))
    for idx, card_key in enumerate(card_keys):
        card = payload.get(card_key, {})
        values = next(iter(card.values()), {}) if isinstance(card, dict) and card else {}
        summary[str(idx)] = {
            "name": str(values.get("Card SKU", values.get("Device Name", card_key))),
            "memory_used_mb": bytes_to_mb_str(values.get("VRAM Total Used Memory (B)")),
            "memory_total_mb": bytes_to_mb_str(values.get("VRAM Total Memory (B)")),
            "util_percent": str(values.get("GPU use (%)", "?")),
        }
    return summary


def bytes_to_mb_str(value) -> str:
    try:
        return str(int(value) // (1024 * 1024))
    except Exception:
        return "?"
