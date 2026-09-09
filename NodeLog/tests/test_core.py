import pytest
from nodelog import NodeLog, EventType, StatusLevel
import os
import shutil

@pytest.fixture
def temp_log_dir(tmpdir):
    log_dir = str(tmpdir.mkdir("logs"))
    yield log_dir
    if os.path.exists(log_dir):
        shutil.rmtree(log_dir)

def test_nodelog_initialization(temp_log_dir):
    logger = NodeLog(log_dir=temp_log_dir, maxlen=10)
    assert logger.maxlen == 10
    assert len(logger._buffer) == 0

def test_emit_adds_to_buffer(temp_log_dir):
    logger = NodeLog(log_dir=temp_log_dir, maxlen=10)
    record = logger.emit(
        source="NodePulse",
        event_type=EventType.HEARTBEAT,
        status_level=StatusLevel.INFO,
        message="Pulse OK"
    )
    
    assert len(logger._buffer) == 1
    assert logger._buffer[0].seq_id == 1
    assert logger._buffer[0].source == "NodePulse"
    assert logger._buffer[0].event_type == "HEARTBEAT"
    
def test_ring_buffer_eviction(temp_log_dir):
    logger = NodeLog(log_dir=temp_log_dir, maxlen=3)
    logger.emit("Source", EventType.SYSTEM, StatusLevel.INFO, "Msg 1")
    logger.emit("Source", EventType.SYSTEM, StatusLevel.INFO, "Msg 2")
    logger.emit("Source", EventType.SYSTEM, StatusLevel.INFO, "Msg 3")
    logger.emit("Source", EventType.SYSTEM, StatusLevel.INFO, "Msg 4")
    
    assert len(logger._buffer) == 3
    assert logger._buffer[0].message == "Msg 2"
    assert logger._buffer[2].message == "Msg 4"
