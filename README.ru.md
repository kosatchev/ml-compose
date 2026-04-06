# ml-compose

English version: `README.md`  
Памятка для ML-пользователей: `ML_USERS.ru.md`  
Инструкция для администратора: `ADMIN.ru.md`  
English administrator guide: `ADMIN.md`

`ml-compose` — более безопасная обертка над `docker compose` для ML-задач.

Он проверяет итоговую Compose-конфигурацию перед запуском, применяет локальную
policy, выставляет переменные окружения для GPU, добавляет служебные labels и
ведет lock- и state-файлы GPU, чтобы уменьшить число случайных конфликтов между
проектами.

## Что делает

- валидирует результат `docker compose config`, а не только исходный YAML
- поддерживает несколько Compose-файлов через `-f/--file`
- поддерживает GPU-backend'ы `auto`, `nvidia` и `amd`
- выставляет переменные окружения GPU в сервисы
- добавляет labels с владельцем, проектом и выбранными GPU
- не дает двум проектам одновременно занять одну и ту же GPU через lock- и state-файлы
- загружает policy из `compose-policy.yml`, если файл есть

## Требования

- Python 3
- `docker`
- `PyYAML`
- `nvidia-smi` для NVIDIA-хостов
- `/dev/kfd` и/или `/dev/dri/renderD*` для AMD-хостов
- опционально `rocm-smi` для более подробного `gpu-status` на AMD

## Файлы

- `ml-compose.py`: основная точка входа и оркестрация
- `compose_cli.py`: разбор compose-аргументов
- `compose_runtime.py`: `docker compose config` и временный Compose-файл
- `policy.py`: загрузка policy, валидация, labels, настройка окружения GPU
- `gpu_backend.py`: определение NVIDIA/AMD backend и сведения о GPU
- `gpu_locks.py`: guard-lock-файлы и жизненный цикл state lock-файлов
- `compose-policy.yml`: опциональный локальный policy-файл

## Рекомендуемая структура установки

Для автономной системной установки:

- каталог приложения: `/opt/ml-compose/`
- исполняемый файл: `/usr/local/bin/ml-compose`
- policy-файл: `/opt/ml-compose/compose-policy.yml`
- каталоги lock-файлов:
  - `/opt/ml-compose/lock/state/`
  - `/opt/ml-compose/lock/guard/`

Каталог приложения должен принадлежать `root:root` и не должен быть доступен
на запись обычным пользователям.

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

Опции `--gpu` и `-g` управляют выделением GPU для проекта.

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

Использовать все GPU выбранного backend:

```bash
python3 ml-compose.py up --gpu all --gpu-backend auto
```

Если `--gpu` не указан, или передан `-g none`, `up` ведёт себя как обычный
запуск Compose: не выставляет GPU env vars и не создаёт GPU-lock'и.

Короткие алиасы для опций `ml-compose`:

- `-g` для `--gpu`
- `-G` для `--gpu-backend`

Запуск с явным именем проекта, чтобы потом использовать то же имя в `down`,
`ps` и `logs`. Это обычное поведение Compose, а не особенность `ml-compose`:

```bash
python3 ml-compose.py up --gpu 0 -p train-exp-01 -f compose.yml
```

Использование нескольких Compose-файлов, например базового и override-файла:

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

Загрузить последние версии образов без запуска контейнеров:

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

Удалить устаревшие state lock-файлы, которые больше не относятся к живому проекту:

```bash
python3 ml-compose.py reconcile-locks
```

По умолчанию имя проекта автоматически формируется из текущего каталога и
имени пользователя, а затем приводится к виду, совместимому с Compose. При
необходимости его можно переопределить через
`-p/--project-name`.

## Как работает запуск

1. Разбирает аргументы `ml-compose`, такие как `--gpu` и `--gpu-backend`.
2. Разбирает compose-аргументы, такие как `-f`, `--env-file`, `--profile` и `-p`.
3. Получает итоговую конфигурацию через `docker compose config`.
4. Загружает policy, если она доступна, и проверяет итоговый документ.
5. Для `up` всегда добавляет служебные labels; переменные окружения GPU добавляет только если через `-g/--gpu` выбраны GPU.
6. Захватывает guard-lock-файлы GPU и записывает state lock-файлы только если через `-g/--gpu` выбраны GPU.
7. Запускает `docker compose`.
8. После запуска контейнеров помечает локи как активные только для запусков с GPU-блокировкой.

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

- `auto`: выбирает NVIDIA, если доступен `nvidia-smi`, иначе AMD, если найдена подходящая среда выполнения
- `nvidia`: требует `nvidia-smi`
- `amd`: требует обнаружения AMD runtime

Переменные окружения:

- NVIDIA: `CUDA_VISIBLE_DEVICES`
- AMD: `HIP_VISIBLE_DEVICES`, `ROCR_VISIBLE_DEVICES`

Пользовательские индексы GPU локальны для backend. Например, `--gpu 0`
означает `NVIDIA GPU 0` при `--gpu-backend nvidia` и `AMD GPU 0` при
`--gpu-backend amd`.

Внутренние lock ID имеют префикс backend, например:

- `nvidia:0`
- `amd:0`

Это нужно, чтобы избежать конфликтов на хостах со смешанной конфигурацией.

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

Проверка выполняется по уже отрендеренному Compose-документу, поэтому
относительные пути, объединение нескольких файлов и подстановка переменных
Compose проверяются в том виде, в котором их реально видит Docker.

Без `compose-policy.yml` `ml-compose` все равно работает. В этом режиме он
использует:

- встроенные мягкие значения по умолчанию для булевых policy-флагов
- fallback-правила для mount/device с широким доступом к хосту

На практике в режиме без `compose-policy.yml` в основном сохраняются:

- рендеринг и проверка Compose-конфигурации
- настройка окружения GPU
- labels владельца, проекта и GPU
- управление GPU lock- и state-файлами

В этом режиме ограничения policy намеренно ослаблены, поэтому, если нужны
реальные mount/device ограничения, используй `compose-policy.yml`.

Когда `compose-policy.yml` есть:

- boolean flags по-прежнему можно опускать, и тогда будут использованы значения по умолчанию из кода
- правила для списков mount/device должны быть явно заданы в YAML
- YAML-файл становится основным источником этих list-based правил

## Labels и lock-файлы

Сервисам добавляются labels:

- `ml.owner`
- `ml.project`
- `ml.gpu`

В рекомендуемой схеме установки для координации GPU используются два типа
файлов в `/opt/ml-compose/lock`:

- `guard/`: короткоживущие lock-файлы на время переходов состояния
- `state/`: JSON-файлы с информацией о владельце и состоянии активации

`reconcile-locks` удаляет нечитаемые или устаревшие state-файлы, которые больше
не связаны с живыми контейнерами.

## Важные замечания

- `up` работает и без `--gpu`; GPU-блокировки включаются только если указан `--gpu` или `-g`
- `--gpu` и `--gpu-backend` игнорируются для не-`up` действий, кроме `gpu-status`
- Compose-файлы должны находиться в текущей рабочей директории
- рабочий каталог должен находиться внутри домашнего каталога пользователя или `/srv/ml/users/<user>`

## Пример policy

См. `compose-policy.yml`.

## Лицензия

Проект распространяется под Apache License 2.0. См. `LICENSE`.
