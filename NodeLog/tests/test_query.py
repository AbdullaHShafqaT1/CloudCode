import pytest
from nodelog import NodeLog, EventType, StatusLevel

def test_query_no_filters():
    logger = NodeLog(log_dir="dummy", maxlen=10)
    for i in range(5):
        logger.emit("SourceA", EventType.SYSTEM, StatusLevel.INFO, f"Msg {i}")
        
    results = logger.query()
    assert len(results) == 5
    assert results[0].message == "Msg 0"

def test_query_by_status():
    logger = NodeLog(log_dir="dummy", maxlen=10)
    logger.emit("SourceA", EventType.SYSTEM, StatusLevel.INFO, "Info msg")
    logger.emit("SourceA", EventType.SYSTEM, StatusLevel.ERROR, "Error msg")
    logger.emit("SourceA", EventType.SYSTEM, StatusLevel.INFO, "Info msg 2")
    
    results = logger.query(status_levels=[StatusLevel.ERROR])
    assert len(results) == 1
    assert results[0].message == "Error msg"

def test_query_pagination():
    logger = NodeLog(log_dir="dummy", maxlen=10)
    for i in range(10):
        logger.emit("SourceA", EventType.SYSTEM, StatusLevel.INFO, f"Msg {i}")
        
    results = logger.query(limit=3, offset=2)
    assert len(results) == 3
    assert results[0].message == "Msg 2"
    assert results[2].message == "Msg 4"
