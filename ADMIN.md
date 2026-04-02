# Administrator Setup

Russian version: [ADMIN.ru.md](ADMIN.ru.md)  
User guide: [README.md](README.md)  
Russian user guide: [README.ru.md](README.ru.md)

This guide describes how to install `ml-compose` so users can launch ML
containers without being added to the `docker` group.

The intended model is:

- Docker remains accessible only to `root`
- users are not members of the `docker` group
- users run containers only through `sudo ml-compose ...`
- application code, policy, and lock files are owned by `root`

## Install Layout

Recommended installation paths:

- application directory: `/opt/ml-compose/`
- launcher: `/usr/local/bin/ml-compose`
- policy file: `/opt/ml-compose/compose-policy.yml`
- lock directories:
  - `/opt/ml-compose/lock/state/`
  - `/opt/ml-compose/lock/guard/`

## Install

From the project directory:

```bash
sudo sh ./install.sh
```

This installs:

- Python modules into `/opt/ml-compose/`
- the launcher into `/usr/local/bin/ml-compose`
- the policy file into `/opt/ml-compose/compose-policy.yml`
- lock directories under `/opt/ml-compose/lock/`

## Ownership And Permissions

The application tree must not be writable by regular users.

Recommended ownership:

- `/opt/ml-compose`: `root:root`
- `/usr/local/bin/ml-compose`: `root:root`

Recommended modes:

- directories: `0755`
- `ml-compose.py`: `0755`
- helper `.py` files: `0644`
- `compose-policy.yml`: `0644`
- launcher: `0755`

Check with:

```bash
sudo ls -ld /opt/ml-compose
sudo ls -ld /opt/ml-compose/lock /opt/ml-compose/lock/state /opt/ml-compose/lock/guard
sudo ls -l /opt/ml-compose
sudo ls -l /usr/local/bin/ml-compose
```

## Docker Access

Do not add users to the `docker` group.

Check:

```bash
id username
getent group docker
```

If needed, remove a user from the `docker` group:

```bash
sudo gpasswd -d username docker
```

## Sudoers Configuration

Grant access only to the wrapper, not to `docker`, `python3`, or an interactive
shell.

Create a sudoers snippet:

```bash
sudo visudo -f /etc/sudoers.d/ml-compose
```

Example for a group `mlusers`:

```sudoers
Cmnd_Alias ML_COMPOSE = /usr/local/bin/ml-compose *

%mlusers ALL=(root) NOPASSWD: ML_COMPOSE
Defaults!ML_COMPOSE secure_path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Defaults!ML_COMPOSE env_reset
```

WSL note:

On WSL 2 with Docker Desktop and NVIDIA support, `nvidia-smi` may live in
`/usr/lib/wsl/lib` instead of a standard directory in `PATH`. In that case,
extend `secure_path` for the wrapper:

```sudoers
Defaults!ML_COMPOSE secure_path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/lib/wsl/lib"
```

As a simpler workaround on a single host, you can also create a symlink:

```bash
sudo ln -s /usr/lib/wsl/lib/nvidia-smi /usr/local/bin/nvidia-smi
```

This is convenient when you do not want to widen `secure_path`.

If `sudo ml-compose gpu-status` or `sudo ml-compose up --gpu ...` says no GPU
backend was detected while plain `nvidia-smi` works for the user, this is the
first thing to check.

If the group does not exist yet:

```bash
sudo groupadd mlusers
sudo usermod -aG mlusers username
```

## Policy Management

The installed policy file is:

- `/opt/ml-compose/compose-policy.yml`

If you want policy enforcement, keep this file owned by `root` and not writable
by users:

```bash
sudo chown root:root /opt/ml-compose/compose-policy.yml
sudo chmod 0644 /opt/ml-compose/compose-policy.yml
```

Without `compose-policy.yml`, the wrapper still works, but policy enforcement is
intentionally permissive.

## Verification

Run these checks after installation.

On GPU-capable hosts, as root:

```bash
sudo ml-compose gpu-status
```

On CPU-only hosts, use a generic wrapper check instead:

```bash
sudo ml-compose images -a
```

On WSL, also verify:

```bash
which nvidia-smi
sudo which nvidia-smi
sudo env | grep ^PATH=
```

As a permitted user, inside a test Compose project:

```bash
sudo ml-compose up --gpu 0
sudo ml-compose ps
sudo ml-compose down
```

## Security Checklist

- Docker daemon is accessible only to `root`
- users are not in the `docker` group
- `/opt/ml-compose` is owned by `root:root`
- users cannot modify the wrapper code
- users cannot modify the installed policy file
- users can run only `/usr/local/bin/ml-compose` via `sudo`
- users do not have `sudo` access to `docker` or `python3`

## Operational Notes

- `up` can run without `--gpu`; GPU lock files are created only when `--gpu` or `-g` is used
- the wrapper auto-detects standard Compose filenames if `-f` is not provided
- lock files are stored under `/opt/ml-compose/lock/`
- for named projects, operators should use the same `-p/--project-name` across
  `up`, `ps`, `logs`, and `down`
