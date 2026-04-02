# ml-compose

Russian version: [README.ru.md](README.ru.md)  
Administrator guide: [ADMIN.md](ADMIN.md)  
Russian administrator guide: [ADMIN.ru.md](ADMIN.ru.md)

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

## Recommended Install Layout

For a self-contained system install:

- application directory: `/opt/ml-compose/`
- launcher: `/usr/local/bin/ml-compose`
- policy file: `/opt/ml-compose/compose-policy.yml`
- lock directories:
  - `/opt/ml-compose/lock/state/`
  - `/opt/ml-compose/lock/guard/`

The application directory should be owned by `root:root` and not be writable
by regular users.

## Usage

Basic example:

```bash
python3 ml-compose.py up
```

If `-f/--file` is not provided, `ml-compose` looks for a Compose file in the
current directory using the usual order:

- `docker-compose.yml`
- `docker-compose.yaml`
- `compose.yml`
- `compose.yaml`

### Typical commands

Start a simple project without GPU allocation or GPU locking:

```bash
python3 ml-compose.py up
```

Start explicitly in no-GPU mode:

```bash
python3 ml-compose.py up -g none
```

Start one GPU from the default Compose file in the current directory:

```bash
python3 ml-compose.py up --gpu 0
```

Start several GPUs:

```bash
python3 ml-compose.py up --gpu 0,1
```

Take all GPUs from the selected backend:

```bash
python3 ml-compose.py up --gpu all --gpu-backend auto
```

If `--gpu` is omitted, or if you pass `-g none`, `up` behaves like a normal
Compose launch: no GPU environment variables are injected and no GPU locks are
created.

Wrapper-specific short aliases:

- `-g` for `--gpu`
- `-G` for `--gpu-backend`

Use a custom project name so later `down`, `ps`, and `logs` commands refer to
the same deployment explicitly:

```bash
python3 ml-compose.py up --gpu 0 -p train-exp-01 -f compose.yml
```

Use several Compose files, for example base plus override:

```bash
python3 ml-compose.py up --gpu 0 -f compose.yml -f compose.override.yml
```

Build images without starting containers:

```bash
python3 ml-compose.py build
```

Build without using Docker build cache:

```bash
python3 ml-compose.py build --no-cache
```

Pull the latest image versions without starting containers:

```bash
python3 ml-compose.py pull
```

Stop a named project:

```bash
python3 ml-compose.py down -p train-exp-01 -f compose.yml
```

See container state for a named project:

```bash
python3 ml-compose.py ps -p train-exp-01 -f compose.yml
```

See all containers on the host across all users:

```bash
python3 ml-compose.py ps -a
```

See which images are associated with the project:

```bash
python3 ml-compose.py images -p train-exp-01 -f compose.yml
```

See all images on the host:

```bash
python3 ml-compose.py images -a
```

Follow logs for a named project:

```bash
python3 ml-compose.py logs -f -p train-exp-01
```

See which GPUs are visible and whether they are currently locked:

```bash
python3 ml-compose.py gpu-status
```

Clean up stale GPU state locks that no longer belong to a live project:

```bash
python3 ml-compose.py reconcile-locks
```

By default, the project name is generated automatically from the current
working directory and user. You can override it with `-p/--project-name`.

## How Launch Works

1. Parses wrapper arguments such as `--gpu` and `--gpu-backend`.
2. Parses Compose arguments such as `-f`, `--env-file`, `--profile`, and `-p`.
3. Resolves the final config using `docker compose config`.
4. Loads policy, if available, and validates the rendered document.
5. For `up`, always injects service labels; GPU environment variables are injected only when `-g/--gpu` selects GPUs.
6. Acquires GPU guard locks and writes state lock files only when `-g/--gpu` selects GPUs.
7. Launches `docker compose`.
8. Marks locks as active after containers are up only for GPU-locked launches.

## Supported Actions

- `up`
- `build`
- `pull`
- `down`
- `ps`
  `ps -a` is special: it runs global `docker ps -a` and does not require a Compose file.
- `images`
  `images -a` is special: it runs global `docker images` and does not require a Compose file.
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

User-facing GPU indices are backend-local. For example, `--gpu 0` means
`NVIDIA GPU 0` with `--gpu-backend nvidia`, and `AMD GPU 0` with
`--gpu-backend amd`.

Internal GPU lock IDs are namespaced, for example:

- `nvidia:0`
- `amd:0`

This avoids collisions on mixed hosts.

## Policy

`ml-compose` looks for `compose-policy.yml` in this order:

1. next to the Python modules
2. next to the primary Compose file
3. in the current working directory

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

GPU coordination uses two kinds of files under `/opt/ml-compose/lock` in the
recommended installation layout:

- `guard/`: short-lived file locks during state transitions
- `state/`: JSON files describing current ownership and activation state

`reconcile-locks` removes unreadable or stale state files that are no longer
fresh and no longer backed by running containers.

## Notes

- `up` works with or without `--gpu`; GPU locking is enabled only when `--gpu` or `-g` is used
- `--gpu` and `--gpu-backend` are ignored for non-`up` actions except `gpu-status`
- Compose files must be inside the current working directory
- The working directory must be inside the current user's home or `/srv/ml/users/<user>`

## Example Policy

See [compose-policy.yml](compose-policy.yml).

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE).
