# Ollama Monitor

Локальный монитор Ollama для macOS: Python monitoring service, HTTP API и отдельное resizable Goose App. Четыре графика — System RAM, Ollama RAM, Memory Pressure и Context — используют общую временную шкалу, переключатель `1m / 15m / 1h` и синхронный курсор.

Python 3.10+; только стандартная библиотека. Интерфейс: один HTML с CSS, Vanilla JavaScript и Canvas, без npm, CDN и runtime-зависимостей.

## Быстрый запуск

```bash
cd /path/to/GooseAppOllamaWatch
python3 ollama-watch.py
```

Откройте <http://127.0.0.1:11436>. HTTP-вариант позволяет проверить монитор независимо от Goose.

По умолчанию используется **11436**: при разработке на целевой машине порт 11435 уже был занят отдельным llama-server для reranking. Этот независимый процесс не относится к Ollama и исключён из его RAM. Сервер всегда слушает только `127.0.0.1`.

```bash
python3 ollama-watch.py --server          # то же поведение по умолчанию
python3 ollama-watch.py --cli             # HTTP + терминал, один monitoring core
python3 ollama-watch.py --once            # один snapshot JSON, без HTTP
python3 ollama-watch.py --port 11437      # другой порт API и HTML
python3 ollama-watch.py --interval 1      # sampling раз в секунду
python3 ollama-watch.py 2                 # совместимость с positional interval, CLI + HTTP
```

`--host` / `OLLAMA_HOST` задаёт локальный Ollama HTTP origin (по умолчанию `http://127.0.0.1:11434`). `--log` / `OLLAMA_LOG` задаёт путь к server.log. Remote Ollama не поддерживается: локальные процессы и память должны относиться к тому же серверу. Минимальный interval — 0.5 секунды. Порт монитора должен отличаться от порта Ollama.

Для работы после закрытия терминала:

```bash
nohup python3 ollama-watch.py --server > /tmp/ollama-monitor.log 2>&1 &
echo $! > /tmp/ollama-monitor.pid
```

Остановка: `kill "$(cat /tmp/ollama-monitor.pid)"`. При обычном запуске — Ctrl+C. Проверяйте, что PID-файл относится к всё ещё работающему процессу, если система перезапускалась.

## Goose App

Для сетевого доступа в sandbox Goose используется небольшой **stdio MCP extension**. Он возвращает HTML, metadata окна и read-only инструмент чтения API. Он не собирает метрики и не запускает monitoring service; закрытие Goose не влияет на историю.

1. Запустите monitoring service командой выше.
2. В Goose откройте **Extensions → Add custom extension**, выберите **Standard IO**.
3. Укажите имя `ollama-monitor` и команду (с абсолютными путями):

   ```text
   /absolute/path/to/python3 /absolute/path/to/GooseAppOllamaWatch/ollama-monitor-mcp.py
   ```

   Путь к Python можно узнать через `command -v python3`. Если форма разделяет executable и arguments: executable — Python, единственный argument — абсолютный путь к `ollama-monitor-mcp.py`. Этот entry point всегда запускает MCP и сразу отвечает на `initialize`, даже если monitoring service выключен.
4. Включите extension, откройте **Apps → Ollama Monitor → Launch**. Если ресурс ещё не появился, откройте новую сессию с включённым extension, затем Apps. Альтернатива: попросите Goose вызвать read-only `monitor_read`.
5. Окно 880 × 940 изменяет размер и прокручивается при небольшой высоте.

Если backend использует другой порт, добавьте такой же `--port 11437` в команду MCP extension. HTML и CSP будут сформированы для этого порта автоматически.

**Если включение extension зависает:** проверьте executable и arguments. Запуск `ollama-watch.py` без `--mcp` включает HTTP-сервис, который не отвечает на MCP handshake. Goose тогда ждёт ответа до своего timeout. Используйте отдельный `ollama-monitor-mcp.py`; прежняя команда `ollama-watch.py --mcp` также поддерживается. После изменения команды выключите и снова включите extension; если текущая операция включения ещё ожидает ответа, перезапустите Goose. Для отображения метрик отдельно запустите `python3 ollama-watch.py --server`.

`app/ollama-monitor.html` также содержит GooseApp JSON-LD и подходит для **Apps → Import App**. При этом extension `ollama-monitor` должен оставаться включённым: в версиях Goose, не сохраняющих CSP при импорте HTML, приложение использует `tools/call` через MCP Apps bridge. Самостоятельный MCP resource предпочтительнее импорта, поскольку явно указывает `connectDomains`. Изменять исходный код или ослаблять безопасность Goose не требуется.

Формат интеграции основан на [Goose Apps](https://goose-docs.ai/docs/mcp/apps-mcp/), [MCP Apps в Goose](https://goose-docs.ai/docs/tutorials/building-mcp-apps/) и [исходном формате GooseApp](https://github.com/aaif-goose/goose/blob/main/crates/goose/src/goose_apps/app.rs). Для встроенного MCP Apps bridge установленной версии Goose используется protocol `2025-11-21`.

## Что именно измеряется

| Метрика | Источник и определение |
| --- | --- |
| System RAM | `sysctl hw.memsize` и `vm_stat`: `(Anonymous pages + Pages wired down + Pages occupied by compressor) × page_size`. Используется физический размер compressor, без повторного добавления логического `Pages stored in compressor`. Reclaimable file cache не считается занятой памятью приложений. Это оценка использования unified RAM по категориям VM, не побайтовая копия Activity Monitor. |
| Ollama RAM | Сумма RSS уникальных PID: `Ollama`, `ollama`, все их потомки, включая wrapper, llama-server и MLX. Принадлежность определяется executable и цепочкой PPID, а не словом ollama в аргументах. Размеры из `/api/ps` не прибавляются к RSS. |
| Memory Pressure | `memory_pressure -Q`, поле `System-wide memory free percentage`. `pressure_percent = 100 - free_percent` — **производная нормализация**, не внутренний показатель pressure Activity Monitor. |
| Context | Реально занятые токены runner `/slots`: `n_past`, `n_cache_tokens`, `tokens_cached`, `n_tokens`, `cache_n`. `/api/ps.context_length` — только capacity. |

**Ограничения RSS:** PID учитывается один раз, но shared resident pages разных процессов могут пересекаться. RSS исключает выгруженные compressed pages; это не точный уникальный physical footprint и не отдельный GPU allocator counter. API помечает метрику `estimated: true`, интерфейс — как resident memory. Учитываются все обнаруженные дочерние процессы, даже если их executable не содержит ollama.

**Context:** выбирается активный slot, затем slot с наибольшей известной занятостью. Независимые окна не суммируются. API возвращает все доступные `context.slots`, выбранные `runner_pid`, `slot` и источник измерения. При нескольких моделях не делается недостоверное сопоставление runner с именем модели. Capacity берётся из самого slot; fallback к API допустим только при одной модели, одном runner и одном slot. При смене runner/slot/model график делает разрыв.

Сохранён tolerant parser speculative/MTP `next_token`: берётся максимум вложенных `n_decoded`, не сумма draft/target. Если `/slots` не раскрывает occupancy, допускается явно обозначенная оценка из **новых** логов для одного runner/одного slot: наблюдение `n_past`/release либо prompt + generated. Старый лог при старте не принимается за текущий context. Fallback устаревает через 30 секунд; смена процесса/модели, новый task, rotation/truncation сбрасывают прежнюю занятость. Отключённый `/slots` и неинформативный лог означают `null`, а не выдуманный процент. Generation counts, timings, context shifts и последние строки логов доступны через API/CLI.

Отсутствие метрики — `null`. Измеренный ноль остаётся нулём. Если ps недоступен, RAM Ollama неизвестна; если ps успешно подтверждает отсутствие процессов — RAM равна нулю. UI различает недоступный backend, устаревший collector, offline Ollama, отсутствие модели и недоступность context.

## История и API

По умолчанию snapshot собирается раз в 2 секунды. Collector не зависит от подключённых клиентов. Ring buffer — 2000 точек (~66 минут); при меньшем interval ёмкость увеличивается так, чтобы оставалось минимум 65 минут. История хранится в памяти Python; перезапуск UI её сохраняет, перезапуск сервиса сбрасывает. Часы истории — Unix time, cadence loop использует monotonic clock.

```bash
curl http://127.0.0.1:11436/api/status
curl 'http://127.0.0.1:11436/api/history?range=1m'
curl 'http://127.0.0.1:11436/api/history?range=15m'
curl 'http://127.0.0.1:11436/api/history?range=1h'
```

`/api/status`: timestamp, ollama, model/models, system_memory, ollama_memory, memory_pressure, context, processes и collector duration/interval. При нескольких моделях `model` равен `null`, полный список доступен в `models`.

`/api/history`: range, start/end общей шкалы, interval_seconds и samples. Обязательные ряды: `system_ram_percent`, `ollama_ram_bytes`, `memory_pressure_percent`, `context_percent`. Дополнительно: RAM в байтах/процентах, swap, free pressure, context used/max/source/identity. Возвращаются все исходные точки, включая пики и null. 1800 точек за час Canvas отображает без downsampling. Обновление страницы не формирует отдельной истории.

Read-only HTTP: GET и CORS OPTIONS. Неизвестный range — 400, неизвестный путь — 404, прогрев `/api/status` — 503. Host проверяется против localhost/127.0.0.1, CORS разрешён для localhost и opaque origin `null` sandbox Goose. API не имеет endpoints управления моделью или shell. Внешние сайты с обычным remote Origin отклоняются; opaque локальные HTML-клиенты могут читать данные.

## Структура

```text
ollama-watch.py           entry point: service, CLI, once, MCP adapter
ollama-monitor-mcp.py     отдельный entry point для Goose, всегда MCP stdio
ollama_monitor/
  collector.py           общий snapshot и независимый sampling loop
  ollama.py              read-only API Ollama
  memory.py              macOS RAM, swap, derived pressure
  processes.py           process tree, RSS, runner ports
  context.py             slots и ограниченный log fallback
  history.py             thread-safe ring buffer / repository
  api.py                 loopback HTTP, HTML
  mcp.py                 Goose resource и read-only bridge
app/ollama-monitor.html   presentation layer
```

## Проверка

```bash
python3 -m unittest discover -s tests -v
```

Проверяются VM formula/компрессия, дерево процессов/MLX/чужие runner, null/zero, варианты slots, свежесть/rotation логов, model restart, час истории, независимый sampling, CORS, Host, read-only API и MCP resource/tool.
Отдельные subprocess-тесты проверяют initialize, notifications, discovery и ping при открытом stdin, без запущенного backend и из другого рабочего каталога: handshake не должен ждать EOF.

Опциональная проверка настоящим Chrome (macOS, сервис должен работать на 11436):

```bash
python3 tests/browser_smoke.py
```

Тест использует временный профиль, проверяет диапазоны, синхронный tooltip, offline/reconnect, 1801 точку с разрывами, узкое окно и ошибки JavaScript. Скриншоты сохраняются в `/tmp/ollama-monitor-live.png` и `/tmp/ollama-monitor-narrow-fixture.png`; второй использует синтетическую историю только в тестовой странице.

Проверено на целевой macOS с Ollama 0.34.4 без загруженной модели. Активная генерация, несколько runner и offline/restart покрыты fixtures; загрузка модели автоматически не инициируется. MCP contract и формат Goose проверены, непосредственный запуск окна через UI Goose требует добавления extension описанным выше способом.
