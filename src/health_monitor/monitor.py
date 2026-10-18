# monitor.py
#
# RESPONSIBILITY:
#   Core engine: discover servers, read lines, apply events to the
#   fault state, and produce counts. Returns data; does not print.

"""Core engine: discover servers, read lines, apply fault rules.

Returns data (ScanReport); never prints, sleeps or parses CLI arguments.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from health_monitor.config import Settings
from health_monitor.models import (
    Fault,
    FileCheckpoint,
    LogEvent,
    ParseError,
    ScanReport,
    ServerStatus,
)
from health_monitor.parser import parse_line

logger = logging.getLogger(__name__)


class Monitor:
    """Tracks every server file and keeps active-fault state."""

    def __init__(self, settings: Settings, state: dict[str, FileCheckpoint] | None = None) -> None:
        self._settings = settings
        self._state: dict[str, FileCheckpoint] = {} if state is None else state

    @property
    def state(self) -> dict[str, FileCheckpoint]:
        """Per-file state keyed by relative POSIX path."""
        return self._state

    def scan_once(self) -> ScanReport:
        files, errors = self._discover()
        statuses: list[ServerStatus] = []

        for rel, path in files:
            error = self._process_file(rel, path)
            if error is not None:
                errors.append(f"{rel}: {error}")
            statuses.append(self._status(rel, self._state.get(rel), error is None, error))

        statuses.sort(key=lambda s: s.relative_path)
        return ScanReport(
            total_servers=len(statuses),
            faulty_servers=sum(1 for s in statuses if s.is_faulty),
            statuses=tuple(statuses),
            discovery_errors=tuple(errors),
        )

    def _discover(self) -> tuple[list[tuple[str, str]], list[str]]:
        root = self._settings.root
        files: list[tuple[str, str]] = []
        errors: list[str] = []
        stack: list[Path] = [root]

        while stack:
            directory = stack.pop()
            try:
                with os.scandir(directory) as it:
                    entries = list(it)
            except OSError as exc:
                label = "<root>" if directory == root else directory.relative_to(root).as_posix()
                errors.append(f"{label}: cannot read directory ({exc})")
                continue

            for entry in entries:
                if entry.name.startswith("."):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        rel = Path(entry.path).relative_to(root).as_posix()
                        files.append((rel, entry.path))
                except OSError as exc:
                    errors.append(f"{entry.name}: cannot inspect ({exc})")
        return files, errors

    def _process_file(self, rel: str, path: str) -> str | None:
        cp = self._state.get(rel)
        if cp is None:
            try:
                st = os.stat(path)
                ino = st.st_ino
            except OSError:
                ino = 0
            cp = FileCheckpoint(relative_path=rel, inode=ino)
            self._state[rel] = cp

        cp.active_faults.clear()
        cp.seen_fault_ids.clear()
        try:
            with open(path, "rb") as fh:
                pos = 0
                for raw in fh:
                    line_start = pos
                    pos += len(raw)
                    event = parse_line(raw.decode("utf-8", errors="replace"))
                    if event is None or isinstance(event, ParseError):
                        continue
                    self._apply(cp, event, line_start)
        except OSError as exc:
            return str(exc)
        return None

    def _apply(self, cp: FileCheckpoint, event: LogEvent, line_start: int) -> None:
        if not event.triggers_fault(self._settings.min_severity):
            return
        fault_id = event.fault_id or f"auto-{line_start}"
        if fault_id in cp.seen_fault_ids:
            return
        cp.seen_fault_ids.add(fault_id)
        cp.active_faults[fault_id] = Fault(
            fault_id=fault_id,
            severity=event.severity,
            opened_at=event.timestamp,
            message=event.message,
        )

    @staticmethod
    def _status(
        rel: str, cp: FileCheckpoint | None, readable: bool, error: str | None
    ) -> ServerStatus:
        faults: tuple[Fault, ...] = ()
        if cp is not None and cp.active_faults:
            faults = tuple(
                sorted(cp.active_faults.values(), key=lambda f: (f.opened_at, f.fault_id))
            )
        return ServerStatus(relative_path=rel, active_faults=faults, readable=readable, error=error)
