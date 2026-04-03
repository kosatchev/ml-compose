# ml-compose

English version: `README.md`  
Инструкция для администратора: `ADMIN.ru.md`  
English administrator guide: `ADMIN.md`

`ml-compose` — это более безопасный враппер вокруг `docker compose` для ML-задач.

Он валидирует итоговую Compose-конфигурацию перед запуском, применяет локальную
policy, выставляет переменные окружения для GPU, добавляет служебные labels и
ведёт lock/state файлы GPU, чтобы уменьшить число случайных конфликтов между
проектами.

## Что делает

- валидирует результат `docker compose config`, а не только исходный YAML
- поддерживает несколько Compose-файлов через `-f/--file`
- поддерживает GPU backend'ы `auto`, `nvidia` и `amd`
- выставляет GPU env vars в сервисы
- добавляет labels с владельцем, проектом и выбранными GPU
- не даёт двум проектам одновременно занять одну и ту же GPU через lock/state файлы
- загружает policy из `compose-policy.yml`, если файл есть

## Требования

- Python 3
- `docker`
- `PyYAML`
- `nvidia-smi` для NVIDIA-хостов
- `/dev/kfd` и/или `/dev/dri/renderD*` для AMD-хостов
- опционально `rocm-smi` для более подробного `gpu-status` на AMD

## Файлы

- `ml-compose.py`: основной entrypoint и orchestration
- `compose_cli.py`: разбор compose-аргументов
- `compose_runtime.py`: `docker compose config` и временный Compose-файл
- `policy.py`: загрузка policy, валидация, labels, GPU env injection
- `gpu_backend.py`: определение NVIDIA/AMD backend и GPU metadata
- `gpu_locks.py`: guard locks и state lock lifecycle
- `compose-policy.yml`: опциональный локальный policy-файл

## Рекомендуемая структура установки

Для self-contained системной установки:

- каталог приложения: `/opt/ml-compose/`
- launcher: `/usr/local/bin/ml-compose`
- policy-файл: `/opt/ml-compose/compose-policy.yml`
- каталоги локов:
  - `/opt/ml-compose/lock/state/`
  - `/opt/ml-compose/lock/guard/`

Каталог приложения должен принадлежать `root:root` и не быть writable для
обычных пользователей.

## Использование

Базовый пример:

```bash
python3 ml-compose.py up
```

Если `-f/--file` не указан, `ml-compose` ищет Compose-файл в текущей
директории в таком порядке:

- `docker-compose.yml`
- `docker-compose.yaml`
- `compose.yml`
- `compose.yaml`

### Типичные команды

Запуск простого проекта без выделения GPU и без GPU-блокировок:

```bash
python3 ml-compose.py up
```

Явный запуск в режиме без GPU:

```bash
python3 ml-compose.py up -g none
```

Запуск на одной GPU из compose-файла по умолчанию в текущей директории:

```bash
python3 ml-compose.py up --gpu 0
```

Запуск на нескольких GPU:

```bash
python3 ml-compose.py up --gpu 0,1
```

Взять все GPU выбранного backend'а:

```bash
python3 ml-compose.py up --gpu all --gpu-backend auto
```

Если `--gpu` не указан, или передан `-g none`, `up` ведёт себя как обычный
запуск Compose: не выставляет GPU env vars и не создаёт GPU-lock'и.

Короткие алиасы для wrapper-опций:

- `-g` для `--gpu`
- `-G` для `--gpu-backend`

Запуск с явным именем проекта, чтобы потом тем же именем пользоваться в `down`,
`ps` и `logs`:

```bash
python3 ml-compose.py up --gpu 0 -p train-exp-01 -f compose.yml
```

Использование нескольких Compose-файлов, например base + override:

```bash
python3 ml-compose.py up --gpu 0 -f compose.yml -f compose.override.yml
```

Собрать образы без запуска контейнеров:

```bash
python3 ml-compose.py build
```

Собрать без использования Docker build cache:

```bash
python3 ml-compose.py build --no-cache
```

Подтянуть последние версии образов без запуска контейнеров:

```bash
python3 ml-compose.py pull
```

Остановить именованный проект:

```bash
python3 ml-compose.py down -p train-exp-01 -f compose.yml
```

Посмотреть состояние контейнеров именованного проекта:

```bash
python3 ml-compose.py ps -p train-exp-01 -f compose.yml
```

Посмотреть все контейнеры на хосте у всех пользователей:

```bash
python3 ml-compose.py ps -a
```

Посмотреть, какие образы связаны с проектом:

```bash
python3 ml-compose.py images -p train-exp-01 -f compose.yml
```

Посмотреть все образы на хосте:

```bash
python3 ml-compose.py images -a
```

Подписаться на логи именованного проекта:

```bash
python3 ml-compose.py logs -f -p train-exp-01
```

Посмотреть, какие GPU видны и заняты ли они сейчас:

```bash
python3 ml-compose.py gpu-status
```

Почистить stale state locks, которые больше не принадлежат живому проекту:

```bash
python3 ml-compose.py reconcile-locks
```

По умолчанию имя проекта генерируется автоматически из текущей директории и
пользователя. При необходимости его можно переопределить через
`-p/--project-name`.

## Как работает запуск

1. Разбирает аргументы враппера, такие как `--gpu` и `--gpu-backend`.
2. Разбирает compose-аргументы, такие как `-f`, `--env-file`, `--profile` и `-p`.
3. Получает итоговую конфигурацию через `docker compose config`.
4. Загружает policy, если она доступна, и валидирует итоговый документ.
5. Для `up` всегда выставляет служебные labels; GPU env vars добавляет только если через `-g/--gpu` выбраны GPU.
6. Захватывает GPU guard locks и пишет state lock файлы только если через `-g/--gpu` выбраны GPU.
7. Запускает `docker compose`.
8. После старта контейнеров помечает локи как активные только для запусков с GPU-блокировкой.

## Поддерживаемые действия

- `up`
- `build`
- `pull`
- `down`
- `ps`
  `ps -a` работает отдельно: запускает глобальный `docker ps -a` и не требует Compose-файл.
- `images`
  `images -a` работает отдельно: запускает глобальный `docker images` и не требует Compose-файл.
- `logs`
- `restart`
- `stop`
- `start`
- `config`
- `gpu-status`
- `reconcile-locks`

## Поведение GPU

Выбор backend'а:

- `auto`: предпочитает NVIDIA, если доступен `nvidia-smi`, иначе AMD, если найден runtime
- `nvidia`: требует `nvidia-smi`
- `amd`: требует обнаружения AMD runtime

Переменные окружения:

- NVIDIA: `CUDA_VISIBLE_DEVICES`
- AMD: `HIP_VISIBLE_DEVICES`, `ROCR_VISIBLE_DEVICES`

Пользовательские индексы GPU локальны для backend'а. Например, `--gpu 0`
означает `NVIDIA GPU 0` при `--gpu-backend nvidia` и `AMD GPU 0` при
`--gpu-backend amd`.

Внутренние lock IDs namespaced, например:

- `nvidia:0`
- `amd:0`

Это нужно, чтобы не было коллизий на mixed-host.

## Policy

`ml-compose` ищет `compose-policy.yml` в таком порядке:

1. рядом с Python-модулями
2. рядом с основным Compose-файлом
3. в текущей рабочей директории

Текущая схема policy поддерживает:

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

Валидация выполняется по уже отрендеренному Compose-документу, поэтому
относительные пути, merge нескольких файлов и Compose expansion проверяются
в том виде, в котором их реально видит Docker.

Без `compose-policy.yml` `ml-compose` всё равно работает. В этом режиме он
использует:

- permissive встроенные дефолты для boolean policy-флагов
- permissive fallback mount/device rules с широким доступом к host

На практике в no-config режиме в основном сохраняются:

- Compose rendering и validation flow
- GPU environment injection
- owner/project/GPU labels
- GPU lock и state management

В этом режиме policy enforcement намеренно слабый, поэтому если тебе нужны
реальные mount/device ограничения, используй `compose-policy.yml`.

Когда `compose-policy.yml` есть:

- boolean flags по-прежнему могут опускаться и добираются из code defaults
- mount/device list rules должны быть явно заданы в YAML
- YAML становится источником истины для list-based правил

## Labels и lock-файлы

Сервисам добавляются labels:

- `ml.owner`
- `ml.project`
- `ml.gpu`

В рекомендуемой install-схеме GPU coordination использует два типа файлов под
`/opt/ml-compose/lock`:

- `guard/`: короткоживущие file locks на время переходов состояния
- `state/`: JSON-файлы с информацией о владельце и состоянии активации

`reconcile-locks` удаляет нечитаемые или stale state files, которые больше не
связаны с живыми контейнерами.

## Важные замечания

- `up` работает и без `--gpu`; GPU-блокировки включаются только если указан `--gpu` или `-g`
- `--gpu` и `--gpu-backend` игнорируются для не-`up` действий, кроме `gpu-status`
- Compose-файлы должны находиться внутри текущей рабочей директории
- Рабочая директория должна быть внутри home текущего пользователя или `/srv/ml/users/<user>`

## Пример policy

См. `compose-policy.yml`.

## Лицензия

Проект распространяется под Apache License 2.0. См. `LICENSE`.
