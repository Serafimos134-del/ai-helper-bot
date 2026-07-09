# Trader DNA Architecture

Статус: **проектирование, код не писался**. Документ — результат инженерного
аудита текущего состояния `services/`, `ai/`, `core/` (после Этапа 8,
коммит `0a74802`) и архитектурного предложения для Этапа 9 плана AI Trading
Core. Реализация начинается только после подтверждения этого документа.

---

## 1. Аудит существующего Trader Memory

### 1.1 Таблицы БД (фактическое состояние)

| Таблица | Что хранит | Гранулярность | Заполняется |
|---|---|---|---|
| `closed_trades` | Финальный снимок сделки: `entry_price`/`exit_price`/`realized_pnl`/`leverage`/`stop_loss`/`take_profit`/`risk_reward`/`holding_minutes`/`market_trend`/`setup_type`/`ai_score`/`score_breakdown`(JSON)/`market_review`/`risk_review`/`psychology_review`/`judge_verdict` | 1 строка на сделку | Автоматически при закрытии (`auto_sync.py`) |
| `open_trades` | Текущее состояние открытой позиции: `entry_price`/`leverage`/`stop_loss`/`take_profit`/`idea`/`invalidation_sl`/`dca_count`/`tp_zones` | 1 строка на позицию, **перезаписывается** каждый sync-цикл | Автоматически, без истории изменений |
| `trade_events` | Хронологический журнал: `open_analysis` → N × `companion` → `close_analysis`, `payload` = JSON с полным AI-разбором на каждом шаге | N строк на сделку (Этап 8) | Автоматически |
| `behavior_events` | Обнаруженные поведенческие триггеры: `revenge_trading`/`overtrading`/`panic_close`/`fomo`, `severity`, `metadata` (JSON, включает `symbol`, но **не `order_id`**) | N строк, не привязаны к конкретной сделке напрямую | Автоматически (`BehaviorEngine`, живёт с начала проекта) |
| `trader_memory` | Простые key/value счётчики: `global.total_trades`, `ticker.{SYMBOL}_wins`, `direction.{SIDE}_total`, `holding.avg_minutes` | Скаляры, инкрементально | Автоматически (`MemoryEngine.update()`) |

### 1.2 Существующие движки аналитики — важная находка

В проекте уже есть **три независимых, работающих, но не связанных между
собой аналитических инструмента**, каждый из которых закрывает часть того,
что просят в ТЗ на Trader DNA:

| Движок | Что считает | Где используется | Пересекается с ТЗ |
|---|---|---|---|
| `services/behavior_engine.py` (`BehaviorEngine`) | Детерминированные правила: revenge trading (крупная позиция после серии убытков), overtrading (>3 сделок за 2 часа), panic close (закрытие в убыток за <5 мин без срабатывания SL), FOMO (вход после резкого движения) | Живёт в `auto_sync.py` (реальное время, при открытии/закрытии), плюс команда `/test_behavior` | **Behavior Analysis** — почти дословно то, что просит ТЗ |
| `services/performance_engine.py` (`PerformanceEngine`) | Winrate, profit factor, avg win/loss, R:R, серии побед/убытков, производительность по времени суток, по символу, по плечу | Команда `/stats` (`trading_stats.py`) | **Performance Analysis** — почти дословно то, что просит ТЗ |
| `services/coach_engine.py` (`CoachEngine`) | Берёт отчёт `PerformanceEngine`, строит LLM-промпт, находит худшую сессию/худший символ, просит модель выдать "главную проблему" + 3 правила | Команда `/coach` | Прообраз **Feedback Engine**, но однонаправленный: текст для пользователя, не влияет на `JudgeAgent` |

**Вывод №1**: Trader DNA не должен заново считать winrate/profit factor/RR
или заново детектировать revenge trading/FOMO — это уже посчитано и
работает. Задача Этапа 9 — **не построить пятую систему аналитики, а
консолидировать три существующие в одну структурированную модель и
подключить её к `AIOrchestrator`**, чего сегодня не делает ни одна из них.

### 1.3 Данные, которые доходят до агентов — что на самом деле мёртвое

При чтении `ai/context_builder.py` обнаружена **четвёртая, независимая
реализация** поведенческого скоринга — и она не используется:

```
context_builder.py:_calculate_behavior_metrics()
  → считает revenge_score / fomo_score / overtrading_score /
    premature_exit_score / tilt_probability из последних 10 закрытых сделок
  → кладёт в context["history"]
```

Проверка потребителей `context["history"]` показала: этот объект читают
только `RiskAgent._rule_based_analysis()` и `PsychologyAgent._rule_based_analysis()`
— а эти методы вызываются **только когда `mode` не равен `open`/`post_trade`/`setup`**.
Но `ConsensusEngine._run_agents_parallel()` всегда выставляет
`context['mode']` в одно из этих трёх значений для всех вызовов
`AIOrchestrator` (`review_open_position`/`review_closed_trade`/
`evaluate_new_setup` — то есть **100% живого трафика**). Более того, там,
где `history` всё-таки читается (rule-based fallback), используется не
`revenge_score`/`fomo_score` и т.д., а свой, **пятый**, независимый расчёт
внутри `ai/psychology_engine.py:PsychologyEngine.assess()`.

**Вывод №2**: `_calculate_behavior_metrics()` в `context_builder.py` —
мёртвый код (~70 строк). Его не нужно чинить или переиспользовать — его
стоит удалить в ходе интеграции Trader DNA, чтобы не остался шестым
источником той же логики.

Аналогично: `context["memory"]` (текст "ПРОФИЛЬ ТРЕЙДЕРА" из
`_get_memory_context_sync()`, единственное реальное упоминание
персонализации в сегодняшнем консилиуме) доходит только до **финального
текста ответа пользователю** (`result['memory']` → `_build_response()`).
Ни один агент, ни `JudgeAgent.synthesize()` его не видит. Сегодня
персонализация в AI Trading Core — **чисто косметическая приписка после
того, как решение уже принято**, а не входной сигнал для решения.

**Вывод №3** (главный для архитектуры ниже): чтобы Trader DNA реально
работал так, как в примере из ТЗ ("Trader DNA: исторически пользователь
плохо работает с BTC после импульса" → "Judge: снизить оценку"), сигнал
должен попадать **в `JudgeAgent.synthesize()` до того, как он посчитает
`final_score`**, а не в текст после. Сегодня `synthesize()` принимает
`market_json, risk_json, psychology_json, mode, trade_score, confidence,
disagreement` — параметра под Trader DNA там нет.

### 1.4 Каких данных не хватает

| Не хватает | Почему нужно | Как закрыть |
|---|---|---|
| Связь `behavior_events` ↔ конкретная сделка | `metadata` содержит `symbol`, но не `order_id` — нельзя надёжно джойнить "этот revenge trading был вот на этой закрытой сделке" | Добавить nullable `order_id` в `behavior_events` |
| История изменений `stop_loss`/`take_profit`/`dca_count` открытой позиции | `open_trades` перезаписывается каждый sync-цикл — невозможно узнать, послушал ли трейдер рекомендацию `position_watch_job` (перенёс стоп в БУ или нет) — а это ядро "нарушение стратегии" из Behavior Analysis | Логировать `trade_events` типа `position_adjusted` при изменении SL/TP/DCA в `auto_sync.py` (переиспользовать существующую таблицу, не создавать новую) |
| Плотные данные по `setup_type` | Колонка есть, но заполняется только вручную через кнопки `set_setup_*` — для большинства сделок пусто, "лучшие сетапы" считать не по чему | При закрытии, если `setup_type` не задан вручную, подставлять автоматически из `structure`/`trend`, уже посчитанных в `position_plan`/`trade_plan` (Этапы 4/6) |
| Единая структурированная "модель трейдера" | Есть только разрозненные счётчики (`trader_memory`) и **пересчитываемые с нуля при каждом вызове** `/stats` метрики (`PerformanceEngine`) — нет одной таблицы с текущим состоянием профиля, которую можно быстро прочитать при построении контекста для консилиума | Новая таблица `trader_profile` (см. §9) |
| Хранилище найденных закономерностей | "После двух стопов подряд повышается вероятность ошибки" — это не сырые данные, а вывод; сегодня такие выводы нигде не сохраняются, каждый раз их пришлось бы пересчитывать | Новая таблица `trader_patterns` (см. §9) |
| Калибровка: предсказание vs факт | `open_analysis` в `trade_events` хранит `judge_verdict`/`final_score` на входе, `closed_trades` — факт (`realized_pnl`). Данные для сопоставления есть (общий `order_id`), но сама метрика ("когда Judge говорил ENTER с score>80, какой был реальный winrate") нигде не считается | Вычисляется на чтении в Trader DNA Engine, новых полей не требует |

---

## 2. Trader Profile

Структурированное представление трейдера, обновляемое инкрементально при
каждом закрытии сделки (тем же способом, что уже делает `MemoryEngine`, но
в одну таблицу вместо разрозненных key/value):

| Поле | Источник |
|---|---|
| `preferred_symbols` (топ-N по частоте) | `closed_trades.symbol` |
| `avg_risk_percent`, `avg_leverage` | `closed_trades.risk_percent`/`leverage` |
| `avg_rr` | `closed_trades.risk_reward`, сверяется с `PerformanceEngine._risk_reward_analysis` |
| `avg_holding_minutes` | `closed_trades.holding_minutes` |
| `best_symbol` / `worst_symbol` (по PnL, мин. 2 сделки) | `PerformanceEngine._setup_performance` |
| `best_session` / `worst_session` | `PerformanceEngine._session_performance` |
| `winrate`, `profit_factor`, `total_trades` | `PerformanceEngine._basic_metrics` / `db.get_stats()` |
| `current_loss_streak`, `current_win_streak`, `max_loss_streak` | `PerformanceEngine._streak_analysis` |
| `discipline_index` (0–100, обобщённая метрика соблюдения плана) | Новое: доля `position_adjusted`-событий, совпавших с рекомендацией `position_watch_job`, из `trade_events` |
| `last_computed_at` | — |

Это не новый расчёт "с нуля" — это одна таблица, куда пишутся уже
посчитанные (и проверенные в бою) значения из `PerformanceEngine`, вместо
того чтобы гонять полный `_full_report()` по всем сделкам при каждом
обращении консилиума к профилю трейдера.

---

## 3. Behavior Analysis

**Не пересчитывать заново** — `BehaviorEngine` уже детектирует revenge
trading / overtrading / panic close / FOMO в реальном времени и пишет в
`behavior_events`. Задача Trader DNA — **агрегировать** эти события во
времени (а не детектировать заново):

- частота каждого паттерна за последние N сделок/дней;
- триггерные условия, специфичные для этого трейдера (например: FOMO
  случается конкретно на альткоинах, но не на BTC — видно по `symbol` в
  `metadata`, если добавить агрегацию по нему);
- новый паттерн, которого нет в `BehaviorEngine` (он детектирует только
  единичные события, не последовательности): **"нарушение собственного
  плана"** — сверка `position_adjusted`-событий (см. §1.4) с рекомендациями
  `position_watch_job` из `trade_events` типа `companion`. Это тот самый
  "слишком близкий стоп" / "нарушение стратегии" пункт из ТЗ, которого в
  `BehaviorEngine` физически нет, потому что для него нужна история
  изменений позиции, которой раньше не было.

---

## 4. Performance Analysis

**Полностью переиспользует `PerformanceEngine`** — winrate, profit factor,
средняя прибыль/убыток, лучшие инструменты, лучшие сетапы (после того как
`setup_type` станет заполняться автоматически, см. §1.4), худшие сценарии
(символ/сессия/сетап с наихудшим profit factor). Trader DNA добавляет
только один новый срез, которого в `PerformanceEngine` нет — **производительность
по совпадению с трендом**: `side` сделки vs `market_trend` из
`closed_trades` (оба поля уже есть) → "сделки по тренду показывают X%
winrate против Y% против тренда". Не требует новых данных, только новый
`GROUP BY` в существующей таблице.

---

## 5. Pattern Recognition

Здесь единственная категория, для которой в проекте пока нет вообще
никакого прообраза (в отличие от Behavior/Performance). Паттерны — это
**выводы более высокого порядка**, полученные сопоставлением уже
посчитанных метрик друг с другом во времени, например:

- "После 2 стопов подряд вероятность убыточной сделки увеличивается на N
  п.п." — сопоставление `PerformanceEngine._streak_analysis` с исходом
  *следующей* сделки после серии.
- "BTC-сделки по тренду результативнее контртрендовых" — см. §4.
- "Сделки с `discipline_index` ниже среднего для этого трейдера закрываются
  хуже" — сопоставление нового поля из §2 с `realized_pnl`.

Найденные паттерны **сохраняются** (не пересчитываются при каждом запросе)
в `trader_patterns` — с полем `active`, чтобы устаревший паттерн (например,
если трейдер перестал так себя вести) можно было деактивировать, не удаляя
историю. Обновляется периодически (см. План, Этап 3), не на каждую сделку —
для статистической значимости паттерну нужна выборка, пересчёт на каждое
закрытие сделки бессмыслен при малом числе сделок.

---

## 6. Feedback Engine

Это точка, где Trader DNA перестаёт быть просто аналитикой и начинает
**влиять на решения `AI Trading Core`**. Ровно то, чего сегодня не хватает
(см. §1.3, Вывод №3).

Механизм:

1. При построении контекста для консилиума (`context_builder.py`, для всех
   трёх режимов — `open`/`post_trade`/`setup`) подмешивается новый блок
   `trader_dna`: активные `trader_patterns`, релевантные текущему символу/
   направлению, + ключевые поля `trader_profile`.
2. `MarketAgent`/`RiskAgent`/`PsychologyAgent` получают `trader_dna` в
   промпте — модель может явно написать "исторически у тебя плохой winrate
   на BTC после импульса" в своём анализе (как в примере ТЗ).
3. `JudgeAgent.synthesize()` получает **новый параметр** `dna_adjustment:
   dict` (например `{"score_delta": -15, "reason": "..."}`) — считается
   детерминированно (не LLM) новым движком `ai/engines/trader_dna_engine.py`
   на основе активных паттернов, применяется к `final_score` **после**
   базового расчёта, до определения `verdict`. Добавляется в `warnings`,
   если `score_delta` заметный.

Это не создаёт параллельную систему принятия решений — `JudgeAgent`
остаётся единственным местом, где считается `final_score`; Trader DNA
только предоставляет ему один дополнительный вход, как уже делают
`risk_score`/`psychology_score`.

---

## 7. Архитектура агентов: Trader DNA Agent vs контекст

### Вариант A — отдельный агент в консилиуме

Новый `TraderDNAAgent`, работающий параллельно с `MarketAgent`/`RiskAgent`/
`PsychologyAgent` через LLM, четвёртый вход в `JudgeAgent.synthesize()`.

Минусы, специфичные для этого проекта (не абстрактные):
- Trader DNA — это **чтение и агрегация уже посчитанных детерминированных
  метрик** (winrate, streak, паттерны), а не что-то, требующее рассуждения
  LLM. Все остальные детерминированные вычисления в проекте (`ScoringEngine`,
  `RiskRuleEngine`, `TradeScorer`) намеренно **не** оформлены как LLM-агенты
  — именно поэтому в `ai/` есть отдельный `ai/engines/` для них. Agent как
  паттерн в этом кодбейзе зарезервирован под LLM-рассуждение с контекстом
  (`ai/agents/`), не под детерминированный подсчёт.
- Добавляет ещё один параллельный вызов с собственным таймаутом в
  `ConsensusEngine._run_agents_parallel()` — Trader DNA не нуждается в
  собственном тайм-ауте/деградации, так как не ходит в сеть (в отличие от
  Market/Risk/Psychology, у которых таймаут оправдан — они дергают LLM).

### Вариант B — Trader DNA как контекст для существующих агентов

Новый детерминированный движок `ai/engines/trader_dna_engine.py` (по
образцу `ScoringEngine`), который:
- строит `trader_dna` контекст в `context_builder.py` рядом с уже
  существующими `market`/`portfolio`/`history` (но **реально
  используемый** в промптах — в отличие от сегодняшнего мёртвого `history`,
  см. §1.3);
- считает `dna_adjustment` для `JudgeAgent.synthesize()`.

### Выбор: **Вариант B**

Обоснование, кроме архитектурной последовательности с `ScoringEngine`/
`RiskRuleEngine`:

1. **Соответствие принципу проекта** ("одно AI Trading Core, специализированные
   агенты внутри него") — Trader DNA не специализированный аналитик рынка/риска/
   психологии конкретной сделки, а *слой памяти*, общий для всех них. Контекст,
   а не ещё один голос в консилиуме.
2. **Данных не хватает не потому, что нет агента, а потому, что то, что уже
   есть, не подключено** (Вывод №1–3, §1) — добавление ещё одного LLM-агента
   не решает реальную проблему (несвязанность существующих движков), а
   решает несуществующую (нехватку ещё одного мнения).
3. **Дешевле и предсказуемее**: детерминированный расчёт — без LLM-таймаутов,
   без деградации, без расхождения между запусками на одних и тех же данных.
   `JudgeAgent` и так уже детерминированный — согласованная архитектура.
4. Вариант A не исключён навсегда: если позже понадобится **качественная**
   интерпретация паттернов (не просто "исторически плохо на BTC после
   импульса", а более тонкое рассуждение) — это можно добавить поверх
   Варианта B, скормив `trader_dna` контекст как дополнительный вход в уже
   существующий `MarketAgent`/`PsychologyAgent` (у них уже есть все
   переключатели `mode`), не обязательно заводить нового агента.

---

## 8. Совместимость с будущим Vision Engine

Trader DNA спроектирован как источник контекста, не связанный с тем, откуда
пришёл сетап (BingX API vs будущий Vision Engine, распознающий паттерны на
графике). Он оперирует только исходом и поведением: `closed_trades`,
`trade_events`, `behavior_events` — эти таблицы не знают и не должны знать,
был ли сетап найден вручную, через `/consilium new` или через будущий
анализ скриншота графика.

Когда появится Vision Engine, он естественно встанет в `context_builder.py`
рядом с `trader_dna` как ещё один источник контекста (`vision`), который
`MarketAgent`/`StrategyAdvisor` будут читать в промпте — по той же схеме,
по которой сегодня добавляется `trader_dna`. Ядро (`ConsensusEngine`,
`JudgeAgent`, `AIOrchestrator`) менять не придётся — то же расширение
контекста, что уже происходило четыре раза подряд (Этапы 4, 5, 6, 8) без
переписывания ядра.

Единственное, за чем нужно следить при реализации: **не давать Trader DNA
engine напрямую обращаться к BingX-специфичным полям** (например,
`entryPrice` camelCase) — только к уже нормализованным полям БД
(`entry_price`, snake_case). Это уже стандарт проекта (`normalize_position`/
`normalize_trade`), просто явно фиксируется как ограничение и для Trader
DNA.

---

## 9. Необходимые изменения в БД (сводка)

**Новые таблицы:**

```sql
CREATE TABLE trader_profile (
    user_id TEXT PRIMARY KEY DEFAULT 'default',
    total_trades INTEGER DEFAULT 0,
    winrate REAL DEFAULT 0,
    profit_factor REAL DEFAULT 0,
    avg_risk_percent REAL,
    avg_leverage REAL,
    avg_rr REAL,
    avg_holding_minutes REAL,
    best_symbol TEXT,
    worst_symbol TEXT,
    best_session TEXT,
    worst_session TEXT,
    current_loss_streak INTEGER DEFAULT 0,
    current_win_streak INTEGER DEFAULT 0,
    max_loss_streak INTEGER DEFAULT 0,
    discipline_index REAL,
    preferred_symbols TEXT,  -- JSON
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE trader_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT DEFAULT 'default',
    pattern_type TEXT NOT NULL,
    description TEXT NOT NULL,
    confidence REAL,          -- статистическая сила / размер выборки
    metadata TEXT,            -- JSON
    active INTEGER DEFAULT 1,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Изменения существующих таблиц (safe-migration механизм, уже есть в
`database.py`):**

- `behavior_events`: `+ order_id TEXT` (nullable) — джойн с конкретной
  сделкой.
- `closed_trades`: `+ setup_type_source TEXT` (`'manual'`/`'auto'`) —
  отличать вручную заданный сетап от подставленного автоматически, чтобы
  не путать точность источников в Performance Analysis.

**Новые события в `trade_events` (без изменения схемы — уже поддерживает
произвольные `event_type`):**

- `position_adjusted` — при изменении `stop_loss`/`take_profit`/`dca_count`
  открытой позиции в `auto_sync.py`, с `payload` вида `{"field": "stop_loss",
  "old": ..., "new": ..., "matched_recommendation": true/false}`.

---

## 10. Риски

| Риск | Смягчение |
|---|---|
| Дублирование существующей аналитики (создание пятой системы вместо консолидации трёх существующих) | Явно запрещено в §1.1–1.4 этого документа; `trader_profile` заполняется значениями из `PerformanceEngine`, не пересчитывает их заново |
| Данных мало на старте (Trader DNA v1 из документа-отчёта уже отмечался: "на реальных сделках истории пока мало") | `trader_patterns` формируется только при достаточной выборке (порог на `confidence`); при отсутствии паттернов `dna_adjustment` — нейтральный (`score_delta=0`), а не выдуманный |
| `JudgeAgent.synthesize()` — публичный интерфейс, уже используется в 3 местах (`review_open_position`/`review_closed_trade`/`evaluate_new_setup`) | Новый параметр `dna_adjustment` — с дефолтом `None`/нейтральным значением, обратная совместимость сохраняется, старые вызовы не ломаются |
| `position_adjusted`-события увеличат объём `trade_events` и частоту записи в БД на каждый sync-цикл (15s) | Писать событие только при *реальном* изменении значения (diff со старым состоянием), не на каждый цикл — та же дедупликация, что уже применена в `position_watch_job` (Этап 7) |
| Ошибка в паттерне (ложная корреляция на малой выборке) может необоснованно понижать score реальных хороших сделок | `dna_adjustment` ограничен по модулю (например, ±20 очков максимум) и всегда попадает в `warnings` с объяснением — трейдер видит, почему оценка снижена, а не просто получает более низкое число |
| Удаление мёртвого кода (`_calculate_behavior_metrics`) может задеть что-то незамеченное | Проверено грепом всех потребителей (§1.3) — потребителей вне `_rule_based_analysis` нет; перед удалением — ещё один grep-проход в момент реализации |

---

## 11. План реализации

**Этап 1. Изменения базы данных**
`trader_profile`, `trader_patterns` (новые таблицы), `behavior_events.order_id`,
`closed_trades.setup_type_source` (safe-migration, по существующему
паттерну в `database.py`). Без функционального кода — только схема +
методы `Database.get_trader_profile()`/`upsert_trader_profile()`/
`add_trader_pattern()`/`get_active_patterns()`.

**Этап 2. Сбор новых событий**
- `position_adjusted` в `auto_sync.py` (diff SL/TP/DCA при sync).
- Автоподстановка `setup_type` из `position_plan`/`trade_plan` при закрытии,
  если не задан вручную.
- `order_id` в `behavior_events` — прокинуть из вызывающего кода
  (`_check_behavior_on_open`/`_check_behavior_on_close` уже имеют доступ к
  сделке).

**Этап 3. Расчёт статистики**
`ai/engines/trader_dna_engine.py` — консолидирует чтение из
`PerformanceEngine`, `BehaviorEngine`-агрегатов и новых `position_adjusted`-
событий в единый расчёт `discipline_index` и обновление `trader_profile`.
Отдельная функция детекции `trader_patterns` (запускается периодически —
по расписанию в `core/scheduler.py`, не на каждую сделку, см. Риски).

**Этап 4. Формирование профиля трейдера**
Удаление мёртвого `_calculate_behavior_metrics()` из `context_builder.py`.
Новый метод `context_builder.py:_build_trader_dna_context()`, читающий
`trader_profile` + релевантные `trader_patterns` (по символу/направлению
текущего запроса).

**Этап 5. Интеграция в AI Trading Core**
- Подмешать `trader_dna` в контекст всех трёх режимов
  (`build_for_open_position`/`build_for_new_setup`/`build_for_closed_trade`).
- Прокинуть `trader_dna` в промпты `MarketAgent`/`RiskAgent`/`PsychologyAgent`.
- Новый параметр `dna_adjustment` в `JudgeAgent.synthesize()`, применяется
  к `final_score` после базового расчёта.
- `AIOrchestrator` не меняется структурно — расширение существующего
  контекста, тот же принцип, что и в Этапах 4/5/6/8.

Каждый этап — отдельный PR, с смоук-тестами по уже устоявшемуся в проекте
паттерну (мок на `ConsensusEngine`/`AIOrchestrator`, проверка на
синтетических данных), и обновлением `AUDIT.md`.
