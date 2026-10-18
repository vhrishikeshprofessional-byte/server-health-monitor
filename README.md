# Server Health Monitor

A lightweight, incremental, checkpointed server health monitor for log directories.

## Implementation Log

### Step 1: Repo foundation
- **What**: Initialized repository foundation with `.gitignore`, minimal `pyproject.toml` (packaging metadata, `src/` layout, standard library only, zero dependencies), and base `README.md`.
- **Why**: Establish foundational project metadata, packaging layout, and ignore rules to prevent committing transient files and reference artifacts from the outset.
- **Decisions**: Explicitly excluded `_reference/` to protect baseline assets. Chose modern `pyproject.toml` standard packaging targeting Python `>=3.11` without external runtime dependencies.
- **Verify**: TOML syntax parsed and verified using `tomllib`; repository structure inspected.

### Step 2: Add health_monitor package skeleton with version
- **What**: Created `src/health_monitor/__init__.py` exposing `__version__ = "0.1.0"` and package docstring.
- **Why**: Establish the Python package structure under `src/` layout so modules can be imported and packaging tooling can locate the package.
- **Decisions**: Single source of truth for versioning defined in `__init__.__version__` matching `pyproject.toml`.
- **Verify**: Verified importability and `__version__` resolution using Python CLI.

### Step 3: Add Severity, LogEvent, Fault, checkpoint and report types
- **What**: Created `src/health_monitor/models.py` defining pure dataclasses and enums: `Severity`, `LogEvent`, `ParseError`, `Fault`, `FileCheckpoint`, `ServerStatus`, and `ScanReport`.
- **Why**: Provide strongly typed, slot-optimized, pure data models for log events, fault lifecycle tracking, checkpoint state, and report generation without any I/O side effects.
- **Decisions**: Used `IntEnum` for `Severity` enabling threshold comparison (`severity >= threshold`); used frozen slotted dataclasses for immutability and memory efficiency, and mutable dataclass for `FileCheckpoint`.
- **Verify**: Executed unit verification on models validating severity ranking, fault triggering predicate, and dataclass instantiation.

### Step 4: Parse log lines with severity, fault ids and resolutions
- **What**: Created `src/health_monitor/parser.py`, unit test suite `tests/test_parser.py`, and configured dev dependencies alongside pytest settings in `pyproject.toml`.
- **Why**: Provide robust, untrusted-input parsing for log entries, explicit resolution lines, bounded previews for error reporting, and defensive length limits.
- **Decisions**: Treated logs strictly as untrusted input (never raise unhandled exceptions on parse failures); capped max line length to 4096 characters with 80-char sanitized preview; required `INFO` severity for explicit `RESOLVED id=<ID>` lines.
- **Verify**: Executed `python -m pytest` running 59 tests covering all severities, timestamp validation, ASCII-only regex constraints, error truncation, and resolution semantics (all passed).

### Step 5: Add validated settings from flags and environment
- **What**: Created `src/health_monitor/config.py` and unit test suite `tests/test_config.py`.
- **Why**: Provide immutable, strongly validated configuration loaded across CLI flags and environment variables with strict precedence and actionable diagnostics.
- **Decisions**: Implemented precedence: CLI flags > environment variables > defaults; raised custom `ConfigError` rather than calling `sys.exit()` to enable comprehensive unit testing; enforced that the state file path must never reside inside the monitored root directory to prevent self-monitoring loops.
- **Verify**: Executed `python -m pytest` running 84 tests across `test_config.py` and `test_parser.py` (all passed).

### Step 6: Discover servers and count faults by severity threshold
- **What**: Created simple initial version of `src/health_monitor/monitor.py`, shared pytest fixtures in `tests/conftest.py`, and basic tests in `tests/test_monitor.py`.
- **Why**: Deliver a functional core monitor that discovers log files in nested environments, reads file lines, applies configured severity thresholds, and counts active server faults.
- **Decisions**: Recursively walked root path excluding hidden files/directories (e.g. `.git/`); derived stable fault IDs (`auto-<offset>`) when explicit `[id=...]` is omitted; counted servers at most once regardless of multiple active faults.
- **Verify**: Executed `python -m pytest` running 93 tests across `test_monitor.py`, `test_config.py`, and `test_parser.py` (all passed).

### Step 7: Track fault lifecycle and explicit resolution
- **What**: Updated `src/health_monitor/monitor.py` with fault lifecycle management and created `tests/test_resolution.py`.
- **Why**: Implement stateful fault tracking where faults remain open until explicitly resolved by an authorized `INFO RESOLVED id=<ID>` entry, preventing duplicate faults and premature closures.
- **Decisions**: Explicit resolution lines clear only their matching `fault_id` (leaving other active faults open); ordinary `INFO`/`WARN` lines never clear faults; recorded `seen_fault_ids` per file to prevent duplicate fault creation and forbid reopening already-resolved IDs.
- **Verify**: Executed `python -m pytest` running 105 tests including 12 dedicated lifecycle and resolution tests (all passed).

### Step 8: Read only newly appended bytes incrementally
- **What**: Added incremental stream reading to `src/health_monitor/monitor.py` with `_iter_complete_lines` and `_read_new_lines`, and added incremental test scenarios to `tests/test_monitor.py`.
- **Why**: Prevent re-scanning whole log files on every polling cycle, reducing per-scan I/O from $O(\text{total log size})$ to $O(\text{new bytes appended})$.
- **Decisions**: Maintained `cp.offset` and `cp.size_seen` per file checkpoint; skipped reads when file size matches `size_seen`; held back partial trailing lines without trailing newline until completed on later scans; bounded memory consumption during oversized line draining (up to `MAX_LINE_BYTES`).
- **Verify**: Executed `python -m pytest` running 111 tests including spy-verified no-op scans for unchanged files, partial line handling, and oversized line skips (all passed).
