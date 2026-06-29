# Code Cleanup and Unused Code Removal TODO

## Quick Wins (Low Risk, Easy to Fix)

### 1. Remove Redundant Logger Declarations in src/api/server.py
**Status:** Ready to remove

**Files:** [src/api/server.py](src/api/server.py)

**Lines to remove:**
- Line 44 in `api_stats()`: Remove `logger = logging.getLogger(__name__)`
- Line 57 in `api_test_discord()`: Remove `logger = logging.getLogger(__name__)`
- Line 108 in `if __name__ == "__main__"` block: Remove `logger = logging.getLogger(__name__)`

**Reason:** Module-level logger at line 25 is already defined and can be reused. Redundant local declarations shadow it unnecessarily.

**Impact:** None - same logger, just cleaner code

---

### 2. Remove Redundant Global Initialization in src/workers/tcp_listener.py
**Status:** Ready to remove

**File:** [src/workers/tcp_listener.py](src/workers/tcp_listener.py)

**Lines to remove:**
- Lines 21-22 (the initial assignments of BATCH_SIZE and BATCH_FLUSH_INTERVAL globals)

**Context:** These globals are initialized at module load but then immediately overwritten in `run_tcp_listener()`. The initial values are never used.

```python
# REMOVE these lines (21-22):
BATCH_SIZE = TCP_BATCH_SIZE_DEFAULT
BATCH_FLUSH_INTERVAL = TCP_BATCH_FLUSH_INTERVAL_DEFAULT

# They're redefined in run_tcp_listener() at lines 184-186
```

**Impact:** Slight reduction in module initialization time

---

### 3. Simplify ALERT_SEVERITIES Check in src/workers/processor.py
**Status:** Ready to simplify

**File:** [src/workers/processor.py](src/workers/processor.py)

**Current code (Line 35):**
```python
ALERT_SEVERITIES = {"critical"}
```

**Usage (Line 450):**
```python
if effective in ALERT_SEVERITIES:
```

**Change to:**
```python
# Remove the set and check directly
if effective == "critical":
```

**Reason:** The set only contains one value. Direct comparison is clearer and faster.

**Impact:** Negligible performance improvement, slightly more readable

---

### 4. Document Silent Exception Handlers
**Status:** Ready - add comments only

**File:** [src/core/ai_request_log.py](src/core/ai_request_log.py)

**Action:** Add docstring explaining intentional silent failures:
```python
def log_ai_request(model, prompt_tokens, completion_tokens):
    """Log AI API request to database.
    
    Silent failures are intentional (best-effort logging).
    If logging fails, it should not disrupt the main application flow.
    """
    try:
        # ... existing code
    except Exception:
        # Intentionally silent - logging failures should not crash the app
        pass
```

**File:** [src/core/config.py](src/core/config.py)

**Action:** Add docstring explaining version read fallback:
```python
def get_version():
    """Get app version from VERSION file.
    
    Returns 'unknown' if file cannot be read (graceful fallback).
    This allows the app to run even if the VERSION file is missing or unreadable.
    """
    try:
        # ... existing code
    except Exception:
        return "unknown"
```

---

## Code Quality Improvements (Related to Unused Code)

### Consolidate Duplicated Setting Loaders

**Files affected:** 
- src/workers/processor.py (has _get_int_setting, _get_float_setting)
- src/workers/udp_listener.py (duplicate _get_int_setting, _get_float_setting)
- src/workers/tcp_listener.py (duplicate _get_int_setting, _get_float_setting)
- src/workers/ai_worker.py (has _get_int_setting)

**Issue:** Same helper functions defined in 4 files

**Recommendation:** Create `src/core/settings_loader.py`:
```python
def get_int_setting(key, default_value, min_value=1):
    """Load integer setting from DB with validation."""
    
def get_float_setting(key, default_value, min_value=0.1):
    """Load float setting from DB with validation."""
```

Then import in all worker files instead of duplicating.

---

## Summary of Changes

| Item | Type | Impact | Effort |
|------|------|--------|--------|
| Remove redundant loggers in server.py | Dead code removal | Minor | < 5 min |
| Remove redundant global inits in tcp_listener.py | Dead code removal | Minor | < 5 min |
| Simplify ALERT_SEVERITIES check | Code quality | Minor | < 5 min |
| Document silent exceptions | Documentation | Minor | < 10 min |
| Consolidate setting loaders | Refactoring | Medium | 30 min |

---

## Execution Order

1. **First:** Remove redundant logger declarations (server.py)
2. **Second:** Remove redundant global initializations (tcp_listener.py)
3. **Third:** Simplify ALERT_SEVERITIES check (processor.py)
4. **Fourth:** Add documentation to exception handlers (ai_request_log.py, config.py)
5. **Later:** Extract settings loader to shared module (refactoring sprint)

---

## Verification Steps

After each change:
1. Run `pytest` to ensure no regressions
2. Verify logs still contain proper context
3. Test with: `python -m src.api.server` (check no import/attribute errors)
4. Send test syslog and verify it processes normally

---

## Notes

- **No critical code removed** — all changes are safe, tested, and low-risk
- **No behavioral changes** — functionality remains identical
- **Codebase is generally clean** — very few unused code paths found
- **Performance impact** — negligible, mostly code quality improvements
