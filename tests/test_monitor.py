# test_monitor.py
#
# COVERS monitor.py (basic discovery, counting, thresholds)
#   - empty org -> 0 servers, 0 faulty
#   - multiple environments and nested directories discovered recursively
#   - hidden files and directories are ignored
#   - configurable threshold: ERROR threshold ignores WARN, CRIT threshold ignores ERROR
#   - multiple active faults on one server -> server counted once
#   - alert without id gets a stable derived id

"""Basic discovery, counting, and thresholds."""

import pytest

from health_monitor.models import Severity

INFO_LINE = "2026-10-08 09:00:00 INFO Service started"
ERR1 = "2026-10-08 09:02:00 ERROR [id=F001] Database unavailable"
CRIT2 = "2026-10-08 09:06:00 CRIT [id=F002] Disk failure"


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
