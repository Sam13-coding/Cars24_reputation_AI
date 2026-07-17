"""Dashboard: Streamlit UI for the Cars24 reputation report.

Run with: streamlit run dashboard.py
"""

from __future__ import annotations

import html
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import json as _json
import pandas as pd
import streamlit as st

from claude_agent import GeminiUnavailableError, analyze_posts
from collector import collect_posts
from report_generator import build_report, save_report
from risk_agent import PRIORITY_THRESHOLDS, assess_posts

OUTPUT_DIR = Path("output")
REPORT_PATH = OUTPUT_DIR / "report.json"
RESULTS_PATH = OUTPUT_DIR / "results.csv"

# --- Cars24-inspired dark theme -------------------------------------------------
PAGE_BG = "#111827"
SURFACE = "#1F2937"
SURFACE_ALT = "#161F2E"
BRAND_BLUE = "#2563EB"
BRAND_ORANGE = "#F97316"
STATUS_GREEN = "#22C55E"
STATUS_YELLOW = "#EAB308"
STATUS_RED = "#EF4444"
TEXT_PRIMARY = "#FFFFFF"
TEXT_SECONDARY = "#9CA3AF"
BORDER = "rgba(255, 255, 255, 0.08)"

PRIORITY_ORDER = ["Low", "Medium", "High", "Critical"]
STATUS_COLORS = {
    "Low": STATUS_GREEN,
    "Medium": STATUS_YELLOW,
    "High": BRAND_ORANGE,
    "Critical": STATUS_RED,
}


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    """Convert a #RRGGBB color to an rgba() string, for tinted badge/card backgrounds."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha})"


def load_report() -> dict[str, Any]:
    """Load the executive report written by report_generator.py."""
    return _json.loads(REPORT_PATH.read_text(encoding="utf-8"))


def load_results() -> pd.DataFrame:
    """Load the flat, risk-scored discussion table written by report_generator.py."""
    return pd.read_csv(RESULTS_PATH)


def _spacer(height_px: int = 28) -> None:
    """Fixed-height vertical gap, for consistent spacing between major sections."""
    st.markdown(f'<div style="height:{height_px}px;"></div>', unsafe_allow_html=True)


def _inject_theme() -> None:
    """Apply the professional dark SaaS theme: colors, cards, badges, table, micro-interactions."""
    st.markdown(
        f"""
        <style>
        html, body, .stApp {{
            background-color: {PAGE_BG};
            color: {TEXT_PRIMARY};
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }}
        [data-testid="stMainBlockContainer"] {{
            max-width: 1400px;
            padding-top: 2.25rem;
            padding-bottom: 3rem;
        }}
        h1, h2, h3, h4, p, span, label, div {{
            color: {TEXT_PRIMARY};
        }}
        h2, h3 {{
            font-weight: 700;
            letter-spacing: -0.01em;
            font-size: 1.4rem;
            margin-bottom: 0.9rem;
        }}

        @keyframes c24FadeIn {{
            from {{ opacity: 0; transform: translateY(8px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}

        /* Primary CTA: large, blue-to-orange gradient, soft shadow, smooth hover */
        .stButton > button {{
            background: linear-gradient(90deg, {BRAND_BLUE} 0%, {BRAND_ORANGE} 100%);
            color: {TEXT_PRIMARY};
            border: none;
            border-radius: 10px;
            font-weight: 700;
            font-size: 1rem;
            padding: 0.85rem 1.5rem;
            box-shadow: 0 4px 14px rgba(37, 99, 235, 0.35);
            transition: transform 0.25s cubic-bezier(0.2, 0.8, 0.2, 1),
                        box-shadow 0.25s cubic-bezier(0.2, 0.8, 0.2, 1),
                        filter 0.25s ease;
        }}
        .stButton > button:hover {{
            filter: brightness(1.1);
            transform: translateY(-2px) scale(1.01);
            box-shadow: 0 10px 26px rgba(249, 115, 22, 0.4);
        }}
        .stButton > button:active {{
            transform: translateY(0) scale(1);
        }}

        /* Generic card used for KPIs / summary / highlight blocks */
        .c24-card {{
            background: linear-gradient(160deg, {SURFACE} 0%, {SURFACE_ALT} 100%);
            border: 1px solid {BORDER};
            border-radius: 18px;
            padding: 1.5rem 1.75rem;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.35);
            transition: transform 0.25s cubic-bezier(0.2, 0.8, 0.2, 1),
                        box-shadow 0.25s cubic-bezier(0.2, 0.8, 0.2, 1),
                        border-color 0.25s ease;
            animation: c24FadeIn 0.4s ease-out;
        }}
        .c24-card:hover {{
            transform: translateY(-4px);
            box-shadow: 0 14px 32px rgba(0, 0, 0, 0.45);
            border-color: rgba(255, 255, 255, 0.16);
        }}

        /* KPI row cards: fixed height so every card lines up perfectly */
        .c24-kpi-card {{
            height: 176px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }}
        .c24-kpi-icon {{
            font-size: 2.1rem;
            line-height: 1;
        }}
        .c24-kpi-label {{
            color: {TEXT_SECONDARY};
            font-size: 0.8rem;
            font-weight: 500;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        .c24-kpi-value {{
            color: {TEXT_PRIMARY};
            font-size: 2.5rem;
            font-weight: 800;
            letter-spacing: -0.02em;
            font-variant-numeric: tabular-nums;
        }}

        /* Reddit link rendered as a real button */
        .c24-link-btn {{
            display: inline-block;
            background: linear-gradient(90deg, {BRAND_BLUE} 0%, {BRAND_ORANGE} 100%);
            color: {TEXT_PRIMARY} !important;
            font-weight: 700;
            font-size: 0.9rem;
            padding: 0.6rem 1.35rem;
            border-radius: 9px;
            text-decoration: none !important;
            box-shadow: 0 2px 10px rgba(37, 99, 235, 0.3);
            transition: transform 0.2s cubic-bezier(0.2, 0.8, 0.2, 1), box-shadow 0.2s ease;
        }}
        .c24-link-btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(249, 115, 22, 0.4);
        }}

        /* Custom discussions table */
        .c24-table-wrap {{
            max-height: 560px;
            overflow-y: auto;
            border: 1px solid {BORDER};
            border-radius: 16px;
            animation: c24FadeIn 0.4s ease-out;
        }}
        table.c24-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.88rem;
        }}
        table.c24-table thead th {{
            position: sticky;
            top: 0;
            background-color: {SURFACE_ALT};
            color: {TEXT_SECONDARY};
            text-transform: uppercase;
            font-size: 0.72rem;
            font-weight: 600;
            letter-spacing: 0.04em;
            text-align: left;
            padding: 0.85rem 1.1rem;
            border-bottom: 1px solid {BORDER};
            z-index: 1;
        }}
        table.c24-table tbody td {{
            padding: 0.75rem 1.1rem;
            border-bottom: 1px solid {BORDER};
            color: {TEXT_PRIMARY};
            vertical-align: top;
        }}
        table.c24-table tbody tr {{
            transition: background-color 0.15s ease;
        }}
        table.c24-table tbody tr:nth-child(even) {{
            background-color: rgba(255, 255, 255, 0.025);
        }}
        table.c24-table tbody tr:hover {{
            background-color: rgba(37, 99, 235, 0.1);
        }}

        [data-testid="stExpander"] {{
            background-color: {SURFACE};
            border: 1px solid {BORDER};
            border-radius: 14px;
        }}
        [data-testid="stSlider"] label {{
            color: {TEXT_SECONDARY};
            font-weight: 600;
        }}
        div[data-baseweb="slider"] > div > div {{
            background: {BRAND_ORANGE} !important;
        }}
        [data-testid="stStatusWidget"] {{
            background-color: {SURFACE};
            border: 1px solid {BORDER};
            border-radius: 12px;
        }}
        [data-testid="stVerticalBlock"] > [style*="border"] {{
            transition: box-shadow 0.25s ease;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_header() -> None:
    """Premium header: wordmark, subtitle, and a top-right 'Last Updated' timestamp."""
    if REPORT_PATH.exists():
        mtime = datetime.fromtimestamp(REPORT_PATH.stat().st_mtime)
        last_updated_date = mtime.strftime("%d %b %Y")
        last_updated_time = mtime.strftime("%H:%M:%S")
    else:
        last_updated_date, last_updated_time = "No analysis yet", ""

    col_title, col_updated = st.columns([3, 1.2])
    with col_title:
        st.markdown(
            f"""
            <div style="line-height: 1.2; margin-bottom: 0.25rem;">
                <span style="font-size: 2.6rem; font-weight: 800; color: {TEXT_PRIMARY};">Cars24 Reddit
                <span style="color: {BRAND_BLUE};">Reputation</span>
                <span style="color: {BRAND_ORANGE};">Monitor</span></span>
                <div style="font-size: 1.05rem; color: {TEXT_SECONDARY}; font-weight: 400; margin-top: 0.35rem;">
                    AI-powered Reddit Reputation Intelligence
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col_updated:
        st.markdown(
            f"""
            <div style="text-align: right; margin-top: 0.5rem;">
                <div style="color: {TEXT_SECONDARY}; font-size: 0.75rem; text-transform: uppercase;
                            letter-spacing: 0.06em; font-weight: 600;">Last Updated</div>
                <div style="color: {TEXT_PRIMARY}; font-size: 0.98rem; font-weight: 700; margin-top: 0.2rem;">
                    {html.escape(last_updated_date)}
                </div>
                <div style="color: {TEXT_SECONDARY}; font-size: 0.85rem; font-weight: 400;">
                    {html.escape(last_updated_time)}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_controls() -> tuple[int, bool]:
    """Render the discussion-count slider and the large primary analyze button."""
    col_slider, col_button = st.columns([3, 1.3])
    with col_slider:
        max_results = st.slider(
            "Number of Reddit discussions",
            min_value=10,
            max_value=100,
            value=25,
            step=5,
        )
    with col_button:
        st.write("")
        clicked = st.button("Analyze Latest Reddit Discussions", width="stretch", type="primary")
    return max_results, clicked


def _error_card(message: str) -> None:
    """Friendly, on-brand error card — used instead of raw tracebacks so the app never looks broken."""
    st.markdown(
        f"""
        <div style="background-color: {_hex_to_rgba(STATUS_RED, 0.1)}; border: 1px solid {_hex_to_rgba(STATUS_RED, 0.4)};
                    border-left: 4px solid {STATUS_RED}; border-radius: 14px; padding: 1.1rem 1.4rem;">
            <span style="font-weight: 700; color: {STATUS_RED};">&#9888; Something went wrong</span>
            <div style="color: {TEXT_SECONDARY}; margin-top: 0.4rem;">{html.escape(message)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _empty_state(message: str) -> None:
    """Neutral placeholder card for sections with no data yet."""
    st.markdown(
        f"""
        <div class="c24-card" style="color: {TEXT_SECONDARY}; text-align: center;">
            {html.escape(message)}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _run_pipeline(max_results: int) -> bool:
    """Run collector -> claude_agent -> risk_agent -> report_generator for Cars24.

    Mirrors main.py's orchestration, but threads the requested discussion count
    into collect_posts (main.py's entry point takes no arguments), and narrates
    progress through a step tracker. Returns True on success.
    """
    try:
        with st.status("Running Cars24 reputation analysis...", expanded=True) as status:
            status.write("🔎 Collecting Reddit discussions...")
            try:
                posts = collect_posts(query="cars24", max_results=max_results)
            except Exception as exc:
                print(f"[dashboard] STEP 1-4 (collect_posts) raised an exception: {exc!r}")
                traceback.print_exc()
                raise

            status.write(f"🧹 Filtering relevant posts... ({len(posts)} collected)")
            try:
                relevant_posts, reputation_summary = analyze_posts(posts)
            except Exception as exc:
                print(f"[dashboard] STEP 5-6 (analyze_posts) raised an exception: {exc!r}")
                traceback.print_exc()
                raise
            status.write(f"🧠 Gemini reputation summary: {reputation_summary}")

            status.write(f"🧮 Scoring reputation... ({len(relevant_posts)} relevant)")
            try:
                scored_posts = assess_posts(relevant_posts)
            except Exception as exc:
                print(f"[dashboard] STEP 7 (assess_posts) raised an exception: {exc!r}")
                traceback.print_exc()
                raise

            status.write("📄 Generating report...")
            try:
                report = build_report(scored_posts)
                save_report(report, scored_posts, OUTPUT_DIR)
            except Exception as exc:
                print(f"[dashboard] STEP 8 (build_report/save_report) raised an exception: {exc!r}")
                traceback.print_exc()
                raise

            status.write("✨ Preparing dashboard...")
            status.update(
                label=f"Analysis complete — {len(scored_posts)} discussion(s) scored.",
                state="complete",
                expanded=False,
            )
    except GeminiUnavailableError as exc:
        print(f"[dashboard] _run_pipeline aborted: GeminiUnavailableError: {exc}")
        _error_card(str(exc))
        return False
    except Exception as exc:  # noqa: BLE001 - surface any pipeline failure in a friendly card, never crash
        print(f"[dashboard] _run_pipeline aborted: {exc!r}")
        traceback.print_exc()
        _error_card(f"Pipeline run failed: {exc}")
        return False
    return True


def _metric_card(column: Any, icon: str, label: str, value: Any, accent: str) -> None:
    """Render one large KPI card: bigger icon, bigger value, colored top border, subtle gradient."""
    gradient = f"linear-gradient(160deg, {_hex_to_rgba(accent, 0.16)} 0%, {SURFACE} 60%, {SURFACE_ALT} 100%)"
    column.markdown(
        f"""
        <div class="c24-card c24-kpi-card" style="border-top: 4px solid {accent}; background: {gradient};">
            <div style="display: flex; align-items: center; gap: 0.6rem;">
                <span class="c24-kpi-icon">{icon}</span>
                <span class="c24-kpi-label">{html.escape(label)}</span>
            </div>
            <div class="c24-kpi-value">{html.escape(str(value))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _severity_for_score(score: float) -> str:
    """Map an average risk score to a priority label, reusing risk_agent's own thresholds."""
    for threshold, label in PRIORITY_THRESHOLDS:
        if score >= threshold:
            return label
    return "Low"


def _overall_status(report: dict[str, Any]) -> tuple[str, str]:
    """Derive a single reputation status label from the worst priority level present."""
    counts = report["statistics"]["priority_breakdown"]
    for level in ["Critical", "High", "Medium", "Low"]:
        if counts.get(level, 0) > 0:
            return level, STATUS_COLORS[level]
    return "No Data", TEXT_SECONDARY


def _render_kpis(report: dict[str, Any], results: pd.DataFrame) -> None:
    """Render the top KPI row: total discussions, avg risk score, high-risk count, overall status."""
    total = report["statistics"]["total_discussions"]
    avg_risk_score = results["risk_score"].mean() if not results.empty else 0.0
    high_risk_count = int(results["priority"].isin(["Critical", "High"]).sum()) if not results.empty else 0
    status_label, status_color = _overall_status(report)
    avg_score_color = STATUS_COLORS[_severity_for_score(avg_risk_score)] if not results.empty else TEXT_SECONDARY

    col1, col2, col3, col4 = st.columns(4)
    _metric_card(col1, "📊", "Total Discussions", total, BRAND_BLUE)
    _metric_card(col2, "⚠️", "Average Risk Score", f"{avg_risk_score:.1f} / 100", avg_score_color)
    _metric_card(col3, "🚨", "High Risk Discussions", high_risk_count, STATUS_RED)
    _metric_card(col4, "🛡️", "Overall Reputation Status", status_label, status_color)


def _render_executive_summary(report: dict[str, Any]) -> None:
    """Render the executive summary text from report.json in a large, readable card."""
    st.subheader("Executive Summary")
    st.markdown(
        f"""
        <div class="c24-card" style="border-left: 4px solid {BRAND_BLUE};">
            <p style="font-size: 1.08rem; line-height: 1.75; color: {TEXT_PRIMARY}; font-weight: 400; margin: 0;">
                {html.escape(report["executive_summary"])}
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _priority_badge(priority: str, *, font_size: str = "0.72rem") -> str:
    """Build a colored, text-labeled priority pill (never color alone)."""
    color = STATUS_COLORS.get(priority, TEXT_SECONDARY)
    return (
        f'<span style="display:inline-block; padding:0.22rem 0.75rem; border-radius:999px; '
        f'background-color:{_hex_to_rgba(color, 0.16)}; color:{color}; border:1px solid {_hex_to_rgba(color, 0.45)}; '
        f'font-size:{font_size}; font-weight:700; letter-spacing:0.03em; text-transform:uppercase;">'
        f"{html.escape(priority) if priority else '—'}</span>"
    )


def _render_highest_risk(report: dict[str, Any]) -> None:
    """Highlighted centerpiece card for the single highest-risk discussion, if any."""
    st.subheader("Highest Risk Discussion")
    highest = report.get("highest_risk_discussion")
    if not highest:
        _empty_state("No discussions collected yet.")
        return

    title = html.escape(highest.get("title", ""))
    subreddit = html.escape(highest.get("subreddit") or "unknown")
    url = str(highest.get("url", ""))
    reasons = highest.get("reasons", [])
    reasons_html = (
        "".join(
            f'<span style="display:inline-block; background-color:{_hex_to_rgba(BRAND_ORANGE, 0.14)}; '
            f'color:{BRAND_ORANGE}; border:1px solid {_hex_to_rgba(BRAND_ORANGE, 0.35)}; border-radius:8px; '
            f'padding:0.25rem 0.7rem; font-size:0.8rem; font-weight:600; margin:0.25rem 0.4rem 0 0;">'
            f"{html.escape(reason)}</span>"
            for reason in reasons
        )
        or f'<span style="color:{TEXT_SECONDARY};">No specific risk factors flagged.</span>'
    )
    link_html = (
        f'<a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer" '
        f'class="c24-link-btn">Open on Reddit ↗</a>'
        if url.startswith(("http://", "https://"))
        else f'<span style="color:{TEXT_SECONDARY};">No link available</span>'
    )

    st.markdown(
        f"""
        <div class="c24-card" style="border: 1px solid {_hex_to_rgba(STATUS_RED, 0.35)}; border-top: 5px solid {STATUS_RED};
                    background: linear-gradient(160deg, {_hex_to_rgba(STATUS_RED, 0.1)} 0%, {SURFACE} 55%, {SURFACE_ALT} 100%);
                    box-shadow: 0 0 0 1px {_hex_to_rgba(STATUS_RED, 0.12)}, 0 16px 36px {_hex_to_rgba(STATUS_RED, 0.16)};
                    padding: 1.85rem 2rem;">
            <div style="font-size: 1.4rem; font-weight: 800; color: {TEXT_PRIMARY}; line-height: 1.35;">{title}</div>
            <div style="display: flex; flex-wrap: wrap; align-items: center; gap: 0.9rem; margin-top: 1rem;">
                {_priority_badge(highest.get("priority", ""), font_size="0.78rem")}
                <span style="color: {TEXT_SECONDARY}; font-size: 0.95rem;">Risk Score
                    <strong style="color: {TEXT_PRIMARY}; font-size: 1.05rem;">{highest.get("risk_score", 0)}</strong>
                    / 100</span>
                <span style="color: {TEXT_SECONDARY}; font-size: 0.95rem;">r/{subreddit}</span>
            </div>
            <div style="margin-top: 1rem;">{reasons_html}</div>
            <div style="margin-top: 1.4rem;">{link_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _chart_heading(text: str) -> None:
    """Centered chart title, matching the enlarged section-heading typography."""
    st.markdown(f'<h3 style="text-align: center;">{html.escape(text)}</h3>', unsafe_allow_html=True)


def _render_risk_distribution(report: dict[str, Any]) -> None:
    """Bar chart of discussion counts per priority level, colored by severity status."""
    with st.container(border=True):
        _chart_heading("Risk Distribution")
        counts = report["statistics"]["priority_breakdown"]
        dist_df = pd.DataFrame(
            [{priority: counts.get(priority, 0) for priority in PRIORITY_ORDER}],
            index=["Discussions"],
        )
        st.bar_chart(
            dist_df,
            y=PRIORITY_ORDER,
            color=[STATUS_COLORS[priority] for priority in PRIORITY_ORDER],
            x_label="",
            y_label="Discussions",
            height=430,
        )


def _render_top_complaints(report: dict[str, Any]) -> None:
    """Bar chart ranking the most common risk factors raised across discussions."""
    with st.container(border=True):
        _chart_heading("Top Complaint Reasons")
        complaints_df = pd.DataFrame(report["top_complaints"])
        if complaints_df.empty:
            _empty_state("No complaint categories flagged yet.")
            return
        st.bar_chart(
            complaints_df,
            x="reason",
            y="count",
            color=BRAND_BLUE,
            horizontal=True,
            sort="-count",
            x_label="",
            y_label="Mentions",
            height=430,
        )


def _render_discussions_table(results: pd.DataFrame) -> None:
    """Professional table: zebra striping, sticky header, hover highlight, colored priority badges."""
    st.subheader("Reddit Discussions")
    if results.empty:
        _empty_state("No discussions collected yet.")
        return

    table_df = results.sort_values("risk_score", ascending=False).reset_index(drop=True)

    rows = []
    for _, row in table_df.iterrows():
        title = html.escape(str(row.get("title", "")))
        subreddit = html.escape(str(row.get("subreddit", "")) or "—")
        reasons = html.escape(str(row.get("reasons", "")) or "—")
        url = str(row.get("url", ""))
        link_html = (
            f'<a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer" '
            f'style="color:{BRAND_BLUE}; font-weight:600; text-decoration:none;">Open ↗</a>'
            if url.startswith(("http://", "https://"))
            else f'<span style="color:{TEXT_SECONDARY};">—</span>'
        )
        rows.append(
            f"""<tr>
                <td>{_priority_badge(str(row.get("priority", "")))}</td>
                <td style="font-weight:700;">{row.get("risk_score", 0)}</td>
                <td style="max-width:320px;">{title}</td>
                <td style="color:{TEXT_SECONDARY};">{subreddit}</td>
                <td style="max-width:240px; color:{TEXT_SECONDARY};">{reasons}</td>
                <td>{row.get("score", 0)}</td>
                <td>{row.get("num_comments", 0)}</td>
                <td>{link_html}</td>
            </tr>"""
        )

    table_html = f"""
    <div class="c24-table-wrap">
        <table class="c24-table">
            <thead>
                <tr>
                    <th>Priority</th>
                    <th>Risk Score</th>
                    <th>Title</th>
                    <th>Subreddit</th>
                    <th>Reason</th>
                    <th>Score</th>
                    <th>Comments</th>
                    <th>Open Reddit</th>
                </tr>
            </thead>
            <tbody>{"".join(rows)}</tbody>
        </table>
    </div>
    """
    st.markdown(table_html, unsafe_allow_html=True)
    st.caption(f"Sorted by risk score, highest first · {len(table_df)} discussion(s) shown.")


def _render_report_json(report: dict[str, Any]) -> None:
    """Expandable section showing the raw report.json."""
    with st.expander("View raw report.json"):
        st.json(report)


def _render_footer(report: dict[str, Any]) -> None:
    """Professional footer: brand line plus last-analysis time, posts analyzed, and report status."""
    if REPORT_PATH.exists():
        mtime = datetime.fromtimestamp(REPORT_PATH.stat().st_mtime)
        last_analysis_time = mtime.strftime("%d %b %Y, %H:%M:%S")
        generated_report = mtime.strftime("%d %b %Y, %H:%M:%S")
    else:
        last_analysis_time = generated_report = "Unknown"
    total = report["statistics"]["total_discussions"]

    st.markdown(
        f"""
        <div class="c24-card">
            <div style="display: flex; flex-wrap: wrap; justify-content: space-between; align-items: flex-start; gap: 1.75rem;">
                <div>
                    <div style="font-size: 1.1rem; font-weight: 800; color: {TEXT_PRIMARY};">
                        Cars24 Reddit Reputation Monitor
                    </div>
                    <div style="font-size: 0.88rem; color: {TEXT_SECONDARY}; font-weight: 400; margin-top: 0.2rem;">
                        AI-powered Reputation Intelligence
                    </div>
                </div>
                <div style="display: flex; flex-wrap: wrap; gap: 2.5rem;">
                    <div>
                        <div style="color: {TEXT_SECONDARY}; font-size: 0.72rem; text-transform: uppercase;
                                    letter-spacing: 0.05em; font-weight: 500;">Last Analysis Time</div>
                        <div style="color: {TEXT_PRIMARY}; font-weight: 700; margin-top: 0.25rem;">
                            {html.escape(last_analysis_time)}
                        </div>
                    </div>
                    <div>
                        <div style="color: {TEXT_SECONDARY}; font-size: 0.72rem; text-transform: uppercase;
                                    letter-spacing: 0.05em; font-weight: 500;">Posts Analyzed</div>
                        <div style="color: {TEXT_PRIMARY}; font-weight: 700; margin-top: 0.25rem;">{total}</div>
                    </div>
                    <div>
                        <div style="color: {TEXT_SECONDARY}; font-size: 0.72rem; text-transform: uppercase;
                                    letter-spacing: 0.05em; font-weight: 500;">Generated Report</div>
                        <div style="color: {STATUS_GREEN}; font-weight: 700; margin-top: 0.25rem;">
                            &#9989; {html.escape(generated_report)}
                        </div>
                    </div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(page_title="Cars24 Reddit Reputation Monitor", layout="wide")
    _inject_theme()
    _render_header()
    _spacer(24)

    max_results, analyze_clicked = _render_controls()
    if analyze_clicked and _run_pipeline(max_results):
        st.rerun()

    if not REPORT_PATH.exists() or not RESULTS_PATH.exists():
        _spacer(20)
        _empty_state(
            "No report found yet. Set a discussion count above and click "
            "“Analyze Latest Reddit Discussions” to run the pipeline."
        )
        return

    try:
        report = load_report()
        results = load_results()
    except Exception as exc:  # noqa: BLE001 - surface a malformed/partial report without crashing
        print(f"[dashboard] STEP 9: Dashboard failed to load report.json/results.csv: {exc!r}")
        traceback.print_exc()
        _spacer(20)
        _error_card(f"Failed to load the latest report: {exc}")
        return

    total_discussions = report.get("statistics", {}).get("total_discussions", 0)
    print(f"STEP 9: Dashboard loaded {total_discussions} discussions")
    if total_discussions == 0:
        report_mtime = datetime.fromtimestamp(REPORT_PATH.stat().st_mtime) if REPORT_PATH.exists() else None
        print(
            "STEP 9 ZERO REASON: report.json's statistics.total_discussions is 0 "
            f"(report last written: {report_mtime}). See STEP 1-8 above from the run that produced "
            "this report.json for which stage first produced zero."
        )

    _spacer(32)
    _render_kpis(report, results)
    _spacer(36)
    _render_executive_summary(report)
    _spacer(32)
    _render_highest_risk(report)
    _spacer(36)

    col_left, col_right = st.columns(2)
    with col_left:
        _render_risk_distribution(report)
    with col_right:
        _render_top_complaints(report)

    _spacer(36)
    _render_discussions_table(results)
    _spacer(20)
    _render_report_json(report)
    _spacer(32)
    _render_footer(report)


if __name__ == "__main__":
    main()
