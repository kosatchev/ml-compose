# Инструкция для администратора

English version: [ADMIN.md](ADMIN.md)  
Пользовательская инструкция: [README.ru.md](README.ru.md)  
English user guide: [README.md](README.md)

Этот документ описывает, как установить `ml-compose` так,
чтобы не нужно было добавлять пользователей в группу `docker`.

Целевая модель такая:

- Docker остаётся доступен только `root`
- пользователи не входят в группу `docker`
- пользователи запускают контейнеры только через `sudo ml-compose ...`
- код приложения, policy и lock-файлы принадлежат `root`

## Структура установки

Рекомендуемые пути:

- каталог приложения: `/opt/ml-compose/`
- launcher: `/usr/local/bin/ml-compose`
- policy-файл: `/opt/ml-compose/compose-policy.yml`
- каталоги локов:
  - `/opt/ml-compose/lock/state/`
  - `/opt/ml-compose/lock/guard/`

## Установка

Из каталога проекта:

```bash
sudo sh ./install.sh
```

Скрипт установит:

- Python-модули в `/opt/ml-compose/`
- launcher в `/usr/local/bin/ml-compose`
- policy-файл в `/opt/ml-compose/compose-policy.yml`
- lock directories в `/opt/ml-compose/lock/`

## Владельцы и права

Дерево приложения не должно быть writable для обычных пользователей.

Рекомендуемые владельцы:

- `/opt/ml-compose`: `root:root`
- `/usr/local/bin/ml-compose`: `root:root`

Рекомендуемые права:

- каталоги: `0755`
- `ml-compose.py`: `0755`
- вспомогательные `.py`: `0644`
- `compose-policy.yml`: `0644`
- launcher: `0755`

Проверка:

```bash
sudo ls -ld /opt/ml-compose
sudo ls -ld /opt/ml-compose/lock /opt/ml-compose/lock/state /opt/ml-compose/lock/guard
sudo ls -l /opt/ml-compose
sudo ls -l /usr/local/bin/ml-compose
```

## Доступ к Docker

Не добавляй пользователей в группу `docker`.

Проверка:

```bash
id username
getent group docker
```

Если пользователь уже в группе `docker`, убрать его можно так:

```bash
sudo gpasswd -d username docker
```

## Настройка sudoers

Разрешай запуск только wrapper'а, а не `docker`, не `python3` и не shell.

Создай sudoers snippet:

```bash
sudo visudo -f /etc/sudoers.d/ml-compose
```

Пример для группы `mlusers`:

```sudoers
Cmnd_Alias ML_COMPOSE = /usr/local/bin/ml-compose *

%mlusers ALL=(root) NOPASSWD: ML_COMPOSE
Defaults!ML_COMPOSE secure_path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Defaults!ML_COMPOSE env_reset
```

Замечание для WSL:

В WSL 2 с Docker Desktop и NVIDIA-поддержкой `nvidia-smi` часто лежит в
`/usr/lib/wsl/lib`, а не в одном из стандартных каталогов `PATH`. В таком
случае добавь этот путь в `secure_path` для wrapper'а:

```sudoers
Defaults!ML_COMPOSE secure_path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/lib/wsl/lib"
```

Как более простой workaround для одного хоста можно ещё создать симлинк:

```bash
sudo ln -s /usr/lib/wsl/lib/nvidia-smi /usr/local/bin/nvidia-smi
```

Это удобно, если ты не хочешь расширять `secure_path`.

Если `sudo ml-compose gpu-status` или `sudo ml-compose up --gpu ...` пишет,
что GPU backend не найден, а обычный `nvidia-smi` у пользователя работает,
первым делом проверь именно это.

Если группы ещё нет:

```bash
sudo groupadd mlusers
sudo usermod -aG mlusers username
```

## Управление policy

Установленный policy-файл:

- `/opt/ml-compose/compose-policy.yml`

Если тебе нужен enforcement policy, этот файл должен принадлежать `root` и не
быть writable для пользователей:

```bash
sudo chown root:root /opt/ml-compose/compose-policy.yml
sudo chmod 0644 /opt/ml-compose/compose-policy.yml
```

Без `compose-policy.yml` wrapper всё равно работает, но policy enforcement в
этом режиме намеренно permissive.

## Проверка установки

После установки проверь:

На хостах с GPU, от root:

```bash
sudo ml-compose gpu-status
```

На CPU-only хостах вместо этого используй общую проверку wrapper'а:

```bash
sudo ml-compose images -a
```

Для WSL дополнительно проверь:

```bash
which nvidia-smi
sudo which nvidia-smi
sudo env | grep ^PATH=
```

От разрешённого пользователя внутри тестового Compose-проекта:

```bash
sudo ml-compose up --gpu 0
sudo ml-compose ps
sudo ml-compose down
```

## Security checklist

- Docker daemon доступен только `root`
- пользователи не входят в группу `docker`
- `/opt/ml-compose` принадлежит `root:root`
- пользователи не могут менять код wrapper'а
- пользователи не могут менять установленный policy-файл
- через `sudo` разрешён только `/usr/local/bin/ml-compose`
- пользователям не дан `sudo` на `docker` или `python3`

## Операционные заметки

- `up` можно запускать и без `--gpu`; GPU-lock-файлы создаются только если указан `--gpu` или `-g`
- если `-f` не указан, wrapper сам ищет стандартные Compose-файлы
- lock-файлы лежат в `/opt/ml-compose/lock/`
- для именованных проектов нужно использовать одно и то же `-p/--project-name`
  в `up`, `ps`, `logs` и `down`
