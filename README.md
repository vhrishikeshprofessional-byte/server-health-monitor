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

### Step 9: Handle rotation, truncation and unreadable files
- **What**: Enhanced `src/health_monitor/monitor.py` and `tests/test_monitor.py` to handle log rotation, file truncation/replacement, unreadable permissions, vanished directories, and symlink skipping.
- **Why**: Production log systems continuously rotate (logrotate) and truncate logs, and access permissions can change; the monitor must remain resilient without dropping active faults or crashing.
- **Decisions**: Compared `st_ino` and `st_size` against checkpoint state to detect replacements and truncations, triggering replay from offset 0 while preserving unresolved active faults; unreadable files are flagged (`readable=False`) and kept in error reports rather than falsely reported healthy; missing files retain active faults unless healthy and directories are verified accessible.
- **Verify**: Executed `python -m pytest` running 120 tests including permission denial, file rotation/replacement, file truncation, and missing file retention (119 passed, 1 platform-skipped for Windows symlinks).

### Step 10: Persist state atomically with crash recovery
- **What**: Created `src/health_monitor/checkpoint.py` defining `CheckpointStore` and created unit test suite `tests/test_checkpoint.py`.
- **Why**: Ensure monitoring state, file offsets, active faults, and seen fault IDs are durably and atomically persisted across service restarts and system crashes.
- **Decisions**: Atomically replaced state via temporary file in the same directory (`flush` + `os.fsync` + `os.replace`); strictly validated schema version 1 and relative POSIX paths to prevent path traversal; quarantined corrupted or invalid state files (`.corrupt-<timestamp>`) while starting fresh to ensure unblocked service availability; avoided disk writes when state is unchanged.
- **Verify**: Executed `python -m pytest` running 156 tests covering atomic roundtrip, schema validation failures, quarantine renames, crash recovery, and write errors (155 passed, 1 platform-skipped).

### Step 11: Add report output, scan loop and graceful shutdown
- **What**: Created `src/health_monitor/cli.py`, `src/health_monitor/__main__.py`, added `[project.scripts]` entry `health-monitor` to `pyproject.toml`, and created unit test suite `tests/test_cli.py`.
- **Why**: Provide the user-facing CLI interface, structured terminal reporting, single-scan mode (`--once`), continuous polling loop with sliced sleeping, signal handling for graceful shutdown, and standardized process exit codes (0 for success, 1 for runtime failure, 2 for configuration errors).
- **Decisions**: Sent structured reports strictly to stdout and operational log messages to stderr; sanitized untrusted terminal output against escape sequences and capped display lengths; implemented graceful double-signal handling (first `SIGINT`/`SIGTERM` saves state and stops, second aborts); used 0.5s sleep slices for responsive interrupt handling across platforms.
- **Verify**: Executed `python -m pytest` running 183 tests across all test suites (182 passed, 1 platform-skipped) and verified `$env:PYTHONPATH='src'; python -m health_monitor --help`.

### Step 12: Add sample logs and usage documentation
- **What**: Created sample log tree `org/` (`dev/server-1.log`, `dev/server-2.log`, `prd/server-3.log`, `staging/server-5.log`) matching the reference baseline and appended CLI usage documentation.
- **Why**: Provide realistic sample data for local verification, smoke testing, and onboarding documentation.
- **Decisions**: Sample files illustrate all state combinations: healthy servers (`dev/server-1.log`), single active fault (`dev/server-2.log`), resolved fault (`prd/server-3.log`), and multi-fault progression where one is resolved and another stays active (`staging/server-5.log`).
- **Verify**: Executed `python -m health_monitor --root org --state-file state/test-state.json --once` producing 4 discovered servers, 2 in fault (`F001` on `dev/server-2.log`, `F002` on `staging/server-5.log`), and ran `python -m pytest` (182 passed, 1 platform-skipped).

### Step 13: Configure ruff and fix findings
- **What**: Added `[tool.ruff]`, `[tool.ruff.lint]`, and `[tool.ruff.format]` configurations to `pyproject.toml` and verified all code passes linting and formatting checks.
- **Why**: Establish automated static analysis, import sorting, code hygiene, and style consistency across the codebase before introducing CI automation.
- **Decisions**: Selected comprehensive rules (`E`, `F`, `W`, `I`, `B`, `UP`, `S`, `C4`, `SIM`, `RUF`); ignored `E501` to let formatter manage line wrapping while tolerating long strings/comments; configured per-file ignores for tests (`S101` for asserts, `RUF001` for unicode test data) and scripts (`S603`, `S607` for subprocesses); enforced double-quote style.
- **Verify**: Executed `ruff check .` (All checks passed!) and `ruff format --check .` (16 files already formatted).

### Step 14: Add workflow skeleton with lint job
- **What**: Created `.github/workflows/ci.yml` defining the base CI/CD pipeline triggers (`push` and `pull_request` on `main`), concurrency settings, read-only permissions, and initial `lint` stage.
- **Why**: Automate pull request and merge validation so that code formatting and static analysis are enforced on every contribution.
- **Decisions**: Configured concurrency cancellation for non-main branches (`cancel-in-progress`); locked baseline workflow permissions to `contents: read`; cached pip dependencies keyed on `pyproject.toml` under Python 3.11; ran both `ruff check .` and `ruff format --check .`.
- **Verify**: Inspected workflow YAML structure and confirmed local lint and format checks execute with zero findings.

### Step 15: Add unit test job with coverage gate
- **What**: Added `[tool.coverage.run]` and `[tool.coverage.report]` sections to `pyproject.toml`, and configured the `test` job in `.github/workflows/ci.yml` with coverage gating and artifact uploading (`coverage.xml`).
- **Why**: Enforce an automated quality gate verifying unit tests pass and test branch coverage remains at or above 80% on all pull requests and merges.
- **Decisions**: Configured coverage collection across `health_monitor` package measuring branch coverage, omitting `__main__.py` entrypoint; enforced `--cov-fail-under=80`; configured XML report generation for downstream SonarCloud analysis and artifact upload with 1-day retention.
- **Verify**: Executed `pytest --cov=health_monitor --cov-report=xml --cov-report=term-missing --cov-fail-under=80` achieving 95.93% total coverage with 182 passing tests.

### Step 16: Add SonarCloud quality gate job
- **What**: Created `sonar-project.properties` and added the `sonar` job to `.github/workflows/ci.yml`.
- **Why**: Integrate automated static analysis, code smells detection, security hotspots scanning, and quality gate enforcement within the CI pipeline.
- **Decisions**: Passed SonarCloud project key and organization via workflow arguments; linked `coverage.xml` generated by the test stage; added an environment pre-check (`check-sonar`) that cleanly skips the scan with a GitHub Actions notice when secrets/variables are missing, preventing pipeline failure on forks or repositories without SonarCloud tokens.
- **Verify**: Inspected `sonar-project.properties` properties and verified workflow syntax with pre-check skip condition.

---

## Usage Guide & Sample Execution

### Single-Scan Mode (`--once`)

Executes an immediate one-pass scan across `--root`, saves updated state to `--state-file`, outputs the report to `stdout`, and exits cleanly:

```bash
python -m health_monitor --root org --state-file state/local-state.json --once
```

#### Actual Output on Sample Data

```text
=== Server Health Report ===
Total servers discovered: 4
Servers in fault: 2

Affected servers:
  dev/server-2.log  (1 active fault)
    - F001 [ERROR] since 2026-10-08 09:02:00: Database unavailable
  staging/server-5.log  (1 active fault)
    - F002 [CRIT] since 2026-10-08 09:06:00: Disk failure
```

### Continuous Monitoring Loop

For continuous monitoring, run without `--once`:

```bash
health-monitor --root org --state-file state/state.json --interval 15
```

The daemon responds to `SIGINT` (Ctrl+C) and `SIGTERM` by flushing the latest checkpoint and shutting down cleanly.

### Exit Code Contract

| Exit Code | Classification | Meaning |
|:---:|---|---|
| **`0`** | **Success** | Clean exit in `--once` mode or successful shutdown after `SIGINT` / `SIGTERM`. |
| **`1`** | **Runtime Crash** | Fatal I/O failure, permission barrier, or unhandled exception during scan loop. |
| **`2`** | **Configuration Error** | Invalid argument, malformed severity, missing root directory, or state file inside root. |
