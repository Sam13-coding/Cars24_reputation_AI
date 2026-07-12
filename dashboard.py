"""Dashboard: Streamlit UI for the Cars24 reputation report.

Run with: streamlit run dashboard.py
"""

import json
from pathlib import Path

import pandas as pd
import streamlit as st

OUTPUT_DIR = Path("output")
REPORT_PATH = OUTPUT_DIR / "report.json"
RESULTS_PATH = OUTPUT_DIR / "results.csv"


def load_report() -> dict:
    return json.loads(REPORT_PATH.read_text(encoding="utf-8"))


def load_results() -> pd.DataFrame:
    return pd.read_csv(RESULTS_PATH)


def main() -> None:
    st.set_page_config(page_title="Cars24 Reputation Intelligence", layout="wide")
    st.title("Cars24 Reputation Intelligence")

    if not REPORT_PATH.exists() or not RESULTS_PATH.exists():
        st.warning("No report found yet. Run `python main.py` first to generate output/report.json and output/results.csv.")
        return

    report = load_report()
    results = load_results()

    st.subheader("Executive Summary")
    st.write(report["executive_summary"])

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Total discussions", report["statistics"]["total_discussions"])
        st.subheader("Priority breakdown")
        st.bar_chart(report["statistics"]["priority_breakdown"])
    with col2:
        st.subheader("Complaint categories")
        complaint_df = pd.DataFrame(report["top_complaints"])
        if not complaint_df.empty:
            st.bar_chart(complaint_df.set_index("reason"))

    st.subheader("Highest risk discussion")
    highest = report.get("highest_risk_discussion")
    if highest:
        st.markdown(f"**[{highest['title']}]({highest['url']})** — risk score {highest['risk_score']} ({highest['priority']})")
        st.write(", ".join(highest.get("reasons", [])) or "No specific risk factors flagged.")
    else:
        st.write("No discussions collected yet.")

    st.subheader("All flagged discussions")
    st.dataframe(results.sort_values("risk_score", ascending=False), use_container_width=True)


if __name__ == "__main__":
    main()
