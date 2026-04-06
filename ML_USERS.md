# ML User Quick Start

Russian version: `ML_USERS.ru.md`  
General guide: `README.md`  
Administrator guide: `ADMIN.md`

This note is for ML engineers who run workloads through `ml-compose`.

## Access Model

- do not run `docker` directly
- do not use the `docker` group
- run workloads through `sudo ml-compose ...`
- work only from your home directory or `/srv/ml/users/<user>`

## Where To Work

Allowed working directories:

- `/home/<user>/...`
- `/srv/ml/users/<user>/...`

If you run `ml-compose` outside these locations, the command will be rejected.

## Basic Flow

Inside your project directory:

```bash
cd ~/my-project
ls
sudo ml-compose pull
sudo ml-compose up
sudo ml-compose ps
sudo ml-compose logs -f
sudo ml-compose down
```

## Minimal Compose Example

Create `compose.yml`:

```yaml
services:
  hello:
    image: hello-world
```

Run it:

```bash
sudo ml-compose up
sudo ml-compose ps
sudo ml-compose down
```

## GPU Usage

The `--gpu` and `-g` options control GPU allocation for the project.

One GPU:

```bash
sudo ml-compose up --gpu 0
```

Several GPUs:

```bash
sudo ml-compose up --gpu 0,1
```

All GPUs from the selected backend:

```bash
sudo ml-compose up --gpu all
```

No GPU mode:

```bash
sudo ml-compose up -g none
```

Check GPU visibility and lock state:

```bash
sudo ml-compose gpu-status
```

## Useful Commands

Start project:

```bash
sudo ml-compose up
```

Stop project:

```bash
sudo ml-compose down
```

Show project containers:

```bash
sudo ml-compose ps
```

Show logs:

```bash
sudo ml-compose logs -f
```

Build images:

```bash
sudo ml-compose build
```

Pull images:

```bash
sudo ml-compose pull
```

Show all containers on the host:

```bash
sudo ml-compose ps -a
```

Show all images on the host:

```bash
sudo ml-compose images -a
```

## Named Projects

If you want a stable project name, pass `-p` explicitly:

```bash
sudo ml-compose up -p train-exp-01
sudo ml-compose ps -p train-exp-01
sudo ml-compose down -p train-exp-01
```

This is standard Compose behavior: if you set `-p/--project-name` explicitly,
use the same name for `up`, `ps`, `logs`, and `down`.

## Common Errors

`ERROR: operation requires elevated privileges`

- run the command through `sudo`

`ERROR: no compose file found in current directory`

- create `compose.yml`
- or pass `-f compose.yml`

`ERROR: working directory is not allowed`

- move into your home directory
- or use `/srv/ml/users/<user>`

`ERROR: no supported GPU backend detected on this host`

- run without `--gpu`
- or use `-g none` on CPU-only hosts

## Good Habits

- keep each project in its own directory
- prefer explicit `-p` for long-running jobs
- use `sudo ml-compose down` after experiments
- check `sudo ml-compose gpu-status` before taking GPUs on shared hosts
- do not edit `/opt/ml-compose/*`

## Ask The Administrator If

- `sudo` does not allow `ml-compose`
- your shell or home directory is not set up correctly
- `gpu-status` shows no GPUs on a GPU host
- policy blocks a mount or device you really need
- Docker is down or inaccessible
