import pytest
import asyncio
from nodelog import NodeLog, EventType, StatusLevel
import os
import shutil

@pytest.fixture
def temp_log_dir(tmpdir):
    log_dir = str(tmpdir.mkdir("logs"))
    yield log_dir
    if os.path.exists(log_dir):
        shutil.rmtree(log_dir)

@pytest.mark.asyncio
async def test_pubsub_streaming(temp_log_dir):
    logger = NodeLog(log_dir=temp_log_dir, maxlen=10)
    await logger.start()
    
    # We create a subscriber
    subscriber = logger.subscribe()
    
    # We need a task to consume
    received_events = []
    
    async def consumer():
        async for event in subscriber:
            received_events.append(event)
            if len(received_events) >= 2:
                break
                
    consumer_task = asyncio.create_task(consumer())
    
    # Yield control so consumer starts and registers queue
    await asyncio.sleep(0.01)
    
    # Emit events
    logger.emit("SourceA", EventType.SYSTEM, StatusLevel.INFO, "Msg 1")
    logger.emit("SourceB", EventType.SYSTEM, StatusLevel.ERROR, "Msg 2")
    
    # Wait for consumer
    await asyncio.wait_for(consumer_task, timeout=1.0)
    
    assert len(received_events) == 2
    assert received_events[0].message == "Msg 1"
    assert received_events[1].message == "Msg 2"
    
    await logger.stop()

@pytest.mark.asyncio
async def test_pubsub_filtering(temp_log_dir):
    logger = NodeLog(log_dir=temp_log_dir, maxlen=10)
    await logger.start()
    
    # We create a subscriber with a filter
    def only_errors(record):
        return record.status_level == StatusLevel.ERROR
        
    subscriber = logger.subscribe(filter_fn=only_errors)
    received_events = []
    
    async def consumer():
        async for event in subscriber:
            received_events.append(event)
            if len(received_events) >= 1:
                break
                
    consumer_task = asyncio.create_task(consumer())
    
    # Yield control so consumer starts and registers queue
    await asyncio.sleep(0.01)
    
    # Emit events
    logger.emit("SourceA", EventType.SYSTEM, StatusLevel.INFO, "Info Msg")
    logger.emit("SourceB", EventType.SYSTEM, StatusLevel.ERROR, "Error Msg")
    
    await asyncio.wait_for(consumer_task, timeout=1.0)
    
    assert len(received_events) == 1
    assert received_events[0].message == "Error Msg"
    
    await logger.stop()
