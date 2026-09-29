# Ollama Monitor — архитектурное описание

## 1. Цель

Разработать **Ollama Monitor** — отдельный Goose App для мониторинга локального Ollama на macOS.

В корне проекта уже находится:

```text
ollama-watch.py
```

Существующую логику этого скрипта разрешено и предполагается **изменять, рефакторить и расширять**.

Не требуется сохранять внутреннюю архитектуру `ollama-watch.py`.

Требуется сохранить полезный существующий функционал и дополнить его полноценным сбором временных метрик.

Основные наблюдаемые показатели:

```text
System RAM utilization
Ollama RAM utilization
Memory pressure
Context window utilization
```

Для каждого показателя должна храниться история и отображаться график с диапазонами:

```text
1m
15m
1h
```

---

# 2. Целевая архитектура

Использовать архитектуру:

```text
                    macOS
                      │
           ┌──────────┴──────────┐
           │                     │
           ▼                     ▼
     Ollama API              OS metrics
     :11434                   vm_stat
                              sysctl
                              ps
                              memory_pressure
           │                     │
           └──────────┬──────────┘
                      ▼
             ollama-watch.py
             Monitoring Core
                      │
             ┌────────┴────────┐
             │                 │
             ▼                 ▼
        Current state      Time-series
                            ring buffer
             │                 │
             └────────┬────────┘
                      ▼
                Local HTTP API
                      │
                      ▼
                 Goose App
              HTML/CSS/JavaScript
```

Ключевой принцип:

```text
ollama-watch.py = источник истины

Goose App = presentation layer
```

Goose App самостоятельно не должен вычислять системные метрики.

---

# 3. Роль ollama-watch.py

`ollama-watch.py` становится центральным компонентом мониторинга.

Его разрешено существенно переработать.

Он должен отвечать за:

```text
сбор текущих метрик
нормализацию данных
вычисление производных показателей
ведение истории
определение загруженных моделей
определение заполнения context
HTTP API для Goose App
CLI-режим при необходимости
```

Старую реализацию необходимо сначала изучить.

Полезные существующие алгоритмы следует сохранить либо улучшить, если новая реализация получается точнее или устойчивее.

Не нужно искусственно сохранять старый код только ради обратной совместимости внутренней реализации.

---

# 4. Monitoring Core

Логику желательно разделить на независимые collectors:

```python
get_ollama_status()
get_loaded_models()
get_context_metrics()

get_system_memory_metrics()
get_ollama_memory_metrics()
get_memory_pressure_metrics()

build_snapshot()
```

Результатом одного цикла мониторинга является единый snapshot.

Пример:

```json
{
  "timestamp": 1790672400.125,

  "system_memory": {
    "total_bytes": 51539607552,
    "used_bytes": 40802189312,
    "used_percent": 79.17,
    "swap_used_bytes": 2147483648
  },

  "ollama_memory": {
    "used_bytes": 25769803776,
    "used_percent_of_system_ram": 50.0
  },

  "memory_pressure": {
    "free_percent": 31,
    "pressure_percent": 69
  },

  "context": {
    "used_tokens": 70412,
    "max_tokens": 98304,
    "used_percent": 71.63
  }
}
```

---

# 5. Sampling

Monitoring Core должен собирать snapshot постоянно.

Базовый интервал:

```text
2 seconds
```

То есть:

```text
30 samples / minute
450 samples / 15 minutes
1800 samples / hour
```

Это приемлемый объём.

Хранить минимум последние:

```text
1 hour
```

Рекомендуемый ring buffer:

```text
~1900-2000 samples
```

чтобы иметь небольшой запас.

История должна храниться в `ollama-watch.py`, а не только внутри UI.

Таким образом открытие или перезапуск Goose App не должно обнулять историю, пока работает monitoring process.

---

# 6. System RAM

Необходима метрика:

```text
system_memory.used_bytes
system_memory.total_bytes
system_memory.used_percent
```

График должен показывать именно **утилизацию всей unified memory системы**.

Пример:

```text
System RAM

████████████████░░░░ 79 %

38.0 GB / 48 GB
```

Формула:

```text
used_percent =
    used_bytes / total_bytes * 100
```

При расчёте используемой памяти учитывать специфику macOS и compressed memory.

Не использовать исключительно значение `free` как:

```text
used = total - free
```

если это приводит к некорректному представлению памяти macOS.

На современных версиях macOS при наличии подходящего `meminfo` допустимо использовать его данные; в остальных случаях использовать `vm_stat` / системные API. `meminfo` умеет выдавать категории памяти и swap в машиночитаемом виде.

---

# 7. Ollama RAM

Необходима отдельная временная метрика:

```text
ollama_memory.used_bytes
```

Также рассчитывать:

```text
ollama_memory.used_percent_of_system_ram
```

Пример:

```text
Ollama RAM

24.3 GB
50.6 % of physical RAM
```

Не ограничиваться только основным процессом `ollama`.

Необходимо исследовать process tree Ollama и определить процессы, относящиеся к:

```text
ollama serve
model runner
MLX runner
другим дочерним Ollama processes
```

Если память модели фактически находится в дочернем runner-процессе, она должна учитываться.

Избегать двойного подсчёта.

---

# 8. Context window

Сохранить либо улучшить существующую функцию `ollama-watch.py`, определяющую заполнение контекстного окна.

Требуемые данные:

```text
context.used_tokens
context.max_tokens
context.used_percent
```

Пример:

```text
Context

70 412 / 98 304

██████████████░░░░░░ 71.6 %
```

Важно различать:

```text
allocated context_length
```

и:

```text
actually used context
```

Значение `context_length` из `/api/ps` нельзя автоматически считать фактически заполненным context.

Если текущий `ollama-watch.py` уже умеет получать реальное использование context, можно использовать и доработать этот механизм.

---

# 9. Memory Pressure

Добавить отдельную временную метрику:

```text
memory_pressure.free_percent
memory_pressure.pressure_percent
```

В macOS использовать системный:

```bash
memory_pressure
```

или:

```bash
memory_pressure -Q
```

и извлекать:

```text
System-wide memory free percentage
```

`memory_pressure` действительно выводит этот системный показатель.

Для визуализации определить:

```text
pressure_percent = 100 - free_percent
```

Например:

```text
System-wide memory free percentage = 34 %

pressure_percent = 66 %
```

В коде обязательно явно назвать это производной метрикой.

Например:

```python
memory_pressure_normalized_percent
```

Не утверждать, что это внутреннее значение Apple Activity Monitor.

---

# 10. Четыре основные time-series

История должна содержать минимум четыре ряда:

```text
system_ram_percent
ollama_ram_bytes
memory_pressure_percent
context_percent
```

Можно дополнительно хранить:

```text
ollama_ram_percent
system_ram_bytes
context_used_tokens
swap_bytes
CPU
```

но четыре основных ряда обязательны.

---

# 11. Исторические диапазоны

UI должен иметь общий переключатель:

```text
[ 1m ] [ 15m ] [ 1h ]
```

Он должен одновременно менять диапазон всех графиков.

Например:

```text
               1m    15m    1h
                      ▲

System RAM
────────────────────────────────

Ollama RAM
────────────────────────────────

Memory Pressure
────────────────────────────────

Context
────────────────────────────────
```

После переключения диапазона все графики должны отображать один и тот же временной интервал.

---

# 12. График System RAM

Показывать:

```text
0–100 %
```

Y-axis:

```text
RAM utilization %
```

Текущее значение показывать отдельно:

```text
System RAM
79.2 %

38.0 / 48 GB
```

---

# 13. График Ollama RAM

Основной график:

```text
GB
```

Например:

```text
Ollama RAM
24.3 GB

24 ┤              ╭────────
20 ┤         ╭────╯
16 ┤─────────╯
```

Дополнительно в tooltip:

```text
24.3 GB
50.6 % of system RAM
12:45:32
```

---

# 14. График Memory Pressure

Диапазон:

```text
0–100 %
```

Текущее значение:

```text
Memory Pressure
69 %
```

Tooltip должен при возможности показывать оба значения:

```text
Pressure: 69 %
System-wide memory free: 31 %
```

---

# 15. График Context

Диапазон:

```text
0–100 %
```

Текущее значение:

```text
Context
71.6 %

70.4k / 98.3k
```

Tooltip:

```text
70 412 tokens
71.6 %
12:45:32
```

---

# 16. Связь между графиками

Все графики должны использовать одну временную шкалу.

Это важная функция приложения.

Пользователь должен иметь возможность визуально увидеть:

```text
context grows
      ↓
Ollama RAM grows
      ↓
system RAM grows
      ↓
memory pressure grows
```

Поэтому X-axis у всех графиков должен быть синхронизирован.

---

# 17. API

`ollama-watch.py` должен предоставлять локальный HTTP API.

Например:

```text
127.0.0.1:11435
```

Порт не должен конфликтовать с Ollama:

```text
11434
```

---

## Current status

```http
GET /api/status
```

Пример:

```json
{
  "timestamp": 1790672400,

  "ollama": {
    "online": true,
    "version": "0.x.x"
  },

  "model": {
    "name": "qwen3.6:35b-a3b-coding"
  },

  "system_memory": {
    "total_bytes": 51539607552,
    "used_bytes": 40802189312,
    "used_percent": 79.17
  },

  "ollama_memory": {
    "used_bytes": 25769803776,
    "used_percent_of_system_ram": 50.0
  },

  "memory_pressure": {
    "free_percent": 31,
    "pressure_percent": 69
  },

  "context": {
    "used_tokens": 70412,
    "max_tokens": 98304,
    "used_percent": 71.63
  }
}
```

---

# 18. History API

Добавить:

```http
GET /api/history?range=1m
GET /api/history?range=15m
GET /api/history?range=1h
```

Пример:

```json
{
  "range": "15m",

  "samples": [
    {
      "timestamp": 1790672400,
      "system_ram_percent": 72.4,
      "ollama_ram_bytes": 22649241600,
      "ollama_ram_percent": 43.9,
      "memory_pressure_percent": 54,
      "context_used_tokens": 32000,
      "context_percent": 32.5
    }
  ]
}
```

---

# 19. Downsampling

Monitoring Core хранит исходные samples с интервалом около:

```text
2 seconds
```

Не нужно терять исходную историю при сборе.

Однако UI не обязан рисовать все точки.

Для рендеринга допустимо downsampling:

```text
1m  → все точки
15m → все точки или ~300–450
1h  → ~300–600 points
```

Downsampling должен сохранять форму графика.

Не использовать простое случайное удаление точек.

Допустим bucket aggregation:

```text
min
max
average
```

или другой простой детерминированный алгоритм.

---

# 20. Goose App

Goose App должен быть отдельным resizable окном.

Технологии:

```text
HTML
CSS
Vanilla JavaScript
Canvas или SVG
```

Не использовать runtime dependencies:

```text
React
Vue
Svelte
npm packages
external chart libraries
```

---

# 21. Предлагаемый интерфейс

```text
┌──────────────────── Ollama Monitor ───────────────────┐
│                                                       │
│ ● ONLINE     qwen3.6:35b-a3b-coding      Ollama x.x  │
│                                                       │
│                  [ 1m ] [ 15m ] [ 1h ]               │
│                                                       │
│ SYSTEM RAM                                            │
│ 38.0 / 48 GB                              79.2 %     │
│        ╭────────╮                                     │
│ ───────╯        ╰────────────                        │
│                                                       │
│ OLLAMA RAM                                            │
│ 24.3 GB                                    50.6 %     │
│             ╭────────────────                          │
│ ────────────╯                                         │
│                                                       │
│ MEMORY PRESSURE                                       │
│ 69 %                                                  │
│                   ╭────────────                       │
│ ──────────────────╯                                   │
│                                                       │
│ CONTEXT                                               │
│ 70.4k / 98.3k                             71.6 %     │
│        ╭────────────────────                          │
│ ───────╯                                              │
│                                                       │
│ Last update: 12:45:32                                 │
└───────────────────────────────────────────────────────┘
```

---

# 22. Hover / tooltip

Наведение на любой график должно показывать sample.

Например:

```text
12:42:18

System RAM       78.2 %
Ollama RAM       23.7 GB
Memory Pressure  64 %
Context          68.3 %
```

Желательно синхронизировать положение курсора между всеми четырьмя графиками.

То есть наведение на одну временную точку подсвечивает соответствующую точку остальных графиков.

---

# 23. Offline states

Разделять состояния:

```text
Monitoring backend unavailable
Ollama offline
Ollama online / no model loaded
Model loaded
Context metric unavailable
```

Например:

```text
Collector     ONLINE
Ollama        ONLINE
Model         NONE
```

Отсутствие модели не является ошибкой мониторинга.

---

# 24. Отсутствующие значения

Если конкретную метрику определить невозможно:

```json
null
```

Не использовать:

```text
0
```

поскольку `0` является валидным измерением.

Графики должны уметь отображать разрывы данных.

---

# 25. Безопасность

HTTP server:

```text
bind = 127.0.0.1
```

Никогда:

```text
0.0.0.0
```

API read-only.

Не создавать endpoints для:

```text
shell execution
model deletion
arbitrary command execution
system configuration
```

---

# 26. Предлагаемая структура проекта

Допустима архитектура:

```text
project/
│
├── ollama-watch.py
│
├── ollama_monitor/
│   ├── __init__.py
│   ├── collector.py
│   ├── history.py
│   ├── memory.py
│   ├── context.py
│   ├── ollama.py
│   └── api.py
│
├── app/
│   └── ollama-monitor.html
│
└── README.md
```

При этом:

```text
ollama-watch.py
```

остаётся entry point.

Например:

```bash
python3 ollama-watch.py
```

может запускать мониторинг и API.

Допустимо добавить:

```bash
python3 ollama-watch.py --server
python3 ollama-watch.py --cli
```

если это упрощает архитектуру.

---

# 27. Работа в фоне

Monitoring loop не должен зависеть от того, открыт ли Goose App.

Желаемое поведение:

```text
ollama-watch.py running
        │
        ├── collects samples continuously
        ├── maintains 1h history
        └── exposes HTTP API

Goose App closed
        ↓
history continues

Goose App opened
        ↓
GET /api/history?range=1h
        ↓
graph immediately populated
```

Это важнее, чем хранить историю непосредственно в JavaScript.

---

# 28. Persistence

На первом этапе запись истории на диск не обязательна.

Использовать:

```text
in-memory ring buffer
```

После перезапуска `ollama-watch.py` допускается потеря истории.

Архитектура `history.py` должна позволять позднее добавить SQLite или другой persistence backend без изменения Goose App.

---

# 29. Дополнительные метрики

Архитектура должна позволять позднее добавить:

```text
swap usage
swap in/out rate
CPU utilization
tokens/sec
prompt tokens
generated tokens
request duration
model load time
KV cache size
disk I/O
GPU / Metal metrics
```

Но они не должны задерживать реализацию четырёх основных графиков.

---

# 30. Definition of Done

Реализация считается законченной, когда:

1. `ollama-watch.py` работает как monitoring service.
2. Метрики обновляются примерно раз в 2 секунды.
3. Хранится история минимум за последний час.
4. Goose App запускается отдельным окном.
5. Доступны диапазоны `1m / 15m / 1h`.
6. Есть график общей утилизации RAM.
7. Есть график использования RAM Ollama.
8. Есть график Memory Pressure.
9. Есть график заполнения context.
10. Все четыре графика используют синхронизированную временную шкалу.
11. Отображается текущее значение каждой метрики.
12. Context показывает `used / max`.
13. Ollama RAM учитывает реальные Ollama/model-runner процессы.
14. История продолжает собираться при закрытом Goose App.
15. Offline Ollama не ломает collector.
16. Перезапуск Ollama автоматически определяется.
17. Пропущенные измерения представлены как `null`.
18. Backend слушает только `127.0.0.1`.
19. Goose App не запускает shell-команды.
20. Исходный код самого Goose изменять не требуется.

---

# 31. Порядок реализации

### Шаг 1

Изучить существующий:

```text
ollama-watch.py
```

и понять текущие способы определения:

```text
Ollama RAM
context
model
processes
```

### Шаг 2

Рефакторизовать код в reusable monitoring core.

### Шаг 3

Добавить:

```text
system RAM
Ollama RAM
memory pressure
context
```

в единый snapshot.

### Шаг 4

Добавить ring buffer минимум на 1 час.

### Шаг 5

Создать:

```text
/api/status
/api/history
```

### Шаг 6

Проверить API независимо от Goose.

### Шаг 7

Создать Goose App с текущими показателями.

### Шаг 8

Добавить четыре графика.

### Шаг 9

Добавить общий:

```text
1m / 15m / 1h
```

selector.

### Шаг 10

Добавить synchronized hover и обработку offline/null states.

---

# 32. Главный архитектурный принцип

Не строить:

```text
ollama-watch.py → terminal logic

и отдельно

Goose App → собственная monitoring logic
```

Вместо этого:

```text
                   ┌──────────────────┐
                   │ Monitoring Core  │
                   └────────┬─────────┘
                            │
               ┌────────────┼────────────┐
               │            │            │
               ▼            ▼            ▼
             CLI          HTTP API     History
                            │
                            ▼
                        Goose App
```

Все интерфейсы должны использовать один и тот же набор метрик и одну систему расчётов.