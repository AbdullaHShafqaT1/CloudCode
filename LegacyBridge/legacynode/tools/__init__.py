"""
LegacyNode — Tools Package
Registry of all available agent tools.
"""

from .file_reader import FileReader
from .file_writer import FileWriter
from .node_insight_bridge import NodeInsightBridge
from .node_link_bridge import NodeLinkBridge
from .node_pulse_bridge import NodePulseBridge
from .terminal_executor import TerminalExecutor

__all__ = ["FileReader", "FileWriter", "NodeInsightBridge", "NodeLinkBridge", "NodePulseBridge", "TerminalExecutor"]



def build_tool_registry(
    workspace_root: str,
    file_reader: "FileReader",
    file_writer: "FileWriter",
    terminal_executor: "TerminalExecutor",
) -> dict:
    """
    Returns a dict mapping tool names to their async callable signatures.
    The AgentController uses this registry to dispatch tool calls from the LLM.
    """
    return {
        "read_file": file_reader.read_file,
        "read_files": file_reader.read_files,
        "scan_tree": file_reader.scan_tree,
        "write_file": file_writer.write_file,
        "apply_diff": file_writer.apply_diff,
        "create_directory": file_writer.create_directory,
        "execute_command": terminal_executor.execute,
    }
