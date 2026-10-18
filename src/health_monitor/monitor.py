# monitor.py
#
# RESPONSIBILITY:
#   Core engine: discover servers, read only NEW bytes, apply events to the
#   fault state, and produce counts. Returns data; does not print.

"""Core engine: discover servers, read only NEW bytes, apply fault rules.

Returns data (ScanReport); never prints, sleeps or parses CLI arguments.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from health_monitor.config import Settings
from health_monitor.models import (
    Fault,
    FileCheckpoint,
    LogEvent,
    ParseError,
    ScanReport,
    ServerStatus,
)
from health_monitor.parser import MAX_LINE_LENGTH, parse_line

logger = logging.getLogger(__name__)

MAX_LINE_BYTES = MAX_LINE_LENGTH * 4  # worst case UTF-8 bytes for a max-length line
MAX_DIAGNOSTICS_PER_FILE = 20


def _iter_complete_lines(fh: BinaryIO, start: int) -> Iterator[tuple[bytes | None, int, int]]:
    """Yield (line, start_offset, end_offset) for COMPLETE lines only.

    `line` is None when the line was longer than MAX_LINE_BYTES (it is drained
    without being stored). A trailing line without a newline is not yielded.
    """
    pos = start
    while True:
        chunk = fh.readline(MAX_LINE_BYTES + 1)
        if not chunk:
            return
        length = len(chunk)
        oversized = False
        if not chunk.endswith(b"\n"):
            if length <= MAX_LINE_BYTES:
                return  # partial trailing line: wait for its newline
            oversized = True
            while True:  # drain the rest of the oversized line, bounded memory
                rest = fh.readline(MAX_LINE_BYTES)
                if not rest:
                    return  # EOF before newline: line still incomplete
                length += len(rest)
                if rest.endswith(b"\n"):
                    break
        yield (None if oversized else chunk), pos, pos + length
        pos += length


class Monitor:
    """Tracks every server file incrementally and keeps active-fault state."""

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
        """Bring one file's state up to date. Returns an error text or None."""
        try:
            st = os.stat(path)
        except OSError as exc:
            return str(exc)

        cp = self._state.get(rel)
        if cp is None:
            cp = FileCheckpoint(relative_path=rel, inode=st.st_ino)
            self._state[rel] = cp

        if st.st_size == cp.size_seen:
            return None  # nothing new since the last read

        try:
            self._read_new_lines(cp, path)
        except OSError as exc:
            return str(exc)
        return None

    def _read_new_lines(self, cp: FileCheckpoint, path: str) -> None:
        problems = 0
        with open(path, "rb") as fh:
            fh.seek(cp.offset)
            for raw, start, stop in _iter_complete_lines(fh, cp.offset):
                if raw is None:
                    problems += 1
                    self._note_problem(cp, start, f"line exceeds {MAX_LINE_BYTES} bytes", problems)
                else:
                    event = parse_line(raw.decode("utf-8", errors="replace"))
                    if isinstance(event, ParseError):
                        problems += 1
                        self._note_problem(cp, start, event.reason, problems, event.preview)
                    elif event is not None:
                        self._apply(cp, event, start)
                cp.offset = stop  # advance only after the line's effects are applied
                cp.size_seen = max(cp.size_seen, stop)  # invariant: offset <= size_seen
            cp.size_seen = fh.tell()
        if problems > MAX_DIAGNOSTICS_PER_FILE:
            logger.warning(
                "%d further malformed lines in %r suppressed",
                problems - MAX_DIAGNOSTICS_PER_FILE,
                cp.relative_path,
            )

    @staticmethod
    def _note_problem(
        cp: FileCheckpoint,
        offset: int,
        reason: str,
        count: int,
        preview: str = "",
    ) -> None:
        if count <= MAX_DIAGNOSTICS_PER_FILE:
            logger.warning("malformed line in %r at byte %d: %s", cp.relative_path, offset, reason)
            if preview:
                logger.debug("malformed line preview: %r", preview)

    def _apply(self, cp: FileCheckpoint, event: LogEvent, line_start: int) -> None:
        if event.is_resolution:
            if cp.active_faults.pop(event.fault_id or "", None) is None:
                logger.info(
                    "resolution for unknown or inactive fault %r in %r ignored",
                    event.fault_id,
                    cp.relative_path,
                )
            return

        if not event.triggers_fault(self._settings.min_severity):
            return  # below threshold: ordinary entry, never clears anything

        fault_id = event.fault_id or f"auto-{line_start}"
        if fault_id in cp.seen_fault_ids:
            return  # duplicate, replay, or reuse of a resolved id: no new fault
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
