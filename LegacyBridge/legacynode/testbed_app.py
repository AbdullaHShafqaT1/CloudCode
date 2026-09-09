"""
LegacyBridge Interactive Test Dashboard  (testbed_app.py)
==========================================================
A premium Streamlit developer harness for testing and validating the
LegacyBridge interface.  Launch with:

    streamlit run legacynode/testbed_app.py

Panels
------
Sidebar  : Configuration, session lifecycle, live status metrics
Tab 1    : Interactive Runner  — task dispatch, progress stepper
Tab 2    : Telemetry & Logs    — live streaming output terminal
Tab 3    : Data Inspector      — JSON payload viewer, error traces
Tab 4    : Connection Tests    — health-check, ping, env-validate panel
Tab 5    : Task History        — paginated DB records (live mode)
"""

from __future__ import annotations

import sys
import time
import os
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ---- Path Bootstrap ---------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent
for _p in [str(_HERE), str(_REPO_ROOT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from bridge_adapter import BridgeAdapter, BridgeMode, SessionStatus  # noqa: E402

# =============================================================================
# Page Config
# =============================================================================

st.set_page_config(
    page_title="LegacyBridge Testbed",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =============================================================================
# Premium CSS
# =============================================================================

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap');

:root {
    --bg:           #080C14;
    --bg2:          #0E1420;
    --bg3:          #131A26;
    --card:         rgba(14,20,32,0.9);
    --border:       rgba(56,70,100,0.5);
    --border-glow:  rgba(99,179,237,0.25);
    --accent:       #63B3ED;
    --accent2:      #805AD5;
    --green:        #48BB78;
    --amber:        #F6AD55;
    --red:          #FC8181;
    --purple:       #9F7AEA;
    --text:         #E8F0FE;
    --muted:        #7B90B8;
    --font:         'Inter', -apple-system, sans-serif;
    --mono:         'JetBrains Mono', 'Courier New', monospace;
    --radius:       12px;
    --radius-sm:    8px;
}

html, body, [class*="css"] {
    font-family: var(--font) !important;
    background-color: var(--bg) !important;
    color: var(--text) !important;
}

/* === App background === */
.stApp {
    background: radial-gradient(ellipse at 20% 20%, #0D1829 0%, #080C14 50%, #080C14 100%);
    min-height: 100vh;
}

/* === Sidebar === */
section[data-testid="stSidebar"] {
    background: var(--bg2) !important;
    border-right: 1px solid var(--border) !important;
}
section[data-testid="stSidebar"] > div:first-child {
    padding-top: 1.5rem;
}

/* === Header Banner === */
.tb-header {
    background: linear-gradient(135deg, #0D1829 0%, #111C33 50%, #0D1829 100%);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 24px 32px;
    margin-bottom: 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    position: relative;
    overflow: hidden;
    box-shadow: 0 8px 32px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.05);
}
.tb-header::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: linear-gradient(90deg, transparent, #63B3ED, #805AD5, transparent);
}
.tb-title {
    font-size: 1.85rem;
    font-weight: 800;
    letter-spacing: -0.5px;
    background: linear-gradient(135deg, #63B3ED 0%, #B794F4 60%, #F6AD55 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}
.tb-sub {
    color: var(--muted);
    font-size: 0.875rem;
    margin-top: 4px;
    font-weight: 400;
}

/* === Status Pill === */
.pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
}
.pill-disconnected { background: rgba(255,255,255,0.05); color: var(--muted); border: 1px solid var(--border); }
.pill-ready        { background: rgba(72,187,120,0.12); color: var(--green); border: 1px solid rgba(72,187,120,0.3); }
.pill-running      { background: rgba(99,179,237,0.12); color: var(--accent); border: 1px solid rgba(99,179,237,0.3); animation: pulse 1.8s ease-in-out infinite; }
.pill-error        { background: rgba(252,129,129,0.12); color: var(--red); border: 1px solid rgba(252,129,129,0.3); }
.pill-initializing { background: rgba(246,173,85,0.12); color: var(--amber); border: 1px solid rgba(246,173,85,0.3); }
.pill-terminated   { background: rgba(159,122,234,0.10); color: var(--purple); border: 1px solid rgba(159,122,234,0.3); }

@keyframes pulse {
    0%,100% { opacity: 1; }
    50%      { opacity: 0.55; }
}

/* === Metric Cards === */
.metric-row {
    display: flex;
    gap: 12px;
    margin-bottom: 16px;
}
.metric-card {
    flex: 1;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 14px 16px;
    backdrop-filter: blur(8px);
    transition: border-color 0.2s, box-shadow 0.2s;
}
.metric-card:hover {
    border-color: var(--border-glow);
    box-shadow: 0 0 20px rgba(99,179,237,0.1);
}
.metric-label {
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: var(--muted);
    margin-bottom: 6px;
}
.metric-value {
    font-size: 1.4rem;
    font-weight: 700;
    color: var(--text);
    font-family: var(--mono);
}
.metric-delta {
    font-size: 0.75rem;
    color: var(--green);
    margin-top: 2px;
}

/* === Terminal === */
.terminal {
    background: #050810;
    border: 1px solid #1A2340;
    border-radius: var(--radius-sm);
    padding: 16px 18px;
    font-family: var(--mono);
    font-size: 0.81rem;
    line-height: 1.7;
    color: #8BA7D4;
    min-height: 340px;
    max-height: 480px;
    overflow-y: auto;
    box-shadow: inset 0 2px 12px rgba(0,0,0,0.6);
    position: relative;
}
.terminal::before {
    content: '● ● ●';
    position: absolute;
    top: 10px; left: 14px;
    font-size: 0.65rem;
    letter-spacing: 4px;
    color: #2A3A5A;
}
.terminal-body { margin-top: 20px; }
.t-task    { color: #63B3ED; }
.t-ok      { color: #68D391; }
.t-error   { color: #FC8181; }
.t-warn    { color: #F6AD55; }
.t-tool    { color: #B794F4; }
.t-stdout  { color: #A3E4A6; }
.t-stderr  { color: #FCA5A5; }
.t-agent   { color: #93C5FD; }
.t-info    { color: #8BA7D4; }
.t-ts      { color: #3A5070; font-size: 0.75rem; }

/* === Stepper === */
.stepper { display: flex; align-items: center; gap: 0; margin: 16px 0; }
.step {
    display: flex;
    flex-direction: column;
    align-items: center;
    flex: 1;
}
.step-dot {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    border: 2px solid var(--border);
    background: var(--bg3);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.7rem;
    font-weight: 700;
    color: var(--muted);
    position: relative;
    z-index: 1;
    transition: all 0.3s;
}
.step-dot.done    { background: var(--green); border-color: var(--green); color: #050810; }
.step-dot.active  { background: var(--accent); border-color: var(--accent); color: #050810; box-shadow: 0 0 12px rgba(99,179,237,0.5); }
.step-label { font-size: 0.68rem; color: var(--muted); margin-top: 5px; text-align: center; }
.step-line {
    flex: 1;
    height: 2px;
    background: var(--border);
    margin: 0 -1px;
    margin-top: -30px;
    z-index: 0;
}
.step-line.done { background: var(--green); }

/* === Buttons === */
.stButton > button {
    background: linear-gradient(135deg, #2B6CB0, #553C9A) !important;
    color: white !important;
    border: none !important;
    border-radius: var(--radius-sm) !important;
    font-weight: 600 !important;
    font-family: var(--font) !important;
    padding: 10px 20px !important;
    transition: all 0.2s !important;
    box-shadow: 0 2px 10px rgba(43,108,176,0.3) !important;
    letter-spacing: 0.3px !important;
}
.stButton > button:hover {
    background: linear-gradient(135deg, #3182CE, #6B46C1) !important;
    transform: translateY(-1px) !important;
    box-shadow: 0 4px 20px rgba(43,108,176,0.45) !important;
}
.stButton > button:active { transform: translateY(0) !important; }

/* === Tabs === */
.stTabs [data-baseweb="tab-list"] {
    background: transparent !important;
    border-bottom: 1px solid var(--border) !important;
    gap: 2px !important;
}
.stTabs [data-baseweb="tab"] {
    background: transparent !important;
    color: var(--muted) !important;
    border-radius: 8px 8px 0 0 !important;
    padding: 9px 18px !important;
    font-weight: 500 !important;
    font-size: 0.88rem !important;
    transition: all 0.15s !important;
}
.stTabs [aria-selected="true"] {
    color: var(--text) !important;
    border-bottom: 2px solid var(--accent) !important;
    background: rgba(99,179,237,0.06) !important;
}

/* === Inputs === */
.stTextInput > div > div > input,
.stTextArea > div > div > textarea,
.stNumberInput > div > div > input,
.stSelectbox > div > div > div {
    background: var(--bg3) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-sm) !important;
    color: var(--text) !important;
    font-family: var(--font) !important;
}

/* === JSON Viewer === */
.stJson { background: var(--bg3) !important; border-radius: var(--radius-sm) !important; }

/* === DataFrames === */
.stDataFrame {
    background: var(--bg2) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-sm) !important;
}

/* === Divider === */
hr { border-color: var(--border) !important; }

/* === Alerts === */
.stAlert {
    border-radius: var(--radius-sm) !important;
    border-left-width: 3px !important;
}

/* === Scrollbar === */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #1E2D47; border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: var(--accent); }

/* === Sidebar logo === */
.tb-logo {
    font-size: 1.4rem;
    font-weight: 800;
    background: linear-gradient(135deg, #63B3ED, #B794F4);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    margin-bottom: 4px;
}

/* === Connection dot === */
.conn-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    display: inline-block;
    margin-right: 6px;
    vertical-align: middle;
}
.conn-dot.connected    { background: var(--green); box-shadow: 0 0 6px var(--green); }
.conn-dot.disconnected { background: var(--muted); }
.conn-dot.running      { background: var(--accent); box-shadow: 0 0 6px var(--accent); animation: pulse 1.8s infinite; }
.conn-dot.error        { background: var(--red); }

/* === Test result badges === */
.test-pass { color: var(--green); font-weight: 700; }
.test-fail { color: var(--red);   font-weight: 700; }

/* === Info grid === */
.info-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    margin-top: 12px;
}
.info-item {
    background: var(--bg3);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 10px 14px;
}
.info-key   { font-size: 0.7rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.6px; }
.info-val   { font-size: 0.88rem; color: var(--text); font-family: var(--mono); margin-top: 3px; word-break: break-all; }

/* === Error trace box === */
.error-trace {
    background: rgba(252,129,129,0.05);
    border: 1px solid rgba(252,129,129,0.2);
    border-radius: var(--radius-sm);
    padding: 14px 16px;
    font-family: var(--mono);
    font-size: 0.79rem;
    color: var(--red);
    white-space: pre-wrap;
    max-height: 320px;
    overflow-y: auto;
}

</style>
"""

st.markdown(_CSS, unsafe_allow_html=True)

# =============================================================================
# Session-state helpers
# =============================================================================

def _init_ss(key: str, default):
    if key not in st.session_state:
        st.session_state[key] = default

_init_ss("last_result", None)
_init_ss("task_result", None)
_init_ss("run_phase", 0)     # 0=idle, 1=dispatching, 2=done, 3=error
_init_ss("conn_test_results", {})

# =============================================================================
# Helpers
# =============================================================================

_STATUS_CLASS = {
    "DISCONNECTED": "pill-disconnected",
    "INITIALIZING": "pill-initializing",
    "READY":        "pill-ready",
    "RUNNING":      "pill-running",
    "ERROR":        "pill-error",
    "TERMINATED":   "pill-terminated",
}
_STATUS_ICON = {
    "DISCONNECTED": "○",
    "INITIALIZING": "◌",
    "READY":        "✓",
    "RUNNING":      "▶",
    "ERROR":        "✕",
    "TERMINATED":   "■",
}


def _pill(status: str) -> str:
    cls  = _STATUS_CLASS.get(status, "pill-disconnected")
    icon = _STATUS_ICON.get(status, "○")
    return f'<span class="pill {cls}">{icon} {status}</span>'


def _color_log_line(raw: str) -> str:
    """Return the HTML-class name for a log line based on its prefix content."""
    r = raw.lower()
    if "[stderr]" in r or "error" in r:
        return "t-error"
    if "[stdout]" in r:
        return "t-stdout"
    if "task_complete" in r or "ok " in r or "approved" in r:
        return "t-ok"
    if "warn" in r or "amber" in r:
        return "t-warn"
    if "[tools]" in r or "calling:" in r:
        return "t-tool"
    if "[coderagent]" in r or "[criticagent]" in r or "[agent]" in r:
        return "t-agent"
    if "[task]" in r:
        return "t-task"
    return "t-info"


def _render_terminal(lines: list[str], max_lines: int = 200) -> None:
    if not lines:
        st.markdown(
            '<div class="terminal"><div class="terminal-body" style="color:#2A3A5A;">'
            'No output yet — initialize a session and dispatch a task.</div></div>',
            unsafe_allow_html=True,
        )
        return
    parts = []
    for raw in lines[-max_lines:]:
        cls = _color_log_line(raw)
        # Separate timestamp portion from rest
        safe = raw.replace("<", "&lt;").replace(">", "&gt;")
        if safe.startswith("[") and "]" in safe[:12]:
            ts_end = safe.index("]") + 1
            ts_part   = safe[:ts_end]
            rest_part = safe[ts_end:]
            parts.append(
                f'<span class="t-ts">{ts_part}</span>'
                f'<span class="{cls}">{rest_part}</span>'
            )
        else:
            parts.append(f'<span class="{cls}">{safe}</span>')
    body = "<br>".join(parts)
    st.markdown(
        f'<div class="terminal"><div class="terminal-body">{body}</div></div>',
        unsafe_allow_html=True,
    )


def _stepper(phase: int) -> None:
    """Render a 4-step progress indicator."""
    steps = ["Queued", "Connecting", "Running", "Complete"]
    dots  = []
    lines = []
    for i, label in enumerate(steps):
        if i < phase:
            cls = "done"
            ico = "✓"
        elif i == phase:
            cls = "active"
            ico = str(i + 1)
        else:
            cls = ""
            ico = str(i + 1)
        dots.append(
            f'<div class="step">'
            f'<div class="step-dot {cls}">{ico}</div>'
            f'<div class="step-label">{label}</div>'
            f'</div>'
        )
        if i < len(steps) - 1:
            line_cls = "done" if i < phase else ""
            lines.append(f'<div class="step-line {line_cls}"></div>')

    combined = ""
    for i, dot in enumerate(dots):
        combined += dot
        if i < len(lines):
            combined += lines[i]

    st.markdown(f'<div class="stepper">{combined}</div>', unsafe_allow_html=True)


def _result_json_view(result, label: str = "Payload") -> None:
    if result is None:
        st.info("No payload yet.")
        return
    d = result.to_dict() if hasattr(result, "to_dict") else result
    if d.get("data"):
        st.json(d["data"])
    if d.get("error"):
        st.markdown(
            f'<div class="error-trace">{d["error"]}</div>',
            unsafe_allow_html=True,
        )
        if d.get("traceback"):
            with st.expander("Full Traceback"):
                st.markdown(
                    f'<div class="error-trace">{d["traceback"]}</div>',
                    unsafe_allow_html=True,
                )


# =============================================================================
# Sidebar — Configuration & Session Management
# =============================================================================

with st.sidebar:
    st.markdown('<div class="tb-logo">🔬 LegacyBridge</div>', unsafe_allow_html=True)
    st.caption("Interactive Test Dashboard")
    st.markdown("---")

    # ---- Mode ----------------------------------------------------------------
    st.markdown("**Runtime Mode**")
    mode_val = st.radio(
        "mode_radio",
        options=[m.value for m in BridgeMode],
        index=0,
        label_visibility="collapsed",
        key="cfg_mode",
    )
    selected_mode = BridgeMode(mode_val)

    st.markdown("---")

    # ---- Workspace -----------------------------------------------------------
    st.markdown("**Workspace Path**")
    workspace = st.text_input(
        "workspace_input",
        value=str(Path(__file__).resolve().parent.parent),
        label_visibility="collapsed",
        key="cfg_workspace",
        placeholder="/path/to/workspace",
    )

    # ---- Tunnel URL (Remote only) -------------------------------------------
    tunnel_url = ""
    if selected_mode == BridgeMode.REMOTE:
        st.markdown("**Tunnel URL**")
        tunnel_url = st.text_input(
            "tunnel_input",
            value=os.environ.get("TUNNEL_URL", ""),
            label_visibility="collapsed",
            key="cfg_tunnel",
            placeholder="https://xxx.trycloudflare.com",
        )

    # ---- LLM Model -----------------------------------------------------------
    llm_model = st.text_input(
        "LLM Model",
        value="qwen2.5-coder:32b",
        key="cfg_model",
    )

    st.markdown("---")

    # ---- Execution Parameters ------------------------------------------------
    st.markdown("**Execution Parameters**")
    col_t, col_i = st.columns(2)
    with col_t:
        timeout_val = st.number_input("Timeout (s)", min_value=10, max_value=600,
                                       value=180, step=10, key="cfg_timeout")
    with col_i:
        max_iter = st.number_input("Max Iters", min_value=5, max_value=100,
                                    value=25, step=5, key="cfg_max_iter")

    st.markdown("---")

    # ---- Build adapter -------------------------------------------------------
    adapter = BridgeAdapter.from_session(
        mode=selected_mode,
        workspace_root=workspace,
        tunnel_url=tunnel_url,
        llm_model=llm_model,
        timeout=int(timeout_val),
        max_iterations=int(max_iter),
    )

    # ---- Session Controls ----------------------------------------------------
    st.markdown("**Session Controls**")
    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        if st.button("▶ Init", key="btn_init", use_container_width=True):
            with st.spinner("Initializing…"):
                r = adapter.initialize_session()
            st.session_state["last_result"] = r
            if r.ok:
                st.success("Session ready")
            else:
                st.error(r.error or "Init failed")

    with btn_col2:
        if st.button("↺ Reset", key="btn_reset", use_container_width=True):
            r = adapter.reset_state()
            st.session_state["last_result"] = r
            st.session_state["task_result"] = None
            st.session_state["run_phase"] = 0
            st.rerun()

    if st.button("■ Terminate", key="btn_terminate", use_container_width=True):
        r = adapter.terminate_session()
        st.session_state["last_result"] = r
        st.session_state["task_result"] = None
        st.session_state["run_phase"] = 0
        st.rerun()

    st.markdown("---")

    # ---- Live Status Metrics -------------------------------------------------
    snap = adapter.get_snapshot()
    status_val = snap.get("session_status", "DISCONNECTED")

    st.markdown(
        f'<div style="margin-bottom:10px;">{_pill(status_val)}</div>',
        unsafe_allow_html=True,
    )

    sid = snap.get("session_id") or "—"
    st.markdown(
        f'<div style="font-size:0.78rem; color: var(--muted); font-family: var(--mono);">'
        f'ID: {sid}</div>',
        unsafe_allow_html=True,
    )

    c1, c2 = st.columns(2)
    with c1:
        st.metric("Log Lines", snap.get("log_line_count", 0))
    with c2:
        task_running = snap.get("task_running", False)
        st.metric("Task", "RUNNING" if task_running else "IDLE")

    st.markdown("---")
    st.caption(f"LegacyBridge Testbed · {datetime.now().strftime('%H:%M:%S')}")

# =============================================================================
# Header Banner
# =============================================================================

st.markdown(
    f"""
    <div class="tb-header">
        <div>
            <div class="tb-title">🔬 LegacyBridge Testbed</div>
            <div class="tb-sub">Interactive Developer Dashboard &mdash; Test, Validate &amp; Inspect</div>
        </div>
        <div style="text-align:right;">
            {_pill(status_val)}
            <div style="font-size:0.72rem; color:var(--muted); margin-top:6px; font-family:var(--mono);">
                Mode: {snap.get("mode","—")} &nbsp;·&nbsp; Session: {sid}
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# =============================================================================
# Main Tabs
# =============================================================================

(
    tab_runner,
    tab_telemetry,
    tab_inspect,
    tab_conn,
    tab_history,
) = st.tabs([
    "🚀 Interactive Runner",
    "📡 Telemetry & Logs",
    "🔍 Data Inspector",
    "🩺 Connection Tests",
    "📋 Task History",
])


# ===========================================================================
# TAB 1 — Interactive Runner
# ===========================================================================

with tab_runner:
    st.markdown("### Task Dispatcher")
    st.caption("Submit a goal or task to LegacyBridge and monitor execution in real time.")

    col_form, col_status = st.columns([3, 2], gap="large")

    with col_form:
        task_input = st.text_area(
            "Task Description / Goal",
            height=150,
            key="runner_task",
            placeholder=(
                'Describe what you want the agent to do.\n\n'
                'Examples:\n'
                '  • "Run AST analysis on /src and list all class definitions"\n'
                '  • "Create a FastAPI /health endpoint with pytest tests"\n'
                '  • "Deploy training script to Kaggle notebook"'
            ),
        )

        exp_col1, exp_col2, exp_col3 = st.columns(3)
        with exp_col1:
            run_timeout = st.number_input(
                "Timeout (s)", min_value=10, max_value=600, value=int(timeout_val),
                step=10, key="run_timeout"
            )
        with exp_col2:
            run_max_rounds = st.number_input(
                "Max Rounds", min_value=1, max_value=100, value=int(max_iter),
                step=5, key="run_rounds"
            )
        with exp_col3:
            verbose_mode = st.toggle("Verbose", value=False, key="run_verbose")

        dispatch_disabled = (
            not task_input.strip()
            or not adapter.is_ready
            or adapter._task_running
        )

        btn_dispatch, btn_clear = st.columns([2, 1])
        with btn_dispatch:
            if st.button(
                "🚀 Dispatch to LegacyBridge",
                key="btn_dispatch",
                disabled=dispatch_disabled,
                use_container_width=True,
            ):
                if not adapter.is_ready:
                    st.warning("Initialize a session first (sidebar).")
                else:
                    st.session_state["run_phase"] = 1
                    with st.spinner("Dispatching task…"):
                        result = adapter.submit_task(
                            description=task_input.strip(),
                            timeout_override=int(run_timeout),
                            verbose=verbose_mode,
                        )
                    st.session_state["task_result"] = result
                    st.session_state["run_phase"] = 3 if not result.ok else 2
                    st.session_state["last_result"] = result
                    st.rerun()

        with btn_clear:
            if st.button("🗑 Clear", key="btn_clear_task", use_container_width=True):
                st.session_state["task_result"] = None
                st.session_state["run_phase"] = 0
                adapter.reset_state()
                st.rerun()

    with col_status:
        st.markdown("**Execution Progress**")
        phase = st.session_state.get("run_phase", 0)
        _stepper(phase)

        task_result = st.session_state.get("task_result")
        if task_result is not None:
            if task_result.ok:
                data = task_result.data or {}
                st.success(f"Completed in **{task_result.latency_ms:.0f} ms**")
                st.markdown(
                    f"""
                    <div class="info-grid">
                        <div class="info-item">
                            <div class="info-key">Task ID</div>
                            <div class="info-val">{data.get("task_id","—")}</div>
                        </div>
                        <div class="info-item">
                            <div class="info-key">Status</div>
                            <div class="info-val">{data.get("status","—")}</div>
                        </div>
                        <div class="info-item">
                            <div class="info-key">Steps</div>
                            <div class="info-val">{data.get("steps","—")}</div>
                        </div>
                        <div class="info-item">
                            <div class="info-key">Mode</div>
                            <div class="info-val">{data.get("mode","—")}</div>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if data.get("summary"):
                    st.markdown("**Summary**")
                    st.markdown(
                        f'<div style="font-size:0.85rem;color:var(--muted);'
                        f'border-left:3px solid var(--green);padding-left:10px;">'
                        f'{data["summary"][:400]}</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.error(f"Task failed: {task_result.error}")
        elif not adapter.is_ready:
            st.info("Initialize a session in the sidebar to enable task dispatch.")
        else:
            st.markdown(
                '<div style="color:var(--muted);font-size:0.85rem;">'
                'No task running. Enter a description and click Dispatch.</div>',
                unsafe_allow_html=True,
            )

    # Quick example tasks
    st.markdown("---")
    st.markdown("**Quick Examples**")
    ex_cols = st.columns(3)
    examples = [
        ("📄 AST Analysis", "Run AST analysis on the legacynode/core directory and list all class and function definitions."),
        ("🧪 Health Probe", "Check the LLM endpoint health and list all available models. Report latency."),
        ("🗂 Directory Scan", "Scan the workspace directory tree (max depth 3) and return a structured JSON summary."),
    ]
    for col, (label, desc) in zip(ex_cols, examples):
        with col:
            if st.button(label, key=f"ex_{label}", use_container_width=True):
                st.session_state["runner_task"] = desc
                st.rerun()


# ===========================================================================
# TAB 2 — Telemetry & Live Logs
# ===========================================================================

with tab_telemetry:
    st.markdown("### Live Telemetry Stream")
    st.caption(
        "Real-time view of agent thoughts, tool invocations, stdout/stderr, and system events."
    )

    ctl_col1, ctl_col2, ctl_col3 = st.columns([1, 1, 3])
    with ctl_col1:
        max_lines = st.select_slider(
            "Max lines", options=[50, 100, 200, 500], value=200, key="tel_max"
        )
    with ctl_col2:
        if st.button("🗑 Clear Logs", key="btn_clear_logs"):
            adapter.reset_state()
            st.rerun()
    with ctl_col3:
        st.markdown("")  # spacer

    lines = adapter.get_live_output()
    _render_terminal(lines, max_lines=int(max_lines))

    st.markdown("---")

    # Agent conversation view
    st.markdown("**Agent Message Feed**")
    snap2 = adapter.get_snapshot()
    if adapter._state_manager and adapter.mode != BridgeMode.MOCK:
        steps = adapter._state_manager.get_execution_steps()
        if steps:
            for step in steps[-10:]:
                with st.chat_message("assistant"):
                    st.markdown(f"**Step {step.index}** — {step.thought[:300]}")
                    if step.action:
                        st.code(step.action, language="bash")
        else:
            st.info("No execution steps recorded yet.")
    else:
        if lines:
            # Render last 5 log lines as chat-style messages
            agent_lines = [l for l in lines if "[coderagent]" in l.lower() or "[criticagent]" in l.lower()]
            if agent_lines:
                for line in agent_lines[-5:]:
                    with st.chat_message("assistant"):
                        st.markdown(line)
            else:
                st.info("No agent messages yet. Dispatch a task to see agent output.")
        else:
            st.info("No output yet — dispatch a task first.")


# ===========================================================================
# TAB 3 — Data Inspector
# ===========================================================================

with tab_inspect:
    st.markdown("### Data Inspector")
    st.caption("Inspect raw payloads returned by LegacyBridge and dissect error traces.")

    col_payload, col_snap = st.columns([1, 1], gap="large")

    with col_payload:
        st.markdown("**Last API Payload**")
        last = st.session_state.get("last_result")
        _result_json_view(last, "Last Payload")

    with col_snap:
        st.markdown("**Adapter Snapshot**")
        snap3 = adapter.get_snapshot()
        st.json(snap3)

    st.markdown("---")

    # Task result deep-view
    st.markdown("**Task Result Details**")
    tr = st.session_state.get("task_result")
    if tr is not None:
        d = tr.to_dict()
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown(
                f"""
                <div class="info-grid">
                    <div class="info-item">
                        <div class="info-key">Success</div>
                        <div class="info-val {'test-pass' if tr.ok else 'test-fail'}"
                             style="color:{'#68D391' if tr.ok else '#FC8181'}">
                            {'PASS' if tr.ok else 'FAIL'}
                        </div>
                    </div>
                    <div class="info-item">
                        <div class="info-key">Latency</div>
                        <div class="info-val">{tr.latency_ms:.1f} ms</div>
                    </div>
                    <div class="info-item">
                        <div class="info-key">Timestamp</div>
                        <div class="info-val">{tr.timestamp[:19]}</div>
                    </div>
                    <div class="info-item">
                        <div class="info-key">Error</div>
                        <div class="info-val">{tr.error or "None"}</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with col_b:
            if tr.data:
                st.json(tr.data)

        if tr.traceback:
            with st.expander("Full Python Traceback", expanded=True):
                st.markdown(
                    f'<div class="error-trace">{tr.traceback}</div>',
                    unsafe_allow_html=True,
                )
    else:
        st.info("No task result yet. Run a task from the Interactive Runner tab.")


# ===========================================================================
# TAB 4 — Connection Tests
# ===========================================================================

with tab_conn:
    st.markdown("### Connection Tests & Diagnostics")
    st.caption(
        "Validate connectivity, probe LLM endpoint health, and audit the runtime environment."
    )

    tests = [
        ("🩺 Health Check",   "health_check",  "Probe LLM endpoint availability and list models."),
        ("🏓 Ping",           "ping",          "Measure round-trip latency to the LLM endpoint."),
        ("🔍 Env Validate",   "env_check",     "Audit Python packages, env vars, and workspace."),
    ]

    test_cols = st.columns(len(tests))
    for col, (label, method, desc) in zip(test_cols, tests):
        with col:
            st.markdown(f"**{label}**")
            st.caption(desc)
            if st.button(f"Run {label}", key=f"btn_{method}", use_container_width=True):
                with st.spinner("Running…"):
                    fn = getattr(adapter, method)
                    r  = fn()
                st.session_state["conn_test_results"][method] = r
                st.session_state["last_result"] = r
                st.rerun()

            prev = st.session_state["conn_test_results"].get(method)
            if prev is not None:
                if prev.ok:
                    st.markdown('<span class="test-pass">● PASS</span>', unsafe_allow_html=True)
                    st.caption(f"{prev.latency_ms:.1f} ms")
                else:
                    st.markdown('<span class="test-fail">● FAIL</span>', unsafe_allow_html=True)
                    st.caption(prev.error or "Error")

    st.markdown("---")

    # ---- Run All Tests -------------------------------------------------------
    all_col, _ = st.columns([1, 3])
    with all_col:
        if st.button("▶ Run All Tests", key="btn_all_tests", use_container_width=True):
            results = {}
            bar = st.progress(0, text="Running tests…")
            for idx, (label, method, _) in enumerate(tests):
                fn = getattr(adapter, method)
                with st.spinner(f"Running {label}…"):
                    r = fn()
                results[method] = r
                bar.progress((idx + 1) / len(tests), text=f"Ran {label}")
                time.sleep(0.1)
            bar.empty()
            st.session_state["conn_test_results"].update(results)
            st.session_state["last_result"] = list(results.values())[-1]
            st.rerun()

    # ---- Results Table -------------------------------------------------------
    st.markdown("**Test Results**")
    ctr = st.session_state.get("conn_test_results", {})
    if ctr:
        rows_html = ""
        for method, r in ctr.items():
            status_html = (
                '<span class="test-pass">PASS</span>'
                if r.ok
                else '<span class="test-fail">FAIL</span>'
            )
            rows_html += (
                f"<tr>"
                f"<td style='padding:8px 12px;color:var(--text)'>{method}</td>"
                f"<td style='padding:8px 12px;'>{status_html}</td>"
                f"<td style='padding:8px 12px;color:var(--muted);font-family:var(--mono)'>{r.latency_ms:.1f} ms</td>"
                f"<td style='padding:8px 12px;color:var(--muted);font-size:0.8rem'>{r.timestamp[:19]}</td>"
                f"<td style='padding:8px 12px;color:var(--red);font-size:0.8rem'>{r.error or ''}</td>"
                f"</tr>"
            )
        st.markdown(
            f"""
            <table style="width:100%;border-collapse:collapse;background:var(--bg3);
                          border:1px solid var(--border);border-radius:8px;overflow:hidden;">
              <thead>
                <tr style="border-bottom:1px solid var(--border);">
                  <th style="padding:8px 12px;text-align:left;color:var(--muted);
                             font-size:0.72rem;text-transform:uppercase;letter-spacing:0.6px;">Test</th>
                  <th style="padding:8px 12px;text-align:left;color:var(--muted);
                             font-size:0.72rem;text-transform:uppercase;letter-spacing:0.6px;">Status</th>
                  <th style="padding:8px 12px;text-align:left;color:var(--muted);
                             font-size:0.72rem;text-transform:uppercase;letter-spacing:0.6px;">Latency</th>
                  <th style="padding:8px 12px;text-align:left;color:var(--muted);
                             font-size:0.72rem;text-transform:uppercase;letter-spacing:0.6px;">Timestamp</th>
                  <th style="padding:8px 12px;text-align:left;color:var(--muted);
                             font-size:0.72rem;text-transform:uppercase;letter-spacing:0.6px;">Error</th>
                </tr>
              </thead>
              <tbody>{rows_html}</tbody>
            </table>
            """,
            unsafe_allow_html=True,
        )

        # Detailed payload view for last result
        st.markdown("---")
        st.markdown("**Last Test Payload**")
        last_r = st.session_state.get("last_result")
        _result_json_view(last_r)
    else:
        st.info("Run individual tests or 'Run All Tests' to see results here.")


# ===========================================================================
# TAB 5 — Task History
# ===========================================================================

with tab_history:
    st.markdown("### Task History")
    st.caption("Paginated log of all completed, failed, and cancelled tasks (live mode only).")

    hist_col1, hist_col2 = st.columns([1, 3])
    with hist_col1:
        hist_limit = st.number_input(
            "Records", min_value=5, max_value=200, value=50, step=5, key="hist_limit"
        )
    with hist_col2:
        if st.button("↺ Refresh", key="btn_hist_refresh"):
            st.rerun()

    history = adapter.get_task_history(limit=int(hist_limit))
    if history:
        import pandas as pd

        df = pd.DataFrame(history)
        cols_show = ["id", "status", "description", "step_count", "created_at", "completed_at"]
        df = df[[c for c in cols_show if c in df.columns]]
        if "id" in df.columns:
            df["id"] = df["id"].str[:8] + "…"
        if "description" in df.columns:
            df["description"] = df["description"].str[:70] + "…"
        if "created_at" in df.columns:
            df["created_at"] = df["created_at"].str[:19]
        if "completed_at" in df.columns:
            df["completed_at"] = df["completed_at"].fillna("—").str[:19]

        st.dataframe(df, use_container_width=True, hide_index=True)

        # Summary metrics
        total = len(history)
        completed = sum(1 for t in history if t.get("status") == "COMPLETED")
        failed    = sum(1 for t in history if t.get("status") == "FAILED")
        st.markdown(
            f"""
            <div class="metric-row" style="margin-top:16px;">
                <div class="metric-card">
                    <div class="metric-label">Total</div>
                    <div class="metric-value">{total}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Completed</div>
                    <div class="metric-value" style="color:var(--green)">{completed}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Failed</div>
                    <div class="metric-value" style="color:var(--red)">{failed}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Success Rate</div>
                    <div class="metric-value">{(completed/total*100):.0f}%</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        if adapter.mode == BridgeMode.MOCK:
            st.info(
                "Task history is only available in **Local Runtime** or **Remote Link** mode, "
                "where tasks are persisted to the SQLite DB. Switch modes and initialize a "
                "session to see history."
            )
        else:
            st.info("No task history yet. Submit tasks to populate this view.")
