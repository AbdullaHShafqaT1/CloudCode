"""
LegacyNode — Status Badge UI Component
"""

import streamlit as st


STATUS_COLORS = {
    "IDLE": ("#6B7280", "⬛"),       # Gray
    "RUNNING": ("#3B82F6", "🔵"),   # Blue
    "PAUSED": ("#F59E0B", "🟡"),    # Amber
    "ERROR": ("#EF4444", "🔴"),     # Red
    "COMPLETED": ("#10B981", "🟢"), # Green
    "FAILED": ("#EF4444", "🔴"),
    "PENDING": ("#8B5CF6", "🟣"),   # Purple
    "CANCELLED": ("#6B7280", "⬛"),
}


def render_status_badge(status: str, label: str = "") -> None:
    """
    Render a colored status badge in the Streamlit UI.

    Args:
        status: One of IDLE, RUNNING, PAUSED, ERROR, COMPLETED, FAILED, PENDING.
        label: Optional label prefix.
    """
    color, icon = STATUS_COLORS.get(status.upper(), ("#6B7280", "⬜"))
    display = f"{icon} **{label + ' ' if label else ''}{status}**"
    st.markdown(
        f'<span style="color:{color}; font-size:1.1rem;">{display}</span>',
        unsafe_allow_html=True,
    )
