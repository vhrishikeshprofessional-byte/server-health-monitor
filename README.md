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
