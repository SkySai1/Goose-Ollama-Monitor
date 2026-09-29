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

## Goose App через MCP

### Запуск из списка Apps без чата

Для Goose 1.52 выполните однократно:

```bash
python3 ollama-monitor-mcp.py --install-goose-app
```

Затем откройте **Apps → Ollama Monitor → Launch**. Если Apps уже открыт, перейдите в другой раздел и вернитесь. Чат и вызов инструмента моделью не требуются; monitoring service и extension `ollamamonitor` должны работать.

Эта версия Goose фильтрует страницу Apps по встроенному расширению `apps` и скрывает обычные внешние MCP resources. Команда добавляет отдельную карточку в локальный `mcp-apps-cache`, сохраняя `ollamamonitor` первым в `mcpServers`: именно он обслуживает загрузку HTML и чтение метрик. Исходный код Goose не изменяется. После очистки cache или переустановки extension повторите команду. Для другого ключа extension используйте `--extension-name`, для другого порта — `--port`.

Карточка управляется командой регистрации; кнопки Import/Export/Delete встроенного Apps рассчитаны на его собственные HTML-приложения. Чтобы убрать карточку монитора, удалите только JSON-файл, путь к которому выводит команда установки.

### Подключение расширения и вызов через MCP

Готовая настройка этой рабочей станции: [`goose-extension.yaml`](goose-extension.yaml). Это запись для раздела `extensions` в `~/.config/goose/config.yaml`; остальные расширения сохраняются. На другом компьютере замените абсолютные пути к Python и проекту.

1. Запустите `python3 ollama-watch.py --server`. Collector работает отдельно от Goose и сохраняет историю при закрытии окна.
2. Включите stdio extension **Ollama Monitor** (ключ `ollamamonitor`). Команда:

   ```text
   /opt/homebrew/bin/python3 /Users/sky/Development/GooseAppOllamaWatch/ollama-monitor-mcp.py --port 11436
   ```

   Если UI разделяет поля: executable — `/opt/homebrew/bin/python3`, arguments — абсолютный путь к `ollama-monitor-mcp.py`, `--port`, `11436`. Запуск `ollama-watch.py` без `--mcp` в качестве extension некорректен: HTTP-сервис не отвечает на MCP initialize.
3. После обновления команды/кода выключите и включите extension, затем откройте новую сессию Goose.
4. Напишите Goose: **«Вызови open_monitor расширения Ollama Monitor и покажи приложение»**. Полное имя инструмента внутри Goose — `ollamamonitor__open_monitor`, аргументы `{}`.

Вызов MCP `tools/call`:

```json
{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"open_monitor","arguments":{}}}
```

`open_monitor` возвращает `_meta.ui.resourceUri = ui://ollama-monitor/dashboard`. Goose загружает HTML через `resources/read`. Metadata ресурса задаёт MIME `text/html;profile=mcp-app`, CSP `connectDomains` для выбранного localhost-порта и `window: {width: 880, height: 940, resizable: true}`. В зависимости от места вызова Goose показывает интерактивное приложение в чате; для отдельного окна используйте **Apps → Ollama Monitor → Launch**. HTML импортировать вручную не требуется.

В окне Goose метрики читаются через MCP Apps bridge → `monitor_read` → локальный HTTP API. `monitor_read` помечен `visibility: ["app"]`, чтобы регулярный polling не засорял общение с моделью. `open_monitor` доступен модели. Оба инструмента read-only; открытие приложения не требует работающего backend, а недоступные метрики отображаются как ошибка соединения с автоматическим восстановлением.

Handshake bridge повторяется после ошибки; таймаут одного RPC — 8 секунд. В обычном браузере интерфейс использует прямой HTTP. В Goose прямой запрос может блокироваться sandbox даже при работающем backend, поэтому он используется лишь как резервный путь. Ошибки bridge, HTTP и backend отображаются отдельно. Отключать CSP или расширять CORS для удалённых сайтов не требуется.

Если backend использует другой порт, укажите тот же `--port` в команде MCP extension. После обновления HTML закройте старое окно монитора и откройте приложение заново, чтобы Goose запросил свежий resource.

Формат основан на [Goose MCP Apps](https://goose-docs.ai/docs/tutorials/building-mcp-apps/) и [формате GooseApp](https://github.com/aaif-goose/goose/blob/main/crates/goose/src/goose_apps/app.rs). Для установленного MCP Apps bridge используется protocol `2025-11-21`.

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

Проверено на целевой macOS с Ollama 0.34.4 без загруженной модели. Активная генерация, несколько runner и offline/restart покрыты fixtures; загрузка модели автоматически не инициируется. Проверен установленный Goose: вызов open_monitor через его MCP-клиент, обнаружение ресурса, отдельное окно, ONLINE через bridge и переключение диапазонов.

Интеграционная проверка установленного Goose в отдельном временном профиле (не отправляет LLM prompts и не изменяет пользовательские беседы):

```bash
python3 tests/goose_smoke.py
python3 tests/goose_smoke.py --launcher
```

Она вызывает `open_monitor` через MCP-клиент Goose, проверяет metadata ресурса/окна, открывает приложение, проверяет `ONLINE` и диапазоны. Скриншот: `/tmp/ollama-monitor-goose.png`. Используется renderer SDK установленной Goose 1.52; при обновлении внутреннего SDK тесту может потребоваться адаптация, runtime приложения от него не зависит.
