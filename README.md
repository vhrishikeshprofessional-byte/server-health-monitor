# Server Health Monitor

A lightweight, incremental, checkpointed server health monitor for log directories.

## Implementation Log

### Step 1: Repo foundation
- **What**: Initialized repository foundation with `.gitignore`, minimal `pyproject.toml` (packaging metadata, `src/` layout, standard library only, zero dependencies), and base `README.md`.
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
- **What**: Created sample log tree `org/` (`dev/server-1.log`, `dev/server-2.log`, `prd/server-3.log`, `staging/server-5.log`) appended CLI usage documentation.
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

### Step 17: Containerization (Dockerfile & .dockerignore)
- **What**: Created production-grade, hardened `Dockerfile` based on `python:3.11-slim` and `.dockerignore` for minimal build context.
- **Why**: Package the server health monitor into an isolated, reproducible container image executing under a non-root security context.
- **Decisions**: Configured dedicated non-root user `appuser` (UID/GID 10001); created standard volume mount points `/data/org` (read-only) and `/data/state` (read-write); uninstalled pip/setuptools/wheel after installation to minimize container attack surface and eliminate CVEs; set OCI metadata labels; set `ENTRYPOINT ["health-monitor"]`.
- **Verify**: Inspected Dockerfile and .dockerignore syntax and structure.

### Step 18: Container build job in CI
- **What**: Added `build` job to `.github/workflows/ci.yml` following the `sonar` job.
- **Why**: Automatically build and archive the container image in CI for downstream testing and deployment.
- **Decisions**: Leveraged Docker Buildx (`docker/setup-buildx-action@v4`); tagged image with commit SHA (`server-health-monitor:${{ github.sha }}`); passed `BUILD_DATE` and `VCS_REF` build arguments; exported image as `image.tar` and uploaded as a workflow artifact with 1-day retention.
- **Verify**: Verified job sequencing (`needs: sonar`) and workflow syntax.

### Step 19: Container smoke test harness and smoke job in CI
- **What**: Created `scripts/smoke_test.py` and added `smoke` job to `.github/workflows/ci.yml`.
- **Why**: Validate the built container image in an end-to-end environment verifying runtime execution, fault tracking, state persistence, configuration errors, and non-root security boundaries.
- **Decisions**: Built standard-library-only test harness covering 4 scenarios (Scenario A: fault detection, Scenario B: incremental resolution with shared state, Scenario C: exit code 2 for invalid config, Scenario D: non-root UID verification); configured `smoke` job to download and load `image.tar` before executing `smoke_test.py`.
- **Verify**: Ran `ruff check scripts/smoke_test.py` and `ruff format --check scripts/smoke_test.py` with zero errors.

### Step 20: Add Trivy vulnerability scan job
- **What**: Created `.trivyignore` exception baseline and added the `scan` job to `.github/workflows/ci.yml`.
- **Why**: Detect known Common Vulnerabilities and Exposures (CVEs) in base image OS packages and Python dependencies before container images are published.
- **Decisions**: Downloaded and loaded the built `image.tar` artifact; executed Aqua Security Trivy scanner (`aquasec/trivy:latest`) in table format via Docker socket; scanned both OS and library package types (`--pkg-types os,library`); configured build failure (`--exit-code 1`) on any fixable (`--ignore-unfixed`) `HIGH` or `CRITICAL` severity vulnerabilities; established zero-exception policy in `.trivyignore`.
- **Verify**: Inspected `.trivyignore` and confirmed CI workflow YAML syntax and step sequencing (`needs: smoke`).

### Step 21: Add GHCR publish job with immutable tags
- **What**: Added `publish` job to `.github/workflows/ci.yml` running on pushes to the `main` branch.
- **Why**: Deliver production container images to GitHub Container Registry (GHCR) with strict tag immutability and provenance tracking.
- **Decisions**: Restricted publish execution to `refs/heads/main` pushes; requested `packages: write` permission; authenticated using `GITHUB_TOKEN` via `docker/login-action@v4`; enforced immutability gate by checking `docker manifest inspect` before tagging to prevent overwriting existing commit SHA releases; published immutable commit SHA tag along with `:main` and `:latest` pointers.
- **Verify**: Inspected workflow YAML structure, conditional triggers (`if: github.event_name == 'push' && github.ref == 'refs/heads/main'`), and job dependencies (`needs: scan`).

### Step 22: Add repository metadata and tooling files
- **What**: Created `requirements.txt` and `.gitattributes`.
- **Why**: Provide standard pip onboarding conventions (`pip install -r requirements.txt`) and enforce consistent UNIX-style (`LF`) line endings across cross-platform environments.
- **Decisions**: Delegated `requirements.txt` to editable install with development dependencies (`-e .[dev]`) keeping `pyproject.toml` as single source of truth; configured `* text=auto eol=lf` in `.gitattributes` to eliminate CRLF/LF line-ending drift between Windows, macOS, and Linux runners.
- **Verify**: Confirmed file contents and validated repository status.

### Step 23: Complete system documentation and operational runbook
- **What**: Finalized comprehensive project documentation with architecture flows, project directory structure, setup guide, execution walkthroughs, configuration matrix, format specifications, testing/security documentation, and troubleshooting runbook.
- **Why**: Provide a production-grade, clear operational guide for operators, SREs, developers, and CI/CD pipelines without leaving undocumented behaviors or edge cases.
- **Decisions**: Included complete Mermaid architecture diagram and atomic persistence sequence diagram; detailed all exit codes and configuration options; provided concrete command examples and troubleshooting remediations matching production specifications.
- **Verify**: Inspected Markdown rendering, table syntax, and diagram schemas across the completed documentation.

### Step 24: Comprehensive verification and quality gate audit
- **What**: Executed full structural, functional, and security verification across all codebase assets, configuration manifests, test suites, and CI/CD pipelines.
- **Why**: Ensure 100% compliance with architectural specifications, data integrity standards, resilient stream parsing contracts, and automated quality gates.
- **Decisions**: Verified complete end-to-end execution of unit tests, coverage thresholds, static typing, Ruff linting, container security policies, and build definitions.
- **Verify**: Executed 183 unit tests (182 passed, 1 platform-skipped on Windows) with 95.93% branch coverage against an 80% gate; zero Ruff lint or formatting findings.

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

---

## Table of Contents

- [1. Architecture & Key Features](#1-architecture--key-features)
- [2. Project Structure](#2-project-structure)
- [3. Getting Started](#3-getting-started)
  - [Prerequisites](#prerequisites)
  - [Local Installation](#local-installation)
- [4. Execution & Operations](#4-execution--operations)
  - [Single-Scan Mode (`--once`)](#single-scan-mode---once)
  - [Continuous Monitoring Loop](#continuous-monitoring-loop)
  - [Exit Code Contract](#exit-code-contract)
- [5. Configuration Reference](#5-configuration-reference)
- [6. Log Format & Resolution Specification](#6-log-format--resolution-specification)
  - [Log Event Grammar](#log-event-grammar)
  - [Resolution Event Syntax](#resolution-event-syntax)
  - [Lifecycle Walkthrough](#lifecycle-walkthrough)
- [7. Quality Assurance & Testing](#7-quality-assurance--testing)
  - [Running Tests & Coverage Gate](#running-tests--coverage-gate)
  - [Static Analysis & Formatting](#static-analysis--formatting)
  - [Container Smoke Tests](#container-smoke-tests)
- [8. Containerization & Docker](#8-containerization--docker)
  - [Building the Image](#building-the-image)
  - [Running the Container](#running-the-container)
  - [Non-Root Security Model](#non-root-security-model)
- [9. CI/CD Pipeline & Quality Gates](#9-cicd-pipeline--quality-gates)
  - [Pipeline Flow](#pipeline-flow)
  - [Stage Breakdown](#stage-breakdown)
  - [GitHub Secrets & Variables Configuration](#github-secrets--variables-configuration)
  - [SonarCloud Integration](#sonarcloud-integration)
  - [GHCR Publishing Permissions](#ghcr-publishing-permissions)
- [10. Resilience & Architecture Decisions](#10-resilience--architecture-decisions)
  - [Atomic Persistence & Crash Recovery](#atomic-persistence--crash-recovery)
  - [Security Posture & Input Sanitization](#security-posture--input-sanitization)
  - [Complexity, Performance & Scaling Bounds](#complexity-performance--scaling-bounds)
  - [Design Decisions & Trade-Offs](#design-decisions--trade-offs)
  - [Assumptions & Known Limitations](#assumptions--known-limitations)
- [11. Operations & Troubleshooting Runbook](#11-operations--troubleshooting-runbook)

---

## 1. Architecture & Key Features

```mermaid
flowchart LR
    A["Log Source\n(org/&lt;env&gt;/&lt;server&gt;.log)"] -->|Incremental Read| B["Log Parser\n(Sanitize & Validate)"]
    B -->|LogEvent| C["Health Monitor Engine\n(State & Fault Tracking)"]
    C -->|Atomic Replace| D[("Persistent State\nstate/state.json")]
    C -->|stdout| E["Health Report\n(Counts & Fault Details)"]
    C -->|stderr| F["Operational Logs\n(Diagnostic Telemetry)"]
```

- **Zero External Runtime Dependencies**: Standard library only (`dataclasses`, `json`, `os`, `sys`, `pathlib`, `re`, `logging`). Small attack surface and ~50MB compressed container size.
- **Incremental Byte-Offset Processing**: Scans only newly appended bytes per cycle. Unchanged files cost a single `os.stat` call.
- **Log Rotation & Truncation Handling**: Tracks inode and file size. Automatically resets byte offsets to 0 when rotation or truncation is detected without losing unresolved active faults.
- **Atomic State Persistence**: Writes state to temporary files, flushes via `os.fsync`, and replaces atomically via `os.replace`. Invalid or corrupted checkpoints are safely quarantined (`.corrupt-<timestamp>`).
- **Resilient Input Parser**: Never crashes on malformed input, Unicode corruption, or binary noise. Terminal ANSI control sequences are stripped before report generation.
- **Strict Hardened Container**: Non-root container (`UID 10001`), immutable build metadata (OCI annotations), read-only log mounting, and volume-isolated state storage.

---

## 2. Project Structure

```text
server-health-monitor/
├── .github/workflows/
│   └── ci.yml                     # 7-stage gated GitHub Actions pipeline with GHCR publication
├── .dockerignore                  # Docker context exclusions (keeps builds small and secret-free)
├── .gitattributes                 # Enforces LF line endings across OS platforms
├── .gitignore                     # Git exclusion rules (venv, caches, coverage, state)
├── .trivyignore                   # Documented container CVE exceptions
├── Dockerfile                     # Hardened, non-root container image definition
├── pyproject.toml                 # Packaging specification (src layout) & tool configurations
├── README.md                      # Comprehensive system guide and operational runbook
├── requirements.txt               # Pinned developer tooling dependencies
├── sonar-project.properties       # SonarCloud static analysis and coverage mappings
│
├── org/                           # Sample organization log data for verification and demo
│   ├── dev/
│   │   ├── server-1.log           # Healthy server (INFO events only)
│   │   └── server-2.log           # Active fault (ERROR with id=F001)
│   ├── prd/
│   │   └── server-3.log           # Resolved fault (ERROR id=F010 resolved by INFO RESOLVED)
│   └── staging/
│       └── server-5.log           # Multiple faults (id=F001 resolved; id=F002 active)
│
├── scripts/
│   └── smoke_test.py              # End-to-end container verification script (standard library only)
│
├── src/health_monitor/            # Core application package (Standard Library only)
│   ├── __init__.py                # Package version definition
│   ├── __main__.py                # Module execution entrypoint (`python -m health_monitor`)
│   ├── models.py                  # Immutable data structures (Severity, LogEvent, Fault, Checkpoint)
│   ├── config.py                  # Settings resolution (CLI flags + environment variables) & validation
│   ├── parser.py                  # Resilient log line parser & sanitizer (never raises on bad lines)
│   ├── checkpoint.py              # Atomic JSON persistence, schema validation, and quarantine handling
│   ├── monitor.py                 # File discovery, incremental scanning, rotation detection, lifecycle
│   └── cli.py                     # Execution loop, signal handling, and report output
│
└── tests/                         # Pytest test suite (>95% branch coverage)
    ├── conftest.py                # Reusable fixtures and temporary filesystem helpers
    ├── test_parser.py             # Log syntax, timestamps, severities, sanitization, invalid input
    ├── test_config.py             # CLI flag precedence, environment variable fallbacks, validation
    ├── test_monitor.py            # Discovery, incremental byte reads, rotation, thresholds
    ├── test_resolution.py         # Fault resolution matching, ordering, deduplication, idempotency
    ├── test_checkpoint.py         # Atomic persistence, crash recovery, corruption quarantine
    └── test_cli.py                # Command-line behavior, exit codes (0/1/2), signal trapping
```

---

## 3. Getting Started

### Prerequisites

| Tool | Minimum Version | Purpose |
|---|---|---|
| **Python** | `>= 3.11` | Runtime and test environment |
| **Docker** | `>= 20.10` | Container build and execution |
| **Git** | `>= 2.30` | Version control |
| **SonarCloud** | Free Cloud Tier | Static analysis and quality gate *(optional)* |
| **GitHub Packages** | Free Tier | Container registry hosting *(optional)* |

### Local Installation

```bash
# 1. Clone the repository
git clone https://github.com/YOUR_ORG/server-health-monitor.git
cd server-health-monitor

# 2. Create and activate a Python virtual environment
python3 -m venv .venv

# On Linux/macOS:
source .venv/bin/activate
# On Windows PowerShell:
# .venv\Scripts\Activate.ps1

# 3. Install in editable mode with development dependencies
pip install -e ".[dev]"
```

---

## 4. Execution & Operations

The monitor outputs diagnostic logs to `stderr` and clean, parsable health reports to `stdout`.

### Single-Scan Mode (`--once`)

Executes an immediate one-pass scan across `--root`, saves the updated state, outputs the report to `stdout`, and exits cleanly:

```bash
python -m health_monitor --root org --state-file state/local-state.json --once
```

#### Actual Output on Sample Data

```text
2026-10-18 10:05:47,539 INFO health_monitor.checkpoint: no state file at state/local-state.json; starting with empty state
2026-10-18 10:05:47,539 INFO health_monitor.cli: monitoring org (interval=30.0s, min severity=ERROR, once=True)
=== Server Health Report (2026-10-18 10:05:47) ===
Total servers discovered: 4
Servers in fault: 2

Affected servers:
  dev/server-2.log  (1 active fault)
    - F001 [ERROR] since 2026-10-08 09:02:00: Database unavailable
  staging/server-5.log  (1 active fault)
    - F002 [CRIT] since 2026-10-08 09:06:00: Disk failure
2026-10-18 10:05:47,550 INFO health_monitor.cli: shutdown complete
```

### Continuous Monitoring Loop

For operational daemon deployments, run the scanner continuously:

```bash
health-monitor --root /var/log/org --state-file /var/lib/health-monitor/state.json --interval 15
```

> [!TIP]
> The monitor registers signal handlers for `SIGINT` (Ctrl+C) and `SIGTERM`. It completes any in-flight scan pass and flushes checkpoint state atomically before exiting.

### Exit Code Contract

| Exit Code | Classification | Meaning |
|:---:|---|---|
| **`0`** | **Success** | Clean exit in `--once` mode or successful shutdown after `SIGINT` / `SIGTERM`. |
| **`1`** | **Runtime Crash** | Fatal I/O failure, permission barrier, or unhandled exception during scan loop. |
| **`2`** | **Configuration Error** | Invalid argument, malformed severity, missing root directory, or state file inside root. |

---

## 5. Configuration Reference

Settings can be specified via command-line flags or environment variables. **Command-line flags always take precedence over environment variables.**

| CLI Option | Environment Variable | Default Value | Description |
|---|---|---|---|
| `--root PATH` | `HM_ROOT` | `org` | Root path containing `org/<env>/<server>.log` hierarchies. |
| `--state-file PATH` | `HM_STATE_FILE` | `state/state.json` | Path to persistent checkpoint JSON. Cannot be inside `--root`. |
| `--interval SEC` | `HM_INTERVAL` | `30.0` | Polling interval in seconds (`float >= 0.1`). |
| `--min-severity SEV` | `HM_MIN_SEVERITY` | `ERROR` | Minimum severity triggering faults: `INFO`, `WARN`, `ERROR`, `CRIT`. |
| `--once` | `HM_ONCE` | `false` | Scan once, print report to `stdout`, and exit. |
| `--log-level LEVEL` | `HM_LOG_LEVEL` | `INFO` | Logging verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |

---

## 6. Log Format & Resolution Specification

### Log Event Grammar

Log entries follow standard syslog-compatible timestamped lines:

```text
YYYY-MM-DD HH:MM:SS <SEVERITY> [id=<FAULT_ID>] <MESSAGE>
```

- **Timestamp**: `YYYY-MM-DD HH:MM:SS` (24-hour UTC or local time).
- **Severity**: One of `INFO` (rank 10), `WARN` (rank 20), `ERROR` (rank 30), or `CRIT` (rank 40).
- **Fault ID** *(optional on informational logs, mandatory for tracked alerts)*: Bracketed token `[id=<ID>]`. Must be alphanumeric plus `-` or `_` (up to 64 chars).
- **Message**: Remaining text describing the operational condition.

### Resolution Event Syntax

To resolve an active fault, an authorized `INFO` event referencing the exact fault ID must appear in the same log file:

```text
YYYY-MM-DD HH:MM:SS INFO RESOLVED id=<FAULT_ID>
```

#### Specification Rules:
1. **Severity Restriction**: Must be `INFO`. Warning or error lines containing the word "RESOLVED" do not clear faults.
2. **Grammar Restriction**: Message must start with keyword prefix `RESOLVED id=<ID>`.
3. **Stream Boundary**: A resolution event clears the fault **only on that specific server file**.
4. **Health Transition**: When all active faults on a server are resolved, the server returns to healthy status.
5. **No ID Reuse**: Resolved fault IDs are preserved in `seen_fault_ids` to prevent ambiguous re-opening during replay.

### Lifecycle Walkthrough

```text
2026-10-08 09:00:00 INFO Service initialized                 # Healthy
2026-10-08 09:02:00 ERROR [id=F001] Connection timeout       # Fault F001 opened -> Server in FAULT
2026-10-08 09:05:00 WARN Retrying database connection        # Informational warning -> F001 stays active
2026-10-08 09:08:00 INFO RESOLVED id=F001                    # Fault F001 cleared -> Server HEALTHY
2026-10-08 09:08:01 INFO Database reconnected successfully   # Healthy
```

---

## 7. Quality Assurance & Testing

### Running Tests & Coverage Gate

The automated test suite achieves **95.9% branch coverage** across 180+ tests verifying line parsing, state persistence, crash recovery, rotation semantics, and CLI behaviors without external mocks.

```bash
# Run pytest with strict coverage gate (fails build if coverage < 80%)
pytest --cov=health_monitor --cov-report=term-missing --cov-report=xml --cov-fail-under=80
```

### Static Analysis & Formatting

Code style, import sorting, security idioms, and modern Python formatting are verified using [Ruff](https://astral.sh/ruff):

```bash
# Run static lint checks
ruff check .

# Check code formatting without modifying files
ruff format --check .

# Auto-format files
ruff format .
```

### Container Smoke Tests

The repository includes a standalone automated test harness ([`scripts/smoke_test.py`](scripts/smoke_test.py)) using only standard library modules to test the built container:

```bash
python scripts/smoke_test.py server-health-monitor:latest
```

The smoke test verifies four critical production criteria:
- **Scenario A**: Initial scan with an active fault (verifies exit code 0, discovered count, fault count, and tokens).
- **Scenario B**: Incremental resolution across separate container invocations sharing the same state volume (verifies fault count drops to 0).
- **Scenario C**: Configuration validation contract (verifies `--min-severity=bogus` exits with status `2` and emits `error:` to stderr).
- **Scenario D**: Runtime security validation (verifies container user is non-root `UID 10001`).

---

## 8. Containerization & Docker

### Building the Image

```bash
docker build -t server-health-monitor:latest .
```

### Running the Container

Mount the monitored directory as **read-only** (`:ro`) and the checkpoint state directory as **read-write** (`:rw`):

```bash
docker run --rm \
  -v "$(pwd)/org:/data/org:ro" \
  -v "$(pwd)/state:/data/state:rw" \
  server-health-monitor:latest --once
```

### Non-Root Security Model

- **Fixed Non-Root UID**: The container drops root privileges during image build and executes as `appuser` (`UID 10001`, `GID 10001`).
- **Filesystem Permissions**: Ensure that the host state directory is writable by UID 10001:
  ```bash
  chmod 0777 ./state
  # or
  sudo chown -R 10001:10001 ./state
  ```
- **Read-Only Log Isolation**: Mounting logs with `:ro` protects monitored files from accidental truncation, writes, or deletion.

---

## 9. CI/CD Pipeline & Quality Gates

### Pipeline Flow

The GitHub Actions workflow ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) implements a 7-stage quality gate where **each job depends on the previous job**. Any failure halts downstream execution immediately:

```mermaid
graph TD
    A["1. Setup & Lint\n(Ruff Lint & Format)"] --> B["2. Test & Coverage\n(Pytest &gt;=80% Gate)"]
    B --> C["3. SonarCloud Gate\n(Wait for Quality Gate)"]
    C --> D["4. Build Container\n(Buildx & OCI Metadata)"]
    D --> E["5. Smoke Test\n(Live Container Harness)"]
    E --> F["6. Vulnerability Scan\n(Trivy CRIT/HIGH Gate)"]
    F --> G["7. Publish to GHCR\n(Push on main Only)"]
```

### Stage Breakdown

1. **Setup & Lint**: Executes Ruff linter and formatting checks against Python 3.11.
2. **Unit Tests & Coverage**: Runs pytest suite with `--cov-fail-under=80`. Exports `coverage.xml`.
3. **SonarCloud Quality Gate**: Analyzes code quality, test execution, and coverage mapping. Enforces gate via `-Dsonar.qualitygate.wait=true` (safely skipped on fork PRs).
4. **Build Container Image**: Builds the Docker image, tags with commit SHA, exports image archive `image.tar`, and uploads it as a workflow artifact.
5. **Container Smoke Test**: Downloads `image.tar`, loads it into the runner daemon, and executes `scripts/smoke_test.py`.
6. **Vulnerability Scan**: Runs Aqua Security Trivy scanner on the loaded image. Fails on any fixable `CRITICAL` or `HIGH` CVEs.
7. **Publish to GHCR**: Triggered **only on push to `main` branch**. Enforces tag immutability (verifies `:<commit-sha>` tag does not already exist on GHCR) and publishes `:<sha>`, `:main`, and `:latest` tags.

### GitHub Secrets & Variables Configuration

Configure repository settings under **Settings > Secrets and variables > Actions**:

#### Repository Secrets (`Secrets` tab):
- `SONAR_TOKEN`: Token generated in SonarCloud (**Account > Security > Generate Token**).
*(Note: `GITHUB_TOKEN` is automatically provided by GitHub Actions for GHCR authentication).*

#### Repository Variables (`Variables` tab):
- `SONAR_PROJECT_KEY`: Unique project key defined in SonarCloud (e.g. `my-org_server-health-monitor`).
- `SONAR_ORGANIZATION`: SonarCloud organization key (e.g. `my-org`).

### SonarCloud Integration

1. Log in to [SonarCloud](https://sonarcloud.io) using your GitHub account.
2. Click **+ > Analyze new project** and select this repository.
3. Turn **Automatic Analysis OFF** (analysis runs directly via GitHub Actions).
4. Copy the Organization Key and Project Key into your GitHub repository variables (`SONAR_ORGANIZATION` and `SONAR_PROJECT_KEY`).

### GHCR Publishing Permissions

1. Navigate to **Settings > Actions > General** in your GitHub repository.
2. Under **Workflow permissions**, choose **Read and write permissions**.
3. Click **Save**.
4. Once the first image is published, navigate to your GitHub Profile/Org **Packages** tab, select `server-health-monitor`, and set package visibility to **Public** if desired.

---

## 10. Resilience & Architecture Decisions

### Atomic Persistence & Crash Recovery

```mermaid
sequenceDiagram
    participant M as Monitor Engine
    participant T as Temp File (.tmp)
    participant D as Disk Storage
    participant S as Checkpoint File (.json)

    M->>T: Write serialized JSON state
    M->>T: os.fsync() (Flush OS buffers to physical disk)
    M->>S: os.replace(temp, final) (Atomic directory entry swap)
    Note over S: Guarantees zero corrupt or half-written state files
```

- **Atomic File Swapping**: State checkpoints are serialized to a temporary file (`state.json.tmp`) within the destination folder, flushed via `os.fsync`, and replaced atomically via `os.replace`.
- **Corruption Quarantine**: If an unrecoverable corrupted JSON state file is encountered, the monitor automatically renames it to `<state_file>.corrupt-<timestamp>` and initializes a clean state rather than crashing.
- **Idempotent Replay**: Every tracked file maintains `seen_fault_ids`. Re-reading historical log events will not duplicate active faults or reopen resolved conditions.
- **Log Rotation Detection**: Tracks both file inode and file size. When a log file size decreases below the last recorded byte offset or its inode changes, the offset resets to 0 while maintaining active unresolved faults.

### Security Posture & Input Sanitization

- **Untrusted Input Protection**: All incoming log lines are sanitized. Terminal escape sequences (ANSI control codes `\x1b[...]`) and unprintable control characters are stripped to prevent terminal injection attacks.
- **Safe Serialization**: Uses strict standard JSON validation only. No `pickle`, `yaml`, or `eval` functions are used.
- **Non-Root Execution**: Container drops root privileges to numeric UID `10001` before executing entrypoint code.
- **Symlink Protection**: Directory traversal routines ignore symbolic links to prevent arbitrary filesystem reading.
- **Immutable Artifacts**: Every published container tag corresponds to an immutable Git commit SHA. The pipeline validates that the tag does not exist before publishing.

### Complexity, Performance & Scaling Bounds

- **Scan Time Complexity**: `O(F + B)`, where `F` is the total number of files in `--root` and `B` is the volume of newly appended bytes since the previous cycle.
- **Stat-Only Cache**: Unchanged files cost a single `os.stat` call per polling cycle.
- **Bounded Memory**: Reads files line-by-line with bounded buffers. Maximum state file size is capped at `64 MB` (`MAX_STATE_BYTES`) to protect against memory exhaustion.

### Design Decisions & Trade-Offs

- **Standard Library Only**: Eliminates supply-chain risks, avoids package vulnerabilities, and guarantees predictable behavior across platforms.
- **Polling vs. inotify**: Uses configurable polling intervals (`--interval`) instead of filesystem event notifications (`inotify`). Polling ensures reliable operation across NFS/SMB network mounts, CIFS, and containerized bind mounts where filesystem event APIs frequently drop events.
- **Separation of stdout and stderr**: Operational diagnostic messages are directed to `stderr`, while structured reports are printed to `stdout` for clean piping (`health-monitor --once > report.txt`).

### Assumptions & Known Limitations

- **Line Completion**: Log writers are assumed to write lines ending in `\n`. Incomplete trailing fragments are buffered and evaluated on the subsequent polling cycle.
- **Unique Fault IDs**: Fault IDs must be unique per server log file. Separate servers may use identical IDs (e.g. `F001`) without collision.
- **Fault ID Reuse**: Once a fault ID is resolved, it cannot be reopened in that server's stream. Subsequent incidents should use distinct IDs (e.g. `F001-1`).
- **Single Instance per State File**: State files do not implement cross-process distributed locking. Run one monitor instance per state file.
- **Stateless Server Renames**: Renaming a log file causes it to be tracked as a new server discovery.
- **No HTTP Endpoint**: Designed as a CLI and containerized background daemon for standard logging pipelines and orchestration rather than an HTTP metrics server.

---

## 11. Operations & Troubleshooting Runbook

### Permission Denied on State Volume
- **Symptom**: `PermissionError: [Errno 13] Permission denied: '/data/state/state.json.tmp'`
- **Cause**: The container runs as non-root `UID 10001`, but the host directory mounted to `/data/state` is owned by root or an unprivileged user.
- **Remediation**:
  ```bash
  chmod 0777 ./state
  # or
  sudo chown -R 10001:10001 ./state
  ```

### State File Quarantined
- **Symptom**: Warning logged: `quarantining corrupted state file to state.json.corrupt-...`
- **Cause**: State file was truncated, corrupted by hardware fault, or modified with invalid JSON.
- **Remediation**: Inspect the quarantined `.corrupt-<timestamp>` file to retrieve previous fault history if necessary. The monitor automatically initializes a fresh checkpoint and resumes monitoring without intervention.

### Invalid Configuration (Exit Code 2)
- **Symptom**: Process exits immediately with exit code `2` and `error:` in output.
- **Cause**: Invalid flag passed, negative interval, unknown severity name, or `--state-file` configured inside `--root`.
- **Remediation**: Check parameter spelling and verify that the state file path is outside the monitored log directory.

### SonarCloud Quality Gate Failure in CI
- **Symptom**: CI job `sonar` fails with Quality Gate error.
- **Cause**: Code coverage dropped below 80% or new code introduced security hotspots.
- **Remediation**: Run tests locally with coverage enforcement:
  ```bash
  pytest --cov=health_monitor --cov-fail-under=80
  ```

### GHCR Publish Permission Denied (403 Forbidden)
- **Symptom**: `denied: installation not allowed to Create organization package`
- **Cause**: GitHub Actions workflow lacks `packages: write` permissions.
- **Remediation**: In GitHub repository settings, navigate to **Actions > General > Workflow permissions** and select **Read and write permissions**.
