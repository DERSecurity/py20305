"""Tests for the Live Traffic recorder."""

from __future__ import annotations

from py20305.client.traffic_recorder import (
    LIVE_TRAFFIC_BODY_LIMIT,
    TrafficRecorder,
)


def test_records_response_with_full_body():
    r = TrafficRecorder()
    r.record_response(method="get", url="/api/v2/edev", status=200, body=b"<EndDeviceList/>")
    snap = r.get_snapshot()
    assert snap["total"] == 1
    assert snap["buffered"] == 1
    entry = snap["entries"][0]
    assert entry["direction"] == "response"
    assert entry["method"] == "GET"  # normalized upper
    assert entry["url"] == "/api/v2/edev"
    assert entry["status"] == 200
    assert "EndDeviceList" in entry["body"]
    assert entry["truncated"] is False


def test_request_and_notification_directions():
    r = TrafficRecorder()
    r.record_request(method="POST", url="/api/v2/edev/1/sub", body=b"<Subscription/>")
    r.record_notification(path="/notify", body=b"<Notification/>", source_ip="10.0.0.1")
    snap = r.get_snapshot()
    by_dir = {e["direction"]: e for e in snap["entries"]}
    assert set(by_dir) == {"request", "notification"}
    assert "10.0.0.1" in by_dir["notification"]["url"]


def test_body_truncated_at_cap():
    r = TrafficRecorder()
    big = "x" * (LIVE_TRAFFIC_BODY_LIMIT + 100)
    r.record_response(method="GET", url="/x", status=200, body=big)
    entry = r.get_snapshot()["entries"][0]
    assert entry["truncated"] is True
    assert len(entry["body"]) == LIVE_TRAFFIC_BODY_LIMIT


def test_ring_buffer_evicts_oldest_keeps_total():
    r = TrafficRecorder(max_entries=3)
    for i in range(5):
        r.record_response(method="GET", url=f"/r{i}", status=200, body=b"")
    snap = r.get_snapshot()
    assert snap["buffered"] == 3
    assert snap["total"] == 5  # total counts everything ever recorded


def test_snapshot_newest_first_and_limit():
    r = TrafficRecorder()
    for i in range(5):
        r.record_response(method="GET", url=f"/r{i}", status=200, body=b"")
    snap = r.get_snapshot(limit=2)
    assert [e["url"] for e in snap["entries"]] == ["/r4", "/r3"]
    assert snap["returned"] == 2


def test_records_error_response():
    r = TrafficRecorder()
    r.record_response(method="GET", url="/api/v2/edev/9/fsa", status=404, body="not found")
    r.record_response(method="GET", url="/api/v2/dcap", status=None, error="connection refused")
    entries = r.get_snapshot()["entries"]
    by_status = {e["status"]: e for e in entries}
    assert "not found" in by_status[404]["body"]
    assert by_status[None]["error"] == "connection refused"


def test_large_bytes_body_capped_without_full_decode():
    # A multi-MB byte payload must be capped (truncate before decode), not
    # decoded whole. We can't observe the intermediate allocation, but the
    # stored body must respect the cap and be flagged truncated.
    r = TrafficRecorder()
    big = b"y" * (LIVE_TRAFFIC_BODY_LIMIT + 5000)
    r.record_response(method="GET", url="/x", status=200, body=big)
    entry = r.get_snapshot()["entries"][0]
    assert entry["truncated"] is True
    assert len(entry["body"]) <= LIVE_TRAFFIC_BODY_LIMIT


def test_raw_body_whitespace_preserved():
    # The stored body must match the bytes on the wire (no stripping), so the
    # view doesn't disagree with what was actually sent/received.
    r = TrafficRecorder()
    r.record_response(method="GET", url="/x", status=200, body="  <a/>\n\n")
    assert r.get_snapshot()["entries"][0]["body"] == "  <a/>\n\n"


def test_empty_body_is_blank_not_truncated():
    r = TrafficRecorder()
    r.record_response(method="GET", url="/x", status=204, body=None)
    entry = r.get_snapshot()["entries"][0]
    assert entry["body"] == ""
    assert entry["truncated"] is False
