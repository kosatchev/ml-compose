# Памятка для ML-пользователей

English version: `ML_USERS.md`  
Общая инструкция: `README.ru.md`  
Инструкция для администратора: `ADMIN.ru.md`

Эта памятка предназначена для ML-инженеров, которые запускают задачи через
`ml-compose`.

## Модель доступа

- не запускай `docker` напрямую
- не используй группу `docker`
- запускай задачи через `sudo ml-compose ...`
- работай только из домашнего каталога или из `/srv/ml/users/<user>`

## Где работать

Разрешенные рабочие каталоги:

- `/home/<user>/...`
- `/srv/ml/users/<user>/...`

Если запускать `ml-compose` вне этих каталогов, команда будет отклонена.

## Базовый сценарий

В каталоге проекта:

```bash
cd ~/my-project
ls
sudo ml-compose pull
sudo ml-compose up
sudo ml-compose ps
sudo ml-compose logs -f
sudo ml-compose down
```

## Минимальный пример Compose

Создай `compose.yml`:

```yaml
services:
  hello:
    image: hello-world
```

Запуск:

```bash
sudo ml-compose up
sudo ml-compose ps
sudo ml-compose down
```

## Работа с GPU

Опции `--gpu` и `-g` управляют выделением GPU для проекта.

Одна GPU:

```bash
sudo ml-compose up --gpu 0
```

Несколько GPU:

```bash
sudo ml-compose up --gpu 0,1
```

Все GPU выбранного backend'а:

```bash
sudo ml-compose up --gpu all
```

Режим без GPU:

```bash
sudo ml-compose up -g none
```

Проверка доступных GPU и состояния lock-файлов:

```bash
sudo ml-compose gpu-status
```

## Полезные команды

Запуск проекта:

```bash
sudo ml-compose up
```

Остановка проекта:

```bash
sudo ml-compose down
```

Показать контейнеры проекта:

```bash
sudo ml-compose ps
```

Показать логи:

```bash
sudo ml-compose logs -f
```

Собрать образы:

```bash
sudo ml-compose build
```

Загрузить образы:

```bash
sudo ml-compose pull
```

Показать все контейнеры на хосте:

```bash
sudo ml-compose ps -a
```

Показать все образы на хосте:

```bash
sudo ml-compose images -a
```

## Именованные проекты

Если нужен постоянный идентификатор проекта, явно укажи `-p`:

```bash
sudo ml-compose up -p train-exp-01
sudo ml-compose ps -p train-exp-01
sudo ml-compose down -p train-exp-01
```

Если ты задал `-p/--project-name` вручную,
используй то же имя в `up`, `ps`, `logs` и `down`.
(Это обычное поведение Compose)

## Частые ошибки

`ERROR: operation requires elevated privileges`

- запускай команду через `sudo`

`ERROR: no compose file found in current directory`

- создай `compose.yml`
- или укажи файл через `-f compose.yml`

`ERROR: working directory is not allowed`

- перейди в свой домашний каталог
- или используй `/srv/ml/users/<user>`

`ERROR: no supported GPU backend detected on this host`

- запускай без `--gpu`
- или используй `-g none` на CPU-only хостах

## Полезные рекомендации

- держи каждый проект в отдельном каталоге
- для долгих задач лучше сразу указывать удобное имя `-p`
- после экспериментов выполняй `sudo ml-compose down`
- на общем сервере проверяй `sudo ml-compose gpu-status` перед запуском на GPU
- не редактируй `/opt/ml-compose/*`

## Когда обращаться к администратору

- `sudo` не разрешает запуск `ml-compose`
- не работает оболочка или недоступен домашний каталог
- `gpu-status` не видит GPU на GPU-хосте
- policy блокирует нужное подключение каталога или устройства
- Docker недоступен или не запущен
