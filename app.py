"""
Curve Dental Practice Dashboard
Simple Streamlit dashboard using the Curve Hero API.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from collections import Counter

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from curve_api import CurveAPI

st.set_page_config(
    page_title="Curve Dental Dashboard",
    page_icon="🦷",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    .block-container { padding-top: 1.2rem; }
    div[data-testid="stMetricValue"] { font-size: 1.6rem; }
    .stMetric { background: #f8fafc; border-radius: 10px; padding: 12px 16px; border: 1px solid #e2e8f0; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def get_api() -> CurveAPI:
    return CurveAPI()


@st.cache_data(ttl=120)
def load_clinics():
    return get_api().get_clinics()


@st.cache_data(ttl=120)
def load_staff():
    return get_api().get_staff()


@st.cache_data(ttl=60)
def load_appointments(start_str: str, end_str: str):
    start = datetime.strptime(start_str, "%Y-%m-%d")
    end = datetime.strptime(end_str, "%Y-%m-%d") + timedelta(hours=23, minutes=59, seconds=59)
    return get_api().get_appointments(start, end)


@st.cache_data(ttl=300)
def load_patients_sample(status: str):
    return get_api().get_patients(status=status)


def appts_to_df(appts: list[dict]) -> pd.DataFrame:
    if not appts:
        return pd.DataFrame()
    rows = []
    for a in appts:
        rows.append(
            {
                "id": a.get("id"),
                "startTime": a.get("startTime"),
                "status": (a.get("appointmentStatus") or "").strip(),
                "length_min": a.get("length"),
                "providerId": str(a.get("providerId") or ""),
                "providerName": (a.get("providerName") or "").strip() or "Unknown",
                "operatoryId": str(a.get("operatoryId") or ""),
                "patientId": str(a.get("patientId") or ""),
                "clinicId": str(a.get("clinicId") or ""),
                "clinicName": a.get("clinicName") or "",
                "description": a.get("description") or "",
                "confirmation": a.get("confirmation") or "",
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df["startTime"] = pd.to_datetime(df["startTime"], errors="coerce")
        df["date"] = df["startTime"].dt.date
        df["hour"] = df["startTime"].dt.hour
        df["weekday"] = df["startTime"].dt.day_name()
    return df


def status_bucket(status: str) -> str:
    s = (status or "").lower()
    if s in ("missed",):
        return "No-Show"
    if s in ("cancel", "cancelled", "canceled"):
        return "Cancelled"
    if s in ("check out", "checkout"):
        return "Completed"
    if s in ("check in", "checkin", "active"):
        return "Scheduled / In Progress"
    return status or "Other"


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.title("🦷 Curve Dental")
st.sidebar.caption("Practice Dashboard")

today = datetime.now().date()
default_start = today - timedelta(days=6)
default_end = today

date_range = st.sidebar.date_input(
    "Date range",
    value=(default_start, default_end),
    max_value=today + timedelta(days=30),
)

if isinstance(date_range, tuple) and len(date_range) == 2:
    start_date, end_date = date_range
else:
    start_date = end_date = date_range if not isinstance(date_range, tuple) else today

st.sidebar.markdown("---")
if st.sidebar.button("🔄 Refresh data", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.caption("Data from Curve Hero API · cached ~1–5 min")

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
try:
    clinics = load_clinics()
    staff = load_staff()
    appts = load_appointments(start_date.isoformat(), end_date.isoformat())
    df = appts_to_df(appts)
except Exception as e:
    st.error(f"Could not load data from Curve API: {e}")
    st.info("Check your `.env` credentials (CURVE_CLIENT_ID, CURVE_CLIENT_SECRET, CURVE_INSTANCE).")
    st.stop()

clinic_name = clinics[0].get("name", "Practice") if clinics else "Practice"
st.title(f"{clinic_name}")
st.caption(f"Appointments from **{start_date}** to **{end_date}**")

# ---------------------------------------------------------------------------
# KPI row
# ---------------------------------------------------------------------------
if df.empty:
    st.warning("No appointments found in this date range.")
    total = completed = noshow = cancelled = scheduled = 0
    noshow_rate = cancel_rate = 0.0
else:
    df["bucket"] = df["status"].map(status_bucket)
    total = len(df)
    completed = int((df["bucket"] == "Completed").sum())
    noshow = int((df["bucket"] == "No-Show").sum())
    cancelled = int((df["bucket"] == "Cancelled").sum())
    scheduled = int((df["bucket"] == "Scheduled / In Progress").sum())
    denom = total if total else 1
    noshow_rate = 100.0 * noshow / denom
    cancel_rate = 100.0 * cancelled / denom

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total appointments", f"{total}")
c2.metric("Completed", f"{completed}")
c3.metric("No-shows", f"{noshow}", delta=f"{noshow_rate:.1f}%", delta_color="inverse")
c4.metric("Cancelled", f"{cancelled}", delta=f"{cancel_rate:.1f}%", delta_color="inverse")
c5.metric("Scheduled / active", f"{scheduled}")

st.markdown("---")

# ---------------------------------------------------------------------------
# Charts row
# ---------------------------------------------------------------------------
left, right = st.columns(2)

with left:
    st.subheader("Status breakdown")
    if not df.empty:
        counts = df["bucket"].value_counts().reset_index()
        counts.columns = ["Status", "Count"]
        fig = px.pie(
            counts,
            names="Status",
            values="Count",
            color="Status",
            color_discrete_map={
                "Completed": "#22c55e",
                "No-Show": "#ef4444",
                "Cancelled": "#f97316",
                "Scheduled / In Progress": "#3b82f6",
                "Other": "#94a3b8",
            },
            hole=0.45,
        )
        fig.update_layout(margin=dict(t=20, b=20, l=20, r=20), height=320)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No data")

with right:
    st.subheader("Appointments by day")
    if not df.empty:
        by_day = df.groupby(["date", "bucket"]).size().reset_index(name="count")
        fig2 = px.bar(
            by_day,
            x="date",
            y="count",
            color="bucket",
            barmode="stack",
            color_discrete_map={
                "Completed": "#22c55e",
                "No-Show": "#ef4444",
                "Cancelled": "#f97316",
                "Scheduled / In Progress": "#3b82f6",
                "Other": "#94a3b8",
            },
            labels={"date": "Date", "count": "Appointments", "bucket": "Status"},
        )
        fig2.update_layout(margin=dict(t=20, b=20, l=20, r=20), height=320, legend_title_text="")
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No data")

# ---------------------------------------------------------------------------
# Provider schedule
# ---------------------------------------------------------------------------
st.subheader("By provider")
if not df.empty:
    by_prov = (
        df.groupby(["providerName", "bucket"])
        .size()
        .reset_index(name="count")
    )
    pivot = by_prov.pivot(index="providerName", columns="bucket", values="count").fillna(0).astype(int)
    pivot["Total"] = pivot.sum(axis=1)
    pivot = pivot.sort_values("Total", ascending=False)
    st.dataframe(pivot, use_container_width=True, height=min(400, 40 + 35 * len(pivot)))

    fig3 = px.bar(
        by_prov,
        x="providerName",
        y="count",
        color="bucket",
        barmode="stack",
        labels={"providerName": "Provider", "count": "Appointments", "bucket": "Status"},
        color_discrete_map={
            "Completed": "#22c55e",
            "No-Show": "#ef4444",
            "Cancelled": "#f97316",
            "Scheduled / In Progress": "#3b82f6",
            "Other": "#94a3b8",
        },
    )
    fig3.update_layout(margin=dict(t=10, b=10), height=340, legend_title_text="")
    st.plotly_chart(fig3, use_container_width=True)
else:
    st.info("No appointments to show by provider.")

# ---------------------------------------------------------------------------
# Appointment list
# ---------------------------------------------------------------------------
st.subheader("Appointment list")
if not df.empty:
    show = df[
        ["startTime", "status", "providerName", "operatoryId", "patientId", "length_min", "description", "confirmation"]
    ].copy()
    show = show.sort_values("startTime", ascending=False)
    show.columns = ["Start", "Status", "Provider", "Operatory", "Patient ID", "Length (min)", "Notes", "Confirmation"]
    st.dataframe(show, use_container_width=True, height=420)
else:
    st.info("No appointments in range.")

# ---------------------------------------------------------------------------
# Staff & clinic info
# ---------------------------------------------------------------------------
with st.expander("Practice & staff details"):
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**Clinic**")
        if clinics:
            c = clinics[0]
            st.write(f"Name: {c.get('name')}")
            addr = c.get("address") or {}
            st.write(
                f"Address: {addr.get('line1') or ''}, {addr.get('city') or ''} "
                f"{addr.get('countrySubdivision') or ''} {addr.get('postal') or ''}"
            )
            phone = (c.get("officePhone") or {}).get("number")
            st.write(f"Phone: {phone or '—'}")
            st.write(f"Email: {c.get('email') or '—'}")
            st.write(f"Timezone: {c.get('timeZone') or '—'}")
        else:
            st.write("No clinic data")
    with col_b:
        st.markdown("**Active staff (sample)**")
        active_staff = [s for s in staff if (s.get("status") or "").lower() == "active"]
        if active_staff:
            staff_df = pd.DataFrame(
                [
                    {
                        "Name": f"{s.get('firstName', '')} {s.get('lastName', '')}".strip(),
                        "Role": s.get("kind") or "",
                        "Short": s.get("shortName") or "",
                        "Email": s.get("email") or "",
                    }
                    for s in active_staff[:30]
                ]
            )
            st.dataframe(staff_df, use_container_width=True, height=280)
        else:
            st.write("No staff returned")

st.markdown("---")
st.caption(
    "Production, collections, AR, and treatment-plan metrics need additional Curve API access "
    "(ledger / financial endpoints). Current dashboard uses Scheduling + Patient + Clinic + Staff."
)
