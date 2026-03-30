# ml-compose

`ml-compose` is a safer wrapper around `docker compose` for ML workloads.

It validates the rendered Compose config before launch, applies a local policy,
injects GPU visibility environment variables, adds ownership labels, and keeps
GPU lock/state files to reduce accidental conflicts between projects.

## What It Does

- validates `docker compose config` output instead of only raw YAML
- supports multiple Compose files via `-f/--file`
- supports GPU backends `auto`, `nvidia`, and `amd`
- injects GPU environment variables into services
- labels services with owner, project, and selected GPUs
- prevents simultaneous reuse of the same GPU through lock/state files
- loads policy from `compose-policy.yml` when present

## Requirements

- Python 3
- `docker`
- `PyYAML`
- `nvidia-smi` for NVIDIA hosts
- `/dev/kfd` and/or `/dev/dri/renderD*` for AMD hosts
- optional `rocm-smi` for richer AMD GPU status output

## Files

- `ml-compose.py`: main entrypoint and orchestration
- `compose_cli.py`: parsing of Compose CLI arguments
- `compose_runtime.py`: `docker compose config` and temp Compose handling
- `policy.py`: policy loading, validation, labels, GPU env injection
- `gpu_backend.py`: NVIDIA/AMD backend detection and GPU metadata
- `gpu_locks.py`: guard locks and state lock lifecycle
- `compose-policy.yml`: optional local policy file

## Usage

Basic example:

```bash
python3 ml-compose.py up --gpu 0 -f compose.yml
```

Typical commands:

```bash
python3 ml-compose.py up --gpu 0,1 -f compose.yml
python3 ml-compose.py up --gpu all --gpu-backend auto -f compose.yml
python3 ml-compose.py up --gpu 0 -p train-exp-01 -f compose.yml
python3 ml-compose.py down -p train-exp-01 -f compose.yml
python3 ml-compose.py ps -p train-exp-01 -f compose.yml
python3 ml-compose.py logs -f -p train-exp-01
python3 ml-compose.py gpu-status
python3 ml-compose.py reconcile-locks
```

By default, the project name is generated automatically from the current
working directory and user. You can override it with `-p/--project-name`.

## How Launch Works

1. Parses wrapper arguments such as `--gpu` and `--gpu-backend`.
2. Parses Compose arguments such as `-f`, `--env-file`, `--profile`, and `-p`.
3. Resolves the final config using `docker compose config`.
4. Loads policy, if available, and validates the rendered document.
5. For `up`, injects GPU environment variables and service labels.
6. Acquires GPU guard locks and writes state lock files.
7. Launches `docker compose`.
8. Marks locks as active after containers are up.

## Supported Actions

- `up`
- `down`
- `ps`
- `logs`
- `restart`
- `stop`
- `start`
- `config`
- `gpu-status`
- `reconcile-locks`

## GPU Behavior

Backend selection:

- `auto`: prefer NVIDIA if `nvidia-smi` is available, otherwise AMD if runtime devices are present
- `nvidia`: require `nvidia-smi`
- `amd`: require AMD runtime detection

Environment variables:

- NVIDIA: `CUDA_VISIBLE_DEVICES`
- AMD: `HIP_VISIBLE_DEVICES`, `ROCR_VISIBLE_DEVICES`

Internal GPU lock IDs are namespaced, for example:

- `nvidia:0`
- `amd:0`

This avoids collisions on mixed hosts.

## Policy

`ml-compose` looks for `compose-policy.yml` in this order:

1. next to the primary Compose file
2. in the current working directory
3. next to the Python modules

The current policy schema supports:

- `deny_privileged`
- `deny_docker_sock`
- `deny_pid_host`
- `deny_cgroup_host`
- `deny_userns_host`
- `warn_network_host`
- `warn_ipc_host`
- `warn_root_user`
- `warn_cap_add`
- `warn_security_opt`
- `warn_on_bind_outside_allowed_prefixes`
- `deny_sensitive_mounts`
- `allow_device_exact`
- `allow_device_regex`
- `allowed_abs_mount_prefixes`
- `forbid_other_user_homes`

Validation is performed against the rendered Compose document, so relative
paths, merged files, and Compose expansion are checked in their resolved form.

Without `compose-policy.yml`, `ml-compose` still works. In that mode it uses:

- permissive built-in defaults for boolean policy flags
- permissive fallback mount/device rules with broad host access

In practice, no-config mode mainly keeps:

- Compose rendering and validation flow
- GPU environment injection
- owner/project/GPU labels
- GPU lock and state management

Policy enforcement is intentionally weak in this mode, so use
`compose-policy.yml` when you want real mount/device restrictions.

When `compose-policy.yml` is present:

- boolean flags still get code defaults if omitted
- mount/device list rules must be provided by the YAML file
- the YAML file becomes the effective source of truth for those list-based rules

## Labels and Locks

Services are labeled with:

- `ml.owner`
- `ml.project`
- `ml.gpu`

GPU coordination uses two kinds of files under `/var/lock/ml-gpu`:

- `guard/`: short-lived file locks during state transitions
- `state/`: JSON files describing current ownership and activation state

`reconcile-locks` removes unreadable or stale state files that are no longer
fresh and no longer backed by running containers.

## Notes

- `up` requires `--gpu`
- `--gpu` and `--gpu-backend` are ignored for non-`up` actions except `gpu-status`
- Compose files must be inside the current working directory
- The working directory must be inside the current user's home or `/srv/ml/users/<user>`

## Example Policy

See [compose-policy.yml](compose-policy.yml).
