# Mite — Code Review Findings

**Date:** 2026-06-28
**Scope:** Grounded review of `src/` (workers, core, api). Every finding below was verified by reading the actual source; line references point at the exact code. Items I previously claimed but could **not** substantiate are listed under [Retracted claims](#retracted-claims) so you can ignore them.

**Method:** Read each module end-to-end, confirmed the concurrency model from `supervisord.conf` (one instance per worker) and `server.py` (`threads=10`), and traced the alert/classification data path across `processor.py`, `ai_discovery.py`, and `routes_rules.py`.

## Severity legend

| Level | Meaning |
|-------|---------|
| 🔴 Bug | Broken at runtime today; verified crash/incorrect behavior |
| 🟠 Behavior | Works, but contradicts documented/intended behavior |
| 🟡 Improvement | Correct today, but a real robustness/quality risk |
| 🔵 Note | Design observation, low urgency |

---

## 🔴 BUG-1 — UDP listener crashes on startup (`NameError`)

**File:** [src/workers/udp_listener.py](src/workers/udp_listener.py#L77)
**Confidence:** Certain (verified import + call sites).

The module imports the new consolidated helpers:

```python
# line 21
from src.core.settings_loader import get_int_setting, get_float_setting
```

…but `run_udp_listener()` still calls the **old** underscore-prefixed names that no longer exist:

```python
# lines 77-83
batch_size = _get_int_setting("udp_batch_size", UDP_BATCH_SIZE_DEFAULT)          # line 77
batch_flush_interval = _get_float_setting(                                       # line 78
    "udp_batch_flush_interval_seconds", UDP_BATCH_FLUSH_INTERVAL_DEFAULT
)
udp_recv_buffer = _get_int_setting(                                              # line 81
    "udp_recv_buffer_bytes", UDP_RECV_BUFFER_DEFAULT, min_value=65536
)
```

**Impact:** `_get_int_setting`/`_get_float_setting` are undefined → `NameError` the moment `run_udp_listener()` runs, before the socket loop. **UDP syslog ingestion (port 1514) is completely non-functional.**

**Root cause:** The settings-loader consolidation removed the local helper functions and renamed the imports, but did not update these three call sites.

**Fix:** Rename the three calls to `get_int_setting` / `get_float_setting`. The new `get_int_setting(key, default, min_value=1)` accepts the `min_value=` keyword ([settings_loader.py L14](src/core/settings_loader.py#L14)), so line 81 works unchanged after the rename.

---

## 🔴 BUG-2 — AI worker crashes after first cycle (`NameError`)

**File:** [src/workers/ai_worker.py](src/workers/ai_worker.py#L78)
**Confidence:** Certain (verified import + call sites).

Import is correct and used correctly once:

```python
# line 11
from src.core.settings_loader import get_int_setting
# line 22 (correct usage)
ai_batch_size = get_int_setting("ai_batch_size", AI_BATCH_SIZE_DEFAULT)
```

But two call sites still use the old name:

```python
# line 36 — inside run_regex_review_cycle_if_due()
review_interval = _get_int_setting(
    "ai_regex_review_interval_seconds", AI_REGEX_REVIEW_INTERVAL_DEFAULT
)

# line 78 — main loop, OUTSIDE the try/except
sleep_seconds = _get_int_setting(
    "ai_discovery_interval_seconds", AI_DISCOVERY_INTERVAL_DEFAULT
)
```

**Impact (two distinct effects):**
1. **Line 36** makes `run_regex_review_cycle_if_due()` raise every time. At startup it's wrapped in try/except so it's silently swallowed (logged as a startup failure); inside the loop it's also swallowed. Net effect: **the periodic regex-efficiency review never runs**, and `ai_efficiency_score` is never refreshed.
2. **Line 78** is **outside** the loop's try/except. After the first iteration the worker raises `NameError` and **the AI worker process dies** (supervisord may restart it, which then dies again on the next cycle → crash loop). Retry classification of pending patterns effectively stops.

**Fix:** Rename both calls to `get_int_setting`.

> Note: `tcp_listener.py` was already updated correctly and uses `get_int_setting`/`get_float_setting` — only `udp_listener.py` and `ai_worker.py` were missed.

---


## 🟡 IMPROVE-4 — Debug content logged at ERROR level

**File:** [src/workers/processor.py](src/workers/processor.py#L328)
**Confidence:** Certain.

```python
log_error(logger, f"[DEBUG] Pattern {pattern_id} regex: {debug_regex!r}")
log_error(logger, f"[DEBUG] Pattern {pattern_id} message: {debug_message!r}")
```

These `[DEBUG]` lines go through `log_error`, polluting error logs and inflating any error-rate monitoring.

**Recommendation:** Add a `log_debug` wrapper in [src/utils/locallogging.py](src/utils/locallogging.py) (gated by a `debug_logging` setting) and use it here, or downgrade to `log_info`.

NOTE FOR AI - JUST MAKE THIS A LOG_INFO AND CHANGE THE LOG TO INFO NOT DEBUG
---

## 🔵 NOTE-1 — Minor code smells (verified, low urgency)

- **Redundant condition** — [processor.py L466](src/workers/processor.py#L466): `if alert_id and effective == "critical":` is nested inside `if effective == "critical":`; the second comparison is always true.
- **Dead alternation** — [processor.py L110](src/workers/processor.py#L110): `re.sub(r"<X>|[^A-Za-z\s]", " ", ...)` strips `<X>`, but no stage emits `<X>` (placeholders are `<IP>`, `NUMBER`, etc.). The `<X>|` branch is dead.
- **Confusing constant name** — [udp_listener.py L29](src/workers/udp_listener.py#L29): `UDP_RECV_BUFFER_DEFAULT = SYSLOG_TCP_RECV_BUFFER_SIZE` uses a "TCP" constant for the UDP buffer (same value, misleading name).
- **Logging coupled to DB** — [locallogging.py L16](src/utils/locallogging.py#L16): each log line may read a SQLite setting (2s-cached) to decide verbosity. It works, but logging depending on the DB is an unusual direction and adds load under heavy log volume. Note to AI just increase the cache to 60 seconds. 
- **Docs vs. code drift** — classifications: docs mention `critical`/`noise` from AI, but AI emits only `high`/`medium`/`low`; `critical`/`noise` are reachable only via user override ([routes_rules.py](src/api/routes_rules.py) `VALID_CLASSIFICATIONS = {"critical","high","medium","low","noise"}`).

---
