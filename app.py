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


@st.cache_data(ttl=180)
def load_ledger(start_str: str, end_str: str):
    return get_api().get_ledger_entries(start_str, end_str)


@st.cache_data(ttl=180)
def load_appointment_items(start_str: str, end_str: str):
    return get_api().get_appointment_items(start_str, end_str)


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
    ledger = load_ledger(start_date.isoformat(), end_date.isoformat())
    # Future appointments (next 2 weeks) and their items
    future_start = (end_date + timedelta(days=1)).isoformat()
    future_end = (end_date + timedelta(days=14)).isoformat()
    future_appts = load_appointments(future_start, future_end)
    future_df = appts_to_df(future_appts)
    future_items = load_appointment_items(future_start, future_end)
except Exception as e:
    st.error(f"Could not load data from Curve API: {e}")
    st.info("Check your `.env` credentials (CURVE_CLIENT_ID, CURVE_CLIENT_SECRET, CURVE_INSTANCE).")
    st.stop()

clinic_name = clinics[0].get("name", "Practice") if clinics else "Practice"
st.title(f"{clinic_name}")
st.caption(f"Performance Scorecard · {start_date} to {end_date}")

# ---------------------------------------------------------------------------
# Compute metrics
# ---------------------------------------------------------------------------
from decimal import Decimal

if df.empty:
    total = completed = noshow = cancelled = scheduled = 0
    noshow_rate = cancel_rate = 0.0
    unique_patients = 0
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
    unique_patients = df["patientId"].nunique()

np_fee_count = 0
np_patients = set()
production = Decimal(0)
payments = Decimal(0)
if ledger:
    for entry in ledger:
        calc = entry.get("calculations") or {}
        production += Decimal(str(calc.get("production") or 0))
        payments += Decimal(str(calc.get("payments") or 0))
        if entry.get("transactionType") == "Invoice" and entry.get("amount") == "49.00":
            np_fee_count += 1
            if entry.get("patientId"):
                np_patients.add(str(entry["patientId"]))

# Future scheduled treatment
future_np_patients = set()
future_np_value = Decimal(0)
future_appt_ids = set()
if not future_df.empty:
    future_appt_ids = set(future_df["id"].astype(str))
    # Patients who have future appointments
    for pid in future_df["patientId"].astype(str):
        if pid in np_patients:
            future_np_patients.add(pid)

if future_items:
    for item in future_items:
        appt_id = str((item.get("appointment") or {}).get("id") or "")
        if appt_id in future_appt_ids:
            cost = Decimal(str(item.get("cost") or 0))
            future_np_value += cost

# Breakdown of $49 patients
completed_np = set()
if not df.empty and np_patients:
    for _, row in df.iterrows():
        pid = str(row.get("patientId") or "")
        if pid not in np_patients:
            continue
        status = (row.get("status") or "").lower()
        if status in ("check out", "checkout"):
            completed_np.add(pid)

no_activity_np = np_patients - completed_np - future_np_patients
np_total = len(np_patients) or 1
pct = lambda n: f"{n} ({100.0 * n / np_total:.0f}%)"

days_in_range = max(1, (end_date - start_date).days + 1)
prod_per_day = float(production) / days_in_range if production else 0
prod_per_patient = float(production) / unique_patients if unique_patients else 0
coll_ratio = (float(payments) / float(production) * 100) if production else 0

# ---------------------------------------------------------------------------
# 1. Clinic Performance Scorecard
# ---------------------------------------------------------------------------
st.subheader("1. Clinic Performance")

scorecard = pd.DataFrame({
    "KPI": [
        "New patients (paid $49)",
        "→ Already completed something",
        "→ Have future appointment",
        "→ No activity yet",
        "Scheduled treatment value (next 2 wks)",
        "Unique $49 invoices",
        "Patients Seen",
        "Completed Appointments",
        "No-shows",
        "Cancelled",
        "Adjusted Production",
        "Collections (Payments)",
        "Collections / Production",
        "Production / Day",
        "Production / Patient",
        "Days in range",
    ],
    "Value": [
        str(len(np_patients)),
        pct(len(completed_np)),
        pct(len(future_np_patients)),
        pct(len(no_activity_np)),
        f"${future_np_value:,.2f}",
        str(np_fee_count),
        str(unique_patients),
        str(completed),
        f"{noshow} ({noshow_rate:.1f}%)",
        f"{cancelled} ({cancel_rate:.1f}%)",
        f"${production:,.2f}",
        f"${payments:,.2f}",
        f"{coll_ratio:.1f}%",
        f"${prod_per_day:,.2f}",
        f"${prod_per_patient:,.2f}",
        str(days_in_range),
    ],
})
st.dataframe(scorecard, use_container_width=True, hide_index=True, height=460)

# ---------------------------------------------------------------------------
# 2. Production by Provider
# ---------------------------------------------------------------------------
st.subheader("2. Production by Provider")
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

# ---------------------------------------------------------------------------
# 3. Charts
# ---------------------------------------------------------------------------
st.subheader("3. Appointment status")
left, right = st.columns(2)
with left:
    if not df.empty:
        counts = df["bucket"].value_counts().reset_index()
        counts.columns = ["Status", "Count"]
        fig = px.pie(counts, names="Status", values="Count", hole=0.45)
        fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=300)
        st.plotly_chart(fig, use_container_width=True)
with right:
    if not df.empty:
        by_day = df.groupby(["date", "bucket"]).size().reset_index(name="count")
        fig2 = px.bar(by_day, x="date", y="count", color="bucket", barmode="stack")
        fig2.update_layout(margin=dict(t=10, b=10), height=300, legend_title_text="")
        st.plotly_chart(fig2, use_container_width=True)

# ---------------------------------------------------------------------------
# 4. Appointment list
# ---------------------------------------------------------------------------
st.subheader("4. Appointment list")
if not df.empty:
    show = df[
        ["startTime", "status", "providerName", "operatoryId", "patientId", "length_min", "description", "confirmation"]
    ].copy()
    show = show.sort_values("startTime", ascending=False)
    show.columns = ["Start", "Status", "Provider", "Operatory", "Patient ID", "Length (min)", "Notes", "Confirmation"]
    st.dataframe(show, use_container_width=True, height=400)

with st.expander("Practice & staff details"):
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**Clinic**")
        if clinics:
            c = clinics[0]
            st.write(f"Name: {c.get('name')}")
            addr = c.get("address") or {}
            st.write(f"Address: {addr.get('line1') or ''}, {addr.get('city') or ''}")
            st.write(f"Phone: {(c.get('officePhone') or {}).get('number') or '—'}")
    with col_b:
        st.markdown("**Active staff**")
        active_staff = [s for s in staff if (s.get("status") or "").lower() == "active"]
        if active_staff:
            staff_df = pd.DataFrame([
                {"Name": f"{s.get('firstName','')} {s.get('lastName','')}".strip(),
                 "Role": s.get("kind") or "",
                 "Short": s.get("shortName") or ""}
                for s in active_staff[:25]
            ])
            st.dataframe(staff_df, use_container_width=True, height=250)

st.markdown("---")
st.caption("Data from Curve Hero API. Production and payments from ledger entries. $49 = new patient fee invoices.")
