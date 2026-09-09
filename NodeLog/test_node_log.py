"""
Standalone Unit Test Suite for NodeLog Telemetry Module.

Executes without external cloud connections, AutoGen agents, or filesystem leaks.
Supports execution via:
    pytest test_node_log.py
    python test_node_log.py
"""

import io
import json
import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List

import pytest

# Ensure local module is importable when executed directly
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from nodelog import (
    NodeLog,
    EventRecord,
    EventType,
    StatusLevel,
    LEVEL_WEIGHTS,
    get_level_weight,
    sanitize_text,
    sanitize_data,
)


# =====================================================================
# 1. Test Fixtures
# =====================================================================

@pytest.fixture
def temp_log_dir(tmp_path):
    """Temporary file path fixture for inspecting written JSONL audit logs with clean teardown."""
    log_dir = str(tmp_path / "telemetry_logs")
    os.makedirs(log_dir, exist_ok=True)
    yield log_dir
    if os.path.exists(log_dir):
        shutil.rmtree(log_dir, ignore_errors=True)


class InMemoryEventSubscriber:
    """Helper fixture class for managing in-memory event stream callbacks and asserting deliveries."""

    def __init__(self):
        self.received: List[EventRecord] = []
        self._lock = threading.Lock()

    def callback(self, record: EventRecord) -> None:
        with self._lock:
            self.received.append(record)

    def clear(self) -> None:
        with self._lock:
            self.received.clear()

    @property
    def count(self) -> int:
        with self._lock:
            return len(self.received)


@pytest.fixture
def in_memory_subscriber():
    """In-memory event stream subscriber fixture (queues/callbacks)."""
    return InMemoryEventSubscriber()


@pytest.fixture
def sample_raw_logs() -> Dict[str, Any]:
    """Sample raw logs containing execution metrics, error traces, and simulated API tokens."""
    return {
        "metric_event": {
            "source": "ExecutionTelemetry",
            "event_type": EventType.TOOL_EXECUTION,
            "status_level": StatusLevel.INFO,
            "message": "Step executed in 12.4ms with 98.2% accuracy",
            "trace_id": "trace-uuid-1234-abcd",
            "payload": {
                "cpu_usage_pct": 24.5,
                "memory_mb": 512.8,
                "step_index": 4,
            },
        },
        "error_event": {
            "source": "WorkflowOrchestrator",
            "event_type": EventType.SYSTEM,
            "status_level": StatusLevel.ERROR,
            "message": "ConnectionRefusedError: Failed to reach internal worker node:8080",
            "trace_id": "trace-uuid-err-5678",
            "payload": {
                "exception": "ConnectionRefusedError",
                "retry_count": 3,
                "backoff_seconds": 1.5,
            },
        },
        "sensitive_event": {
            "source": "AuthGateway",
            "event_type": EventType.SYSTEM,
            "status_level": StatusLevel.INFO,
            "message": "Authenticated user with Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 and api_key=sk-proj1234567890abcdef12345678",
            "trace_id": "trace-sec-9999",
            "payload": {
                "password": "super_secret_db_password",
                "token": "ghp_abcdefghijklmnopqrstuvwxyz123456",
                "connection_string": "postgres://telemetry_admin:hunter2_pass@127.0.0.1:5432/audit_db",
                "safe_field": "public_user_id_42",
            },
        },
    }


# =====================================================================
# 2. Test Cases
# =====================================================================

def test_structured_log_emission(temp_log_dir, sample_raw_logs):
    """
    Emits a structured message and asserts timestamp, level, caller tag,
    tracing ID, payload metadata, and JSON schema conformity.
    """
    logger = NodeLog(log_dir=temp_log_dir, maxlen=100)
    raw = sample_raw_logs["metric_event"]

    record = logger.emit(
        caller_tag=raw["source"],
        event_type=raw["event_type"],
        level=raw["status_level"],
        message=raw["message"],
        trace_id=raw["trace_id"],
        payload=raw["payload"],
    )

    # 1. Assert record instance and non-suppressed emission
    assert record is not None
    assert isinstance(record, EventRecord)

    # 2. Assert sequence ID monotonicity
    assert record.seq_id == 1

    # 3. Assert timestamp conformity (ISO 8601 UTC timestamp format)
    assert record.timestamp.endswith("Z")
    parsed_dt = datetime.fromisoformat(record.timestamp.replace("Z", "+00:00"))
    assert parsed_dt.tzinfo is not None

    # 4. Assert severity level and caller tag aliases
    assert record.level == "INFO"
    assert record.status_level == "INFO"
    assert record.caller_tag == "ExecutionTelemetry"
    assert record.source == "ExecutionTelemetry"

    # 5. Assert execution tracing ID and payload metadata
    assert record.trace_id == "trace-uuid-1234-abcd"
    assert record.payload == raw["payload"]
    assert record.payload["cpu_usage_pct"] == 24.5

    # 6. Assert JSON schema conformity
    dict_schema = record.to_dict()
    assert isinstance(dict_schema, dict)
    expected_fields = {"seq_id", "timestamp", "source", "event_type", "status_level", "message", "trace_id", "payload"}
    assert expected_fields.issubset(dict_schema.keys())

    # Verify serialization and round-trip conformity
    json_output = record.to_json()
    decoded = json.loads(json_output)
    assert decoded["seq_id"] == 1
    assert decoded["source"] == "ExecutionTelemetry"
    assert decoded["trace_id"] == "trace-uuid-1234-abcd"
    assert decoded["status_level"] == "INFO"


def test_sink_filtering_levels(temp_log_dir):
    """
    Configures log level thresholds (e.g., WARNING) and asserts that DEBUG and INFO
    events are suppressed while higher severities pass to the sink buffer.
    """
    # Configure threshold to WARNING
    logger = NodeLog(log_dir=temp_log_dir, min_level=StatusLevel.WARNING, maxlen=50)

    # Emit logs across the severity spectrum
    r_debug = logger.emit(source="Worker", event_type=EventType.SYSTEM, status_level=StatusLevel.DEBUG, message="Debug trace info")
    r_info = logger.emit(source="Worker", event_type=EventType.SYSTEM, status_level=StatusLevel.INFO, message="Standard operational info")
    r_warn = logger.emit(source="Worker", event_type=EventType.SYSTEM, status_level=StatusLevel.WARNING, message="Resource limit warning")
    r_error = logger.emit(source="Worker", event_type=EventType.SYSTEM, status_level=StatusLevel.ERROR, message="Task failure error")
    r_critical = logger.emit(source="Worker", event_type=EventType.SYSTEM, status_level=StatusLevel.CRITICAL, message="System crash critical")

    # Assert suppression of DEBUG and INFO
    assert r_debug is None, "DEBUG event should be suppressed under WARNING threshold"
    assert r_info is None, "INFO event should be suppressed under WARNING threshold"
    assert logger.suppressed_count == 2

    # Assert acceptance of WARNING and higher
    assert r_warn is not None, "WARNING event should pass"
    assert r_error is not None, "ERROR event should pass"
    assert r_critical is not None, "CRITICAL event should pass"

    # Assert sink in-memory buffer contents
    buffer_records = logger.query()
    assert len(buffer_records) == 3
    retained_levels = [rec.status_level for rec in buffer_records]
    assert retained_levels == ["WARNING", "ERROR", "CRITICAL"]
    assert "DEBUG" not in retained_levels
    assert "INFO" not in retained_levels

    # Dynamically elevate threshold to ERROR
    logger.set_min_level(StatusLevel.ERROR)
    r_warn2 = logger.emit(source="Worker", event_type=EventType.SYSTEM, status_level=StatusLevel.WARNING, message="Second warning")
    r_err2 = logger.emit(source="Worker", event_type=EventType.SYSTEM, status_level=StatusLevel.ERROR, message="Second error")

    assert r_warn2 is None, "WARNING should now be suppressed under ERROR threshold"
    assert r_err2 is not None, "ERROR should pass under ERROR threshold"
    assert len(logger.query()) == 4


def test_pub_sub_live_delivery(temp_log_dir, in_memory_subscriber):
    """
    Registers an event callback listener, triggers an event, and confirms
    immediate, non-blocking delivery to consumers (e.g. Streamlit UI / webhooks).
    """
    logger = NodeLog(log_dir=temp_log_dir)

    # 1. Register subscriber callback
    logger.subscribe_callback(in_memory_subscriber.callback)
    assert in_memory_subscriber.count == 0

    # 2. Trigger an event and confirm immediate delivery
    emitted = logger.emit(
        source="StreamlitUI",
        event_type=EventType.USER_MESSAGE,
        status_level=StatusLevel.INFO,
        message="User invoked workflow execution",
    )

    # Immediate non-blocking delivery assertion
    assert in_memory_subscriber.count == 1
    delivered = in_memory_subscriber.received[0]
    assert delivered.seq_id == emitted.seq_id
    assert delivered.source == "StreamlitUI"
    assert delivered.message == "User invoked workflow execution"

    # 3. Test selective filter callback
    error_collector: List[EventRecord] = []
    logger.subscribe_callback(
        callback=lambda r: error_collector.append(r),
        filter_fn=lambda r: r.status_level == "ERROR"
    )

    logger.emit("Source", EventType.SYSTEM, StatusLevel.INFO, "Regular info")
    assert len(error_collector) == 0
    assert in_memory_subscriber.count == 2

    logger.emit("Source", EventType.SYSTEM, StatusLevel.ERROR, "Critical malfunction")
    assert len(error_collector) == 1
    assert error_collector[0].message == "Critical malfunction"
    assert in_memory_subscriber.count == 3

    # 4. Test unsubscribe callback
    logger.unsubscribe_callback(in_memory_subscriber.callback)
    logger.emit("Source", EventType.SYSTEM, StatusLevel.INFO, "After unsubscribing")
    assert in_memory_subscriber.count == 3  # Count unchanged


def test_credential_masking(temp_log_dir, sample_raw_logs):
    """
    Supplies a log message containing mock API keys/tokens/passwords/URIs
    and verifies that the logged string replaces them with masks (e.g., Bearer ********).
    """
    mask_str = "********"
    logger = NodeLog(log_dir=temp_log_dir, enable_sanitization=True, mask=mask_str)
    raw = sample_raw_logs["sensitive_event"]

    record = logger.emit(
        source=raw["source"],
        event_type=raw["event_type"],
        status_level=raw["status_level"],
        message=raw["message"],
        payload=raw["payload"],
    )

    assert record is not None

    # 1. Bearer Token Masking
    assert f"Bearer {mask_str}" in record.message
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in record.message

    # 2. API Key Masking in message string
    assert f"api_key={mask_str}" in record.message
    assert "sk-proj1234567890abcdef12345678" not in record.message

    # 3. Sensitive Payload Key Masking
    assert record.payload["password"] == mask_str
    assert "super_secret_db_password" not in str(record.payload)
    assert record.payload["token"] == mask_str
    assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in str(record.payload)

    # 4. Connection URI Credential Masking in payload
    sanitized_uri = record.payload["connection_string"]
    assert f":{mask_str}@" in sanitized_uri
    assert "hunter2_pass" not in sanitized_uri
    assert "postgres://telemetry_admin:" in sanitized_uri

    # 5. Non-sensitive payload preservation
    assert record.payload["safe_field"] == "public_user_id_42"


def test_thread_safe_concurrent_writes(temp_log_dir):
    """
    Dispatches concurrent writes from multiple threads and verifies exact event
    counts, monotonic sequence integrity, and no dropped events in the sink buffer.
    """
    num_threads = 16
    events_per_thread = 50
    total_expected_events = num_threads * events_per_thread

    # Sized to hold all expected events without ring-buffer eviction
    logger = NodeLog(log_dir=temp_log_dir, maxlen=total_expected_events + 100)

    barrier = threading.Barrier(num_threads)
    exceptions: List[Exception] = []

    def worker(thread_idx: int):
        try:
            # Synchronize threads so all start emitting simultaneously
            barrier.wait(timeout=5.0)
            for i in range(events_per_thread):
                logger.emit(
                    caller_tag=f"WorkerThread-{thread_idx}",
                    event_type=EventType.TELEMETRY,
                    level=StatusLevel.INFO,
                    message=f"Event #{i} from thread {thread_idx}",
                    trace_id=f"trace-{thread_idx}-{i}",
                    payload={"thread": thread_idx, "iteration": i},
                )
        except Exception as e:
            exceptions.append(e)

    # Dispatch worker threads
    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, t) for t in range(num_threads)]
        for f in futures:
            f.result()

    # Assert no exceptions occurred in worker threads
    assert len(exceptions) == 0, f"Thread worker encountered exceptions: {exceptions}"

    # Assert exact event count in sink buffer
    all_records = logger.query(limit=total_expected_events + 200)
    assert len(all_records) == total_expected_events, (
        f"Expected {total_expected_events} events, found {len(all_records)}"
    )

    # Assert strictly unique and complete sequence IDs (1 .. total_expected_events)
    seq_ids = [rec.seq_id for rec in all_records]
    assert len(seq_ids) == len(set(seq_ids)), "Duplicate sequence IDs detected in concurrent writes!"
    assert sorted(seq_ids) == list(range(1, total_expected_events + 1)), (
        "Sequence IDs have gaps or are inconsistent!"
    )

    # Assert data integrity of each event
    for rec in all_records:
        assert rec.timestamp is not None
        assert rec.source.startswith("WorkerThread-")
        assert rec.event_type == "TELEMETRY"
        assert rec.status_level == "INFO"
        assert rec.trace_id is not None
        assert "thread" in rec.payload
        assert "iteration" in rec.payload


def test_audit_jsonl_file_persistence(temp_log_dir):
    """
    Verifies that local JSONL audit logs are correctly persisted and formatted on disk.
    """
    logger = NodeLog(log_dir=temp_log_dir, auto_flush_disk=True)

    logger.emit("ServiceA", EventType.SYSTEM, StatusLevel.INFO, "Audit message 1")
    logger.emit("ServiceB", EventType.TOOL_EXECUTION, StatusLevel.WARN, "Audit message 2")
    logger.flush()

    # Verify JSONL log file presence
    jsonl_files = [f for f in os.listdir(temp_log_dir) if f.startswith("nodelog-") and f.endswith(".jsonl")]
    assert len(jsonl_files) >= 1, f"No nodelog-*.jsonl files found in {temp_log_dir}. Files: {os.listdir(temp_log_dir)}"
    log_file = os.path.join(temp_log_dir, jsonl_files[0])
    assert os.path.exists(log_file), f"Audit log file {log_file} does not exist"

    # Read and parse lines
    with open(log_file, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    assert len(lines) == 2
    record_1 = json.loads(lines[0])
    record_2 = json.loads(lines[1])

    assert record_1["source"] == "ServiceA"
    assert record_1["message"] == "Audit message 1"
    assert record_2["source"] == "ServiceB"
    assert record_2["status_level"] == "WARN"


def test_console_sink_streaming():
    """
    Verifies optional console stdout sink formatting.
    """
    capture_stream = io.StringIO()
    logger = NodeLog(
        enable_console=True,
        console_stream=capture_stream,
    )

    logger.emit(
        caller_tag="ConsoleTester",
        event_type=EventType.SYSTEM,
        level=StatusLevel.INFO,
        message="Streamed to console stream",
    )

    output = capture_stream.getvalue()
    assert "[INFO]" in output
    assert "[ConsoleTester]" in output
    assert "Streamed to console stream" in output


# =====================================================================
# 3. Runnable Entrypoint
# =====================================================================

if __name__ == "__main__":
    import pytest
    print(f"Executing NodeLog test suite: {__file__}")
    sys.exit(pytest.main(["-v", __file__]))
