# test_monitor.py
#
# COVERS monitor.py (discovery, counting, incremental reads)
#   - empty org -> 0 servers, 0 faulty
#   - multiple environments and nested directories discovered recursively
#   - configurable threshold: ERROR threshold ignores WARN, CRIT threshold ignores ERROR
#   - multiple active faults on one server -> server counted once
#   - newly appended entries picked up on the next scan
#   - unchanged file is not re-read (observable via counters or a spy)
#   - partial trailing line is held back until its newline arrives
#   - malformed lines do not stop valid ones
#   - oversized line handling

"""Discovery, counting, thresholds and incremental reading."""

import logging

import pytest

from health_monitor.models import Severity
from health_monitor.monitor import MAX_LINE_BYTES, Monitor

INFO_LINE = "2026-10-08 09:00:00 INFO Service started"
ERR1 = "2026-10-08 09:02:00 ERROR [id=F001] Database unavailable"
CRIT2 = "2026-10-08 09:06:00 CRIT [id=F002] Disk failure"
ERR9 = "2026-10-08 09:20:00 ERROR [id=F9] Later failure"


def fault_ids(report, rel):
    status = next(s for s in report.statuses if s.relative_path == rel)
    return {fault.fault_id for fault in status.active_faults}


# ---------------------------------------------------------------- discovery
def test_empty_org_has_no_servers(make_monitor):
    report = make_monitor().scan_once()
    assert report.total_servers == 0
    assert report.faulty_servers == 0
    assert report.statuses == ()
    assert report.discovery_errors == ()


def test_discovers_environments_and_nested_directories(make_monitor, write_log):
    write_log("staging/team-a/server-5.log", INFO_LINE)
    write_log("prd/server-3.log", INFO_LINE)
    write_log("dev/server-1.log", INFO_LINE)
    report = make_monitor().scan_once()
    assert report.total_servers == 3
    assert [s.relative_path for s in report.statuses] == [
        "dev/server-1.log",
        "prd/server-3.log",
        "staging/team-a/server-5.log",
    ]


def test_hidden_files_and_directories_are_ignored(make_monitor, write_log):
    write_log("dev/server-1.log", INFO_LINE)
    write_log("dev/.hidden", ERR1)
    write_log(".git/objects.log", ERR1)
    report = make_monitor().scan_once()
    assert report.total_servers == 1
    assert report.faulty_servers == 0


# --------------------------------------------------------------- thresholds
ALL_SEVERITIES = [
    "2026-10-08 09:00:00 INFO [id=I1] info",
    "2026-10-08 09:01:00 WARN [id=W1] warn",
    "2026-10-08 09:02:00 ERROR [id=E1] error",
    "2026-10-08 09:03:00 CRIT [id=C1] crit",
]


@pytest.mark.parametrize(
    ("threshold", "expected"),
    [
        (Severity.INFO, {"I1", "W1", "E1", "C1"}),
        (Severity.WARN, {"W1", "E1", "C1"}),
        (Severity.ERROR, {"E1", "C1"}),
        (Severity.CRIT, {"C1"}),
    ],
)
def test_threshold_selects_which_severities_open_faults(
    make_monitor, write_log, threshold, expected
):
    write_log("dev/s.log", *ALL_SEVERITIES)
    report = make_monitor(min_severity=threshold).scan_once()
    assert fault_ids(report, "dev/s.log") == expected


def test_server_with_many_faults_is_counted_once(make_monitor, write_log):
    write_log("dev/s.log", ERR1, CRIT2)
    report = make_monitor().scan_once()
    assert report.total_servers == 1
    assert report.faulty_servers == 1
    assert fault_ids(report, "dev/s.log") == {"F001", "F002"}


def test_alert_without_id_gets_a_stable_derived_id(make_monitor, write_log):
    write_log("dev/s.log", "2026-10-08 09:02:00 ERROR Database unavailable")
    monitor = make_monitor()
    first = fault_ids(monitor.scan_once(), "dev/s.log")
    second = fault_ids(monitor.scan_once(), "dev/s.log")
    fresh = fault_ids(make_monitor().scan_once(), "dev/s.log")
    assert len(first) == 1
    assert next(iter(first)).startswith("auto-")
    assert first == second == fresh


# -------------------------------------------------------------- incremental
def test_appended_entries_are_picked_up(make_monitor, write_log):
    write_log("dev/s.log", INFO_LINE)
    monitor = make_monitor()
    assert monitor.scan_once().faulty_servers == 0
    write_log("dev/s.log", ERR1, append=True)
    assert monitor.scan_once().faulty_servers == 1


def test_unchanged_file_is_not_reread(make_monitor, write_log, monkeypatch):
    path = write_log("dev/s.log", ERR1)
    monitor = make_monitor()
    monitor.scan_once()

    calls = []
    original = Monitor._read_new_lines

    def spy(self, cp, file_path):
        calls.append(cp.relative_path)
        return original(self, cp, file_path)

    monkeypatch.setattr(Monitor, "_read_new_lines", spy)
    monitor.scan_once()
    monitor.scan_once()
    assert calls == []

    write_log("dev/s.log", INFO_LINE, append=True)
    monitor.scan_once()
    assert calls == ["dev/s.log"]
    assert monitor.state["dev/s.log"].offset == path.stat().st_size


def test_partial_trailing_line_waits_for_its_newline(make_monitor, write_raw):
    write_raw("dev/s.log", "2026-10-08 09:02:00 ERROR [id=F1] Dis")
    monitor = make_monitor()
    report = monitor.scan_once()
    assert report.faulty_servers == 0
    assert monitor.state["dev/s.log"].offset == 0

    write_raw("dev/s.log", "k full\n", append=True)
    report = monitor.scan_once()
    status = report.statuses[0]
    assert [f.fault_id for f in status.active_faults] == ["F1"]
    assert status.active_faults[0].message == "Disk full"


def test_malformed_lines_do_not_stop_valid_ones(make_monitor, write_log, caplog):
    write_log(
        "dev/s.log",
        "garbage line",
        "2026-13-99 25:00:00 ERROR [id=X] bad timestamp",
        ERR1,
    )
    with caplog.at_level(logging.WARNING, logger="health_monitor.monitor"):
        report = make_monitor().scan_once()
    assert fault_ids(report, "dev/s.log") == {"F001"}
    assert report.discovery_errors == ()
    assert "malformed line" in caplog.text


def test_oversized_line_is_skipped_and_later_lines_still_apply(make_monitor, write_raw, write_log):
    huge = "2026-10-08 09:00:00 INFO " + "x" * (MAX_LINE_BYTES * 2)
    path = write_raw("dev/s.log", huge + "\n")
    write_log("dev/s.log", ERR9, append=True)
    monitor = make_monitor()
    report = monitor.scan_once()
    assert fault_ids(report, "dev/s.log") == {"F9"}
    assert monitor.state["dev/s.log"].offset == path.stat().st_size


def test_incomplete_oversized_line_does_not_advance_offset(make_monitor, write_raw, write_log):
    huge = "2026-10-08 09:00:00 INFO " + "x" * (MAX_LINE_BYTES * 2)
    write_raw("dev/s.log", huge)  # no newline yet
    monitor = make_monitor()
    assert monitor.scan_once().faulty_servers == 0
    assert monitor.state["dev/s.log"].offset == 0

    write_raw("dev/s.log", "\n", append=True)
    write_log("dev/s.log", ERR9, append=True)
    assert fault_ids(monitor.scan_once(), "dev/s.log") == {"F9"}
