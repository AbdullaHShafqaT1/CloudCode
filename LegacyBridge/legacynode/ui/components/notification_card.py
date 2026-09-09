"""
LegacyNode — Notification Card UI Component
"""

import streamlit as st
from datetime import datetime


LEVEL_CONFIG = {
    "INFO":    {"bg": "#1E3A5F", "border": "#3B82F6", "icon": "ℹ️"},
    "SUCCESS": {"bg": "#1A3A2A", "border": "#10B981", "icon": "✅"},
    "WARNING": {"bg": "#3A2D0F", "border": "#F59E0B", "icon": "⚠️"},
    "ERROR":   {"bg": "#3A1212", "border": "#EF4444", "icon": "❌"},
}


def render_notification_card(notification: dict, on_ack=None) -> None:
    """
    Render a single notification card.

    Args:
        notification: Dict with keys: id, level, title, message, timestamp, acknowledged.
        on_ack: Optional callback(notification_id) when Acknowledge is clicked.
    """
    level = notification.get("level", "INFO").upper()
    cfg = LEVEL_CONFIG.get(level, LEVEL_CONFIG["INFO"])
    icon = cfg["icon"]
    bg = cfg["bg"]
    border = cfg["border"]
    ack = notification.get("acknowledged", False)
    ts = notification.get("timestamp", "")
    if ts:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            ts = dt.strftime("%H:%M:%S UTC")
        except ValueError:
            pass

    opacity = "0.55" if ack else "1.0"
    st.markdown(
        f"""
        <div style="
            background:{bg};
            border-left: 4px solid {border};
            border-radius: 6px;
            padding: 10px 14px;
            margin-bottom: 8px;
            opacity: {opacity};
        ">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-weight:700; color:#F9FAFB;">{icon} {notification.get('title','')}</span>
                <span style="font-size:0.75rem; color:#9CA3AF;">{ts}</span>
            </div>
            <div style="color:#D1D5DB; font-size:0.875rem; margin-top:4px;">
                {notification.get('message','')[:300]}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not ack and on_ack:
        if st.button("✓ Acknowledge", key=f"ack_{notification['id']}"):
            on_ack(notification["id"])
