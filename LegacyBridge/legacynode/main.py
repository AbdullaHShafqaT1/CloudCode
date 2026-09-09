"""
LegacyNode — System Entry Point (main.py)

Bootstraps the full system:
  1. Loads config (config.yaml + .env)
  2. Initializes StateManager, NotificationHub, LLMClient
  3. Launches the Streamlit dashboard in a background process
  4. Runs the AgentController event loop — picks tasks from queue
     and dispatches them to the AutoGen multi-agent team

Usage:
    # Normal mode (launches dashboard + agent loop):
    python main.py

    # Headless agent mode (no dashboard):
    python main.py --no-dashboard

    # Single task mode (non-interactive):
    python main.py --task "Write a hello world FastAPI app with tests"

    # Check system health:
    python main.py --health-check
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

import structlog
import yaml
from dotenv import load_dotenv

# ─── Path Bootstrap ──────────────────────────────────────────────────────────
# Ensure legacynode/ is importable regardless of where main.py is called from
_base = Path(__file__).resolve().parent
if str(_base.parent) not in sys.path:
    sys.path.insert(0, str(_base.parent))

from legacynode.core.agent_controller import AgentController
from legacynode.core.llm_client import LLMClient, LLMConfig
from legacynode.core.notification_hub import NotificationHub, NotificationLevel
from legacynode.core.state_manager import AgentStatus, StateManager

# ─── Logging Setup ───────────────────────────────────────────────────────────

def _setup_logging(level: str = "INFO", fmt: str = "console") -> None:
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="%H:%M:%S", utc=True),
        structlog.dev.ConsoleRenderer() if fmt == "console" else structlog.processors.JSONRenderer(),
    ]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


log = structlog.get_logger("legacynode.main")


# ─── Config Loading ───────────────────────────────────────────────────────────

def load_config() -> dict:
    """Load config.yaml and overlay with .env variables."""
    load_dotenv()  # Load .env into os.environ

    config_path = _base / "config.yaml"
    if not config_path.exists():
        log.warning("config.yaml not found — using defaults", path=str(config_path))
        return {}

    with open(config_path) as f:
        cfg = yaml.safe_load(f) or {}

    # Overlay with environment variables (env takes priority)
    tunnel_url = os.environ.get("TUNNEL_URL", "")
    if tunnel_url:
        cfg.setdefault("llm", {})["base_url"] = tunnel_url.rstrip("/") + "/v1"

    if os.environ.get("LLM_MODEL"):
        cfg.setdefault("llm", {})["model"] = os.environ["LLM_MODEL"]

    if os.environ.get("LLM_API_KEY"):
        cfg.setdefault("llm", {})["api_key"] = os.environ["LLM_API_KEY"]

    return cfg


# ─── Streamlit Launcher ───────────────────────────────────────────────────────

def launch_dashboard(config: dict) -> subprocess.Popen:
    """Start Streamlit dashboard in a background subprocess."""
    ui_cfg = config.get("ui", {})
    host = ui_cfg.get("host", "localhost")
    port = ui_cfg.get("port", 8501)
    app_path = _base / "ui" / "app.py"

    cmd = [
        sys.executable, "-m", "streamlit", "run",
        str(app_path),
        "--server.headless", "true",
        "--server.address", host,
        "--server.port", str(port),
        "--theme.base", "dark",
        "--theme.primaryColor", "#3B82F6",
        "--theme.backgroundColor", "#0D1117",
        "--theme.secondaryBackgroundColor", "#161B22",
        "--theme.textColor", "#F0F6FC",
        "--browser.gatherUsageStats", "false",
    ]
    log.info("Launching dashboard", url=f"http://{host}:{port}")
    proc = subprocess.Popen(cmd, env=os.environ.copy())
    return proc


# ─── Health Check ─────────────────────────────────────────────────────────────

async def run_health_check(llm_client: LLMClient) -> None:
    log.info("Running health check...")
    healthy = await llm_client.probe_health()
    if healthy:
        models = await llm_client.list_models()
        log.info("✅ LLM endpoint healthy", models=models)
    else:
        log.error("❌ LLM endpoint unreachable")
        sys.exit(1)


# ─── Agent Event Loop ─────────────────────────────────────────────────────────

async def agent_loop(
    controller: AgentController,
    state: StateManager,
    hub: NotificationHub,
) -> None:
    """
    Continuously dequeue and run tasks until shutdown.
    Designed to run as a long-lived asyncio coroutine.
    """
    log.info("Agent event loop started — waiting for tasks")
    await hub.push(NotificationLevel.INFO, "LegacyNode Ready", "Agent is online and waiting for tasks.")

    while True:
        try:
            # Block until a task is available (up to 2s polling for graceful shutdown)
            try:
                task = await asyncio.wait_for(state.dequeue_task(), timeout=2.0)
            except asyncio.TimeoutError:
                continue

            log.info("Dequeued task", task_id=task.id, description=task.description[:80])

            try:
                summary = await controller.run_task(task.description)
                log.info("Task succeeded", task_id=task.id, summary=summary[:80])
            except asyncio.CancelledError:
                log.info("Task cancelled", task_id=task.id)
            except Exception as e:
                log.error("Task raised exception", task_id=task.id, error=str(e), exc_info=True)

        except asyncio.CancelledError:
            log.info("Agent loop cancelled — shutting down")
            break
        except Exception as e:
            log.error("Unexpected error in agent loop", error=str(e), exc_info=True)
            await asyncio.sleep(1)


# ─── Main ─────────────────────────────────────────────────────────────────────

async def async_main(args: argparse.Namespace, config: dict) -> None:
    llm_cfg_raw = config.get("llm", {})
    agent_cfg = config.get("agent", {})
    notif_cfg = config.get("notifications", {})
    workspace_root = config.get("workspace", {}).get("root", ".")

    # ── Configure logging
    log_cfg = config.get("logging", {})
    _setup_logging(log_cfg.get("level", "INFO"), log_cfg.get("format", "console"))

    # ── Initialize core services
    state = StateManager(
        db_path=str(Path(workspace_root) / ".legacynode" / "state.db"),
        history_size=200,
    )
    await state.initialize()

    hub = NotificationHub(
        db_path=notif_cfg.get("db_path", ".legacynode/notifications.db"),
        max_memory=notif_cfg.get("max_in_memory", 100),
        retention_days=notif_cfg.get("retention_days", 7),
    )
    await hub.initialize()

    llm_client = LLMClient(
        LLMConfig(
            base_url=llm_cfg_raw.get("base_url", "http://localhost:11434/v1"),
            model=llm_cfg_raw.get("model", "qwen2.5-coder:32b"),
            api_key=llm_cfg_raw.get("api_key", "ollama"),
            timeout_seconds=float(llm_cfg_raw.get("timeout_seconds", 180)),
            max_retries=int(llm_cfg_raw.get("max_retries", 3)),
            retry_backoff_base=float(llm_cfg_raw.get("retry_backoff_base", 2.0)),
            temperature=float(llm_cfg_raw.get("temperature", 0.2)),
            max_tokens=int(llm_cfg_raw.get("max_tokens", 8192)),
            stream=bool(llm_cfg_raw.get("stream", True)),
        )
    )

    # ── Health check only?
    if args.health_check:
        await llm_client.connect()
        await run_health_check(llm_client)
        await state.close()
        await hub.close()
        await llm_client.close()
        return

    await llm_client.connect()
    log.info(
        "LegacyNode initialized",
        model=llm_cfg_raw.get("model"),
        workspace=workspace_root,
    )

    controller = AgentController(
        llm_client=llm_client,
        state_manager=state,
        notification_hub=hub,
        workspace_root=workspace_root,
        max_iterations=int(agent_cfg.get("max_iterations", 25)),
        self_correction_retries=int(agent_cfg.get("self_correction_retries", 3)),
        human_in_loop=bool(agent_cfg.get("human_in_loop", False)),
    )

    # ── Single task mode (non-interactive)
    if args.task:
        try:
            summary = await controller.run_task(args.task)
            print(f"\n✅ Task completed:\n{summary}")
        except Exception as e:
            print(f"\n❌ Task failed: {e}")
        finally:
            await state.close()
            await hub.close()
            await llm_client.close()
        return

    # ── Full daemon mode: run agent loop
    loop_task = asyncio.create_task(agent_loop(controller, state, hub))

    try:
        await loop_task
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Shutdown signal received")
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
    finally:
        await state.close()
        await hub.close()
        await llm_client.close()
        log.info("LegacyNode shutdown complete")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="legacynode",
        description="LegacyNode — Autonomous Coding Agent",
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Run in headless mode (no Streamlit UI).",
    )
    parser.add_argument(
        "--task",
        type=str,
        default=None,
        help="Run a single task and exit (non-interactive mode).",
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="Check LLM endpoint health and exit.",
    )
    args = parser.parse_args()

    config = load_config()

    dashboard_proc: Optional[subprocess.Popen] = None
    if not args.no_dashboard and not args.task and not args.health_check:
        dashboard_proc = launch_dashboard(config)
        import time; time.sleep(2)  # Give Streamlit a moment to start

    try:
        asyncio.run(async_main(args, config))
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt — exiting")
    finally:
        if dashboard_proc:
            dashboard_proc.terminate()
            log.info("Dashboard process terminated")


if __name__ == "__main__":
    from typing import Optional
    main()
