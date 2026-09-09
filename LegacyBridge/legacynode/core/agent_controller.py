"""
LegacyNode — Agent Controller
AutoGen AgentChat-powered multi-agent execution loop.
Orchestrates a Coder agent and a Critic/Executor agent with tool use,
self-correction on errors, and real-time state emission.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.conditions import MaxMessageTermination, TextMentionTermination
from autogen_agentchat.messages import TextMessage, ToolCallRequestEvent, ToolCallExecutionEvent, ToolCallSummaryMessage
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.ui import Console
from autogen_core.tools import FunctionTool

from .llm_client import LLMClient, LLMConfig
from .notification_hub import NotificationHub, NotificationLevel
from .state_manager import AgentStatus, ExecutionStep, StateManager
from ..tools import build_tool_registry
from ..tools.file_reader import FileReader
from ..tools.file_writer import FileWriter
from ..tools.terminal_executor import TerminalExecutor

log = structlog.get_logger(__name__)

# ─── System Prompts ──────────────────────────────────────────────────────────

CODER_SYSTEM_PROMPT = """You are LegacyNode's expert Coder Agent. You operate inside a local
development environment and can read/write files, scan directory trees, and execute shell commands.

Your mission: Implement the coding task described by the user with high quality, production-ready code.

Guidelines:
- Always scan the relevant directory tree before making changes.
- Read existing files before modifying them — never overwrite blindly.
- Write atomic, minimal diffs when updating files.
- After executing a command, always check the return code and stderr.
- If a command fails, diagnose the error and self-correct before giving up.
- When done, clearly state: "TASK_COMPLETE" followed by a concise summary of what was done.

Available tools: read_file, read_files, scan_tree, write_file, apply_diff,
create_directory, execute_command.
"""

CRITIC_SYSTEM_PROMPT = """You are LegacyNode's Critic Agent. Your role is to review the Coder
Agent's work and flag issues.

Your responsibilities:
- Check that the implementation matches the task requirements.
- Identify missing tests, documentation, or edge cases.
- Detect potential bugs, security issues, or style violations.
- If the implementation is correct and complete, respond with: "APPROVED"
- If changes are needed, clearly explain what needs fixing.

Do NOT implement changes yourself — guide the Coder Agent.
"""


class AgentController:
    """
    Central orchestrator that:
    1. Builds AutoGen agents with tool access
    2. Runs the RoundRobin multi-agent loop for coding tasks
    3. Monitors for errors and triggers self-correction
    4. Emits state updates to StateManager and NotificationHub
    5. Enforces max_iterations to prevent runaway loops
    """

    def __init__(
        self,
        llm_client: LLMClient,
        state_manager: StateManager,
        notification_hub: NotificationHub,
        workspace_root: str = ".",
        max_iterations: int = 25,
        self_correction_retries: int = 3,
        human_in_loop: bool = False,
        custom_system_prompt: Optional[str] = None,
    ):
        self._llm = llm_client
        self._state = state_manager
        self._hub = notification_hub
        self._workspace_root = workspace_root
        self._max_iterations = max_iterations
        self._correction_retries = self_correction_retries
        self._human_in_loop = human_in_loop
        self._custom_prompt = custom_system_prompt

        self._file_reader = FileReader(workspace_root)
        self._file_writer = FileWriter(workspace_root)
        self._terminal = TerminalExecutor(workspace_root)
        self._tool_registry = build_tool_registry(
            workspace_root, self._file_reader, self._file_writer, self._terminal
        )

        self._stop_event = asyncio.Event()
        self._current_team: Optional[RoundRobinGroupChat] = None

    # ─── Control ─────────────────────────────────────────────────────────────

    async def stop(self) -> None:
        """Signal the running agent loop to stop after the current step."""
        self._stop_event.set()
        await self._state.cancel_current_task()
        await self._state.set_status(AgentStatus.IDLE)
        log.info("AgentController stop requested")

    # ─── Task Runner ─────────────────────────────────────────────────────────

    async def run_task(self, task_description: str) -> str:
        """
        Main entry point. Enqueues and executes a single task.
        Returns a summary string on completion or raises on failure.
        """
        self._stop_event.clear()
        task = await self._state.enqueue_task(task_description)
        await self._state.set_current_task(task)
        await self._state.set_status(AgentStatus.RUNNING)
        self._state.clear_execution_state()

        await self._hub.push(
            NotificationLevel.INFO,
            "Task Started",
            f"Running: {task_description[:100]}",
            task_id=task.id,
        )

        try:
            summary = await self._execute_with_autogen(task_description, task.id)
            await self._state.complete_task(task, summary)
            await self._state.set_status(AgentStatus.IDLE)
            await self._hub.push(
                NotificationLevel.SUCCESS,
                "Task Completed",
                summary[:200],
                task_id=task.id,
            )
            return summary

        except asyncio.CancelledError:
            log.info("Task cancelled via CancelledError")
            await self._state.fail_task(task, "Cancelled by user")
            await self._state.set_status(AgentStatus.IDLE)
            raise

        except Exception as exc:
            err_msg = str(exc)
            log.error("Task failed with exception", exc_info=True)
            await self._state.fail_task(task, err_msg)
            await self._state.set_status(AgentStatus.ERROR)
            await self._hub.push(
                NotificationLevel.ERROR,
                "Task Failed",
                err_msg[:300],
                task_id=task.id,
            )
            raise

    # ─── AutoGen Team Construction ────────────────────────────────────────────

    def _build_autogen_tools(self) -> list[FunctionTool]:
        """Wrap tool registry functions as AutoGen FunctionTools."""
        tool_defs: list[FunctionTool] = []

        async def read_file(path: str) -> str:
            """Read the content of a single file at the given path."""
            return await self._tool_registry["read_file"](path)

        async def scan_tree(root: str = ".", max_depth: int = 4) -> str:
            """Scan a directory tree and return a JSON representation."""
            return await self._tool_registry["scan_tree"](root, max_depth)

        async def write_file(path: str, content: str) -> str:
            """Write content to a file (creates if not exists, overwrites if exists)."""
            return await self._tool_registry["write_file"](path, content)

        async def apply_diff(path: str, unified_diff: str) -> str:
            """Apply a unified diff patch to an existing file."""
            return await self._tool_registry["apply_diff"](path, unified_diff)

        async def create_directory(path: str) -> str:
            """Create a directory and all required parent directories."""
            return await self._tool_registry["create_directory"](path)

        async def execute_command(command: str, cwd: str = ".", timeout: int = 30) -> str:
            """Execute a shell command and return stdout + stderr + returncode."""
            result = await self._tool_registry["execute_command"](command, cwd, timeout)
            # Emit to live output buffer
            for line in result.get("stdout", "").splitlines():
                self._state.add_live_output(f"[stdout] {line}")
            for line in result.get("stderr", "").splitlines():
                self._state.add_live_output(f"[stderr] {line}")
            return json.dumps(result)

        for fn in [read_file, scan_tree, write_file, apply_diff, create_directory, execute_command]:
            tool_defs.append(FunctionTool(fn, description=fn.__doc__ or ""))

        return tool_defs

    def _build_model_client(self):
        """Build an AutoGen-compatible model client from our LLMConfig."""
        from autogen_ext.models.openai import OpenAIChatCompletionClient

        return OpenAIChatCompletionClient(
            model=self._llm._config.model,
            base_url=self._llm._config.base_url,
            api_key=self._llm._config.api_key,
            model_capabilities={
                "vision": False,
                "function_calling": True,
                "json_output": True,
            },
            temperature=self._llm._config.temperature,
            max_tokens=self._llm._config.max_tokens,
        )

    async def _execute_with_autogen(self, task_description: str, task_id: str) -> str:
        """
        Build and run the AutoGen multi-agent team.
        Uses RoundRobinGroupChat with a CoderAgent + CriticAgent.
        Self-corrects on tool errors up to self_correction_retries times.
        """
        tools = self._build_autogen_tools()
        model_client = self._build_model_client()

        coder_agent = AssistantAgent(
            name="CoderAgent",
            description="Expert coding agent that implements tasks using tools.",
            model_client=model_client,
            tools=tools,
            system_message=self._custom_prompt or CODER_SYSTEM_PROMPT,
            reflect_on_tool_use=True,
        )

        critic_agent = AssistantAgent(
            name="CriticAgent",
            description="Reviews the coder's work and provides feedback.",
            model_client=model_client,
            system_message=CRITIC_SYSTEM_PROMPT,
        )

        termination = (
            TextMentionTermination("TASK_COMPLETE")
            | TextMentionTermination("APPROVED")
            | MaxMessageTermination(self._max_iterations)
        )

        team = RoundRobinGroupChat(
            participants=[coder_agent, critic_agent],
            termination_condition=termination,
        )
        self._current_team = team

        step_idx = 0
        summary_lines: list[str] = []
        error_count = 0

        log.info("AutoGen team started", task_id=task_id)

        async for message in team.run_stream(
            task=TextMessage(content=task_description, source="user")
        ):
            if self._stop_event.is_set():
                log.info("Stop event detected — breaking agent loop")
                break

            if isinstance(message, TextMessage):
                content = message.content
                self._state.add_live_output(f"[{message.source}] {content[:300]}")
                summary_lines.append(f"{message.source}: {content[:200]}")

                step = ExecutionStep(
                    index=step_idx,
                    thought=content,
                    action=None,
                )
                self._state.add_execution_step(step)
                step_idx += 1

            elif isinstance(message, ToolCallRequestEvent):
                calls_str = ", ".join(c.name for c in message.content)
                self._state.add_live_output(f"[tools] Calling: {calls_str}")
                log.debug("Tool calls dispatched", tools=calls_str, task_id=task_id)

            elif isinstance(message, ToolCallExecutionEvent):
                for result in message.content:
                    # In autogen 0.7.x is_error was removed; check for error prefix
                    result_str = str(result.content) if hasattr(result, "content") else str(result)
                    is_error = result_str.lower().startswith("error") or "traceback" in result_str.lower()
                    if is_error:
                        error_count += 1
                        err_text = result_str[:200]
                        self._state.add_live_output(f"[tool-error] {err_text}")
                        await self._hub.push(
                            NotificationLevel.WARNING,
                            f"Tool Error (#{error_count})",
                            err_text,
                            task_id=task_id,
                        )
                        log.warning(
                            "Tool returned error",
                            error=err_text,
                        )
                        if error_count >= self._correction_retries:
                            await self._hub.push(
                                NotificationLevel.ERROR,
                                "Max tool errors reached",
                                f"Stopping after {error_count} tool failures.",
                                task_id=task_id,
                            )

        # Extract completion summary
        final_summary = self._extract_summary(summary_lines)
        log.info("AutoGen team completed", steps=step_idx, errors=error_count)
        return final_summary

    @staticmethod
    def _extract_summary(lines: list[str]) -> str:
        """Extract a readable summary from the final agent messages."""
        # Look for TASK_COMPLETE marker
        for line in reversed(lines):
            if "TASK_COMPLETE" in line or "APPROVED" in line:
                return line.split("TASK_COMPLETE")[-1].strip() or line
        # Fallback: return last 3 meaningful lines
        meaningful = [l for l in lines if len(l) > 20][-3:]
        return " | ".join(meaningful) if meaningful else "Task finished (no summary available)"
