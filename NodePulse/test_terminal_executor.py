import pytest
import os
import asyncio
from terminal_executor import TerminalExecutorTool

@pytest.fixture
def executor(tmp_path):
    return TerminalExecutorTool(str(tmp_path))

@pytest.mark.asyncio
async def test_successful_execution(executor):
    result = await executor.call(
        "run_command", 
        command='python -c "print(\'hello\')"'
    )
    assert result["status"] == "success"
    assert result["exit_code"] == 0
    assert "hello" in result["stdout"]
    assert result["truncated"] is False

@pytest.mark.asyncio
async def test_non_zero_exit_code(executor):
    result = await executor.call(
        "run_command", 
        command='python -c "import sys; sys.exit(1)"'
    )
    assert result["status"] == "error"
    assert result["exit_code"] == 1

@pytest.mark.asyncio
async def test_timeout(executor):
    result = await executor.call(
        "run_command", 
        command='python -c "import time; time.sleep(2)"',
        timeout=1
    )
    assert result["status"] == "timeout"
    assert "exceeded timeout" in result["error_message"]

@pytest.mark.asyncio
async def test_forbidden_command(executor):
    result = await executor.call(
        "run_command", 
        command="rm -rf /"
    )
    assert result["status"] == "error"
    assert "forbidden pattern" in result["error_message"]

@pytest.mark.asyncio
async def test_invalid_cwd(executor):
    result = await executor.call(
        "run_command", 
        command='python -c "print(\'hello\')"',
        cwd="../../"
    )
    assert result["status"] == "error"
    assert "outside the allowed project root" in result["error_message"]

@pytest.mark.asyncio
async def test_truncation(executor):
    # Print 110000 characters. Since print adds a newline, it's 110001 characters.
    result = await executor.call(
        "run_command", 
        command='python -c "import sys; sys.stdout.write(\'A\' * 110000)"'
    )
    assert result["status"] == "success"
    assert result["truncated"] is True
    # The length might be exactly 100000 because of max_chars
    assert len(result["stdout"]) == 100000
