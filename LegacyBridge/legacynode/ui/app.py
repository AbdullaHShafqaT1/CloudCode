"""
LegacyNode — Streamlit Dashboard (ui/app.py)

4-tab control panel:
  Tab 1 — Control Center : start/stop agent, set tasks, tunnel health
  Tab 2 — Live Terminal  : real-time agent output stream
  Tab 3 — Notifications  : filterable alert feed with acknowledge
  Tab 4 — Task History   : paginated table of past tasks

Run with:
    streamlit run legacynode/ui/app.py
Or launched automatically by main.py.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import streamlit as st
from streamlit_autorefresh import st_autorefresh

# ─── Path Setup ──────────────────────────────────────────────────────────────
# Allow importing legacynode modules when running via streamlit run ui/app.py
_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from legacynode.core.state_manager import StateManager, AgentStatus
from legacynode.core.notification_hub import NotificationHub, NotificationLevel
from legacynode.ui.components.status_badge import render_status_badge
from legacynode.ui.components.notification_card import render_notification_card

# ─── Page Config ─────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="LegacyNode",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Load Custom CSS ─────────────────────────────────────────────────────────

_css_path = Path(__file__).parent / "static" / "styles.css"
if _css_path.exists():
    with open(_css_path) as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

# ─── Auto-Refresh ────────────────────────────────────────────────────────────

st_autorefresh(interval=1500, key="dashboard_refresh")

# ─── Session State Bootstrap ─────────────────────────────────────────────────

def _get_state() -> StateManager:
    """Get or create the singleton StateManager from session state."""
    if "state_manager" not in st.session_state:
        st.session_state["state_manager"] = StateManager()
    return st.session_state["state_manager"]


def _get_hub() -> NotificationHub:
    if "notification_hub" not in st.session_state:
        st.session_state["notification_hub"] = NotificationHub()
    return st.session_state["notification_hub"]


state = _get_state()
hub = _get_hub()
snap = state.snapshot()

# ─── Sidebar ─────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown(
        '<div class="legacynode-logo">⚡ LegacyNode</div>',
        unsafe_allow_html=True,
    )
    st.caption("Autonomous Coding Agent")
    st.divider()

    st.subheader("Agent Status")
    render_status_badge(snap["status"])

    st.metric("Queue Size", snap["queue_size"])
    st.metric("Steps Taken", snap["step_count"])

    unread = hub.unread_count()
    if unread:
        st.warning(f"🔔 {unread} unread notification{'s' if unread > 1 else ''}")

    st.divider()

    # Tunnel health indicator
    st.subheader("🌐 Tunnel")
    tunnel_url = os.environ.get("TUNNEL_URL", "")
    if tunnel_url:
        st.success(f"URL configured ✓")
        st.code(tunnel_url[:60] + ("..." if len(tunnel_url) > 60 else ""), language=None)
    else:
        st.error("TUNNEL_URL not set in .env")
        st.caption("Start a cloud runner notebook to get a URL.")

    st.divider()
    st.caption(f"LegacyNode v0.1.0 · {time.strftime('%H:%M:%S')}")

# ─── Header ──────────────────────────────────────────────────────────────────

st.markdown(
    """
    <div class="legacynode-header">
        <div>
            <div class="legacynode-logo">⚡ LegacyNode</div>
            <div style="color:#8B949E; font-size:0.9rem; margin-top:2px;">
                Autonomous Coding Agent &mdash; Cloud GPU Bridge
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ─── Main Tabs ───────────────────────────────────────────────────────────────

tab_control, tab_terminal, tab_notif, tab_history = st.tabs(
    ["🎛️ Control Center", "🖥️ Live Terminal", "🔔 Notifications", "📋 Task History"]
)

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1: Control Center
# ═══════════════════════════════════════════════════════════════════════════════

with tab_control:
    col_task, col_status = st.columns([2, 1])

    with col_task:
        st.subheader("Submit a Task")
        task_input = st.text_area(
            "Task Description",
            placeholder=(
                "Describe what you want the agent to build or fix.\n\n"
                "Example: 'Create a FastAPI endpoint at /health that returns JSON status. "
                "Add a pytest test for it. Run the tests and fix any failures.'"
            ),
            height=160,
            key="task_input",
        )

        col_run, col_stop = st.columns([1, 1])
        with col_run:
            run_disabled = (
                snap["status"] == AgentStatus.RUNNING.value
                or not task_input.strip()
                or not tunnel_url
            )
            if st.button(
                "▶ Run Task",
                disabled=run_disabled,
                use_container_width=True,
                key="btn_run",
            ):
                # Store task in session; main.py's background thread will pick it up
                st.session_state["pending_task"] = task_input.strip()
                st.success("Task queued! The agent will start shortly.")
                st.rerun()

        with col_stop:
            stop_disabled = snap["status"] != AgentStatus.RUNNING.value
            if st.button(
                "⏹ Stop Agent",
                disabled=stop_disabled,
                use_container_width=True,
                key="btn_stop",
            ):
                st.session_state["stop_requested"] = True
                st.warning("Stop signal sent to agent.")

    with col_status:
        st.subheader("Current Task")
        current = snap.get("current_task")
        if current:
            st.markdown(f"**ID:** `{current['id'][:8]}...`")
            st.markdown(f"**Status:** {current['status']}")
            st.markdown(f"**Steps:** {current['step_count']}")
            desc = current.get("description", "")
            st.markdown(f"**Description:**\n> {desc[:200]}")
            if current.get("started_at"):
                st.caption(f"Started: {current['started_at'][:19]} UTC")
        else:
            st.info("No active task. Submit one above.")

    st.divider()

    # Quick model / config info
    st.subheader("⚙️ Configuration")
    cfg_col1, cfg_col2, cfg_col3 = st.columns(3)
    with cfg_col1:
        st.metric("Model", os.environ.get("LLM_MODEL", "qwen2.5-coder:32b"))
    with cfg_col2:
        st.metric("Workspace", os.environ.get("WORKSPACE_ROOT", "."))
    with cfg_col3:
        st.metric("Max Iterations", "25")

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2: Live Terminal
# ═══════════════════════════════════════════════════════════════════════════════

with tab_terminal:
    st.subheader("🖥️ Agent Output Stream")
    st.caption("Real-time view of agent thoughts, tool calls, and terminal output.")

    output_lines = snap.get("live_output", [])
    if output_lines:
        # Color-code lines based on prefix
        formatted = []
        for line in output_lines[-100:]:
            if "[stderr]" in line:
                formatted.append(f'<span class="stderr">{line}</span>')
            elif "[stdout]" in line:
                formatted.append(f'<span class="stdout">{line}</span>')
            else:
                formatted.append(f'<span class="info">{line}</span>')

        html = "<br>".join(formatted)
        st.markdown(
            f'<div class="terminal-panel">{html}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="terminal-panel" style="color:#555;">No output yet. Submit a task to see live execution.</div>',
            unsafe_allow_html=True,
        )

    if st.button("🗑️ Clear Output", key="clear_output"):
        state.clear_execution_state()
        st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3: Notifications
# ═══════════════════════════════════════════════════════════════════════════════

with tab_notif:
    notif_header_col, notif_ack_col = st.columns([3, 1])
    with notif_header_col:
        st.subheader(f"🔔 Notifications ({hub.unread_count()} unread)")
    with notif_ack_col:
        if st.button("✓ Mark All Read", key="ack_all"):
            asyncio.run(hub.acknowledge_all())
            st.rerun()

    level_filter = st.multiselect(
        "Filter by Level",
        options=["INFO", "SUCCESS", "WARNING", "ERROR"],
        default=["INFO", "SUCCESS", "WARNING", "ERROR"],
        key="notif_filter",
    )

    # Get notifications synchronously (in-memory snapshot)
    all_notifs = list(reversed(list(hub._memory)))  # Most recent first

    filtered = [n for n in all_notifs if n.level.value in level_filter]
    if not filtered:
        st.info("No notifications match the current filter.")
    else:
        for notif in filtered[:50]:
            def make_ack_callback(nid):
                def _ack(nid=nid):
                    asyncio.run(hub.acknowledge(nid))
                    st.rerun()
                return _ack

            render_notification_card(
                notif.to_dict(),
                on_ack=make_ack_callback(notif.id) if not notif.acknowledged else None,
            )

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4: Task History
# ═══════════════════════════════════════════════════════════════════════════════

with tab_history:
    st.subheader("📋 Task History")
    st.caption("Records of all completed, failed, and cancelled tasks.")

    # Attempt to load from DB (if initialized)
    history: list[dict] = []
    try:
        history = asyncio.run(state.get_task_history(limit=50))
    except Exception:
        st.info("Task history not yet available. Start the agent via `main.py` first.")

    if history:
        import pandas as pd

        df = pd.DataFrame(history)
        # Select display columns
        cols = ["id", "status", "description", "step_count", "created_at", "completed_at"]
        df = df[[c for c in cols if c in df.columns]]
        df["id"] = df["id"].str[:8] + "..."
        df["description"] = df["description"].str[:60] + "..."
        df["created_at"] = df["created_at"].str[:19]
        df["completed_at"] = df["completed_at"].fillna("—").str[:19]

        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No task history yet.")
