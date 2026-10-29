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

from curve_api import CurveAPI, _get_secret
from whatsapp_funnel import render_whatsapp_funnel
from zoneinfo import ZoneInfo

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
    # Curve ledger endDate is exclusive, so add 1 day
    end_dt = datetime.strptime(end_str, "%Y-%m-%d") + timedelta(days=1)
    return get_api().get_ledger_entries(start_str, end_dt.strftime("%Y-%m-%d"))


@st.cache_data(ttl=180)
def load_appointment_items(start_str: str, end_str: str):
    return get_api().get_appointment_items(start_str, end_str)


@st.cache_data(ttl=180)
def load_treatment_plans():
    return get_api().get_treatment_plans_v2()


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



def group_missed_appointment_balances(pending_rows, entries):
    """Group only balances fully reconciled to D9986 in the same ledger period.

    Use signed AR movements so payments, reversals, and provider corrections
    are not counted as new charges. Mixed or unmatched balances stay visible.
    """
    from collections import defaultdict
    from decimal import Decimal

    by_patient = defaultdict(lambda: Decimal(0))
    for entry in entries:
        code = str(entry.get("insuranceCode") or "").strip().upper()
        if code != "D9986":
            continue
        patient_id = str(entry.get("patientId") or "")
        calculations = entry.get("calculations") or {}
        by_patient[patient_id] += Decimal(
            str(calculations.get("changeInAccountsReceivable") or 0)
        )
    other_rows, missed_rows = [], []
    for row in pending_rows:
        balance = Decimal(row["Balance"].replace("$", "").replace(",", ""))
        if balance > 0 and by_patient[str(row["Patient ID"])] == balance:
            missed_rows.append(dict(row))
        else:
            other_rows.append(dict(row))
    return other_rows, missed_rows


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.title("🦷 Curve Dental")
st.sidebar.caption("Practice Dashboard")

today = datetime.now(ZoneInfo("America/New_York")).date()
# Month to date by default
mtd_start = today.replace(day=1)
default_start = mtd_start
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

# Previous month (always the calendar month before the selected start)
prev_month_end = start_date.replace(day=1) - timedelta(days=1)
prev_month_start = prev_month_end.replace(day=1)
# Same number of days into previous month for comparison
days_into_month = (end_date - start_date).days
prev_end_date = prev_month_start + timedelta(days=days_into_month)
if prev_end_date > prev_month_end:
    prev_end_date = prev_month_end

st.sidebar.markdown("---")
if st.sidebar.button("🔄 Refresh data", use_container_width=True):
    st.cache_data.clear()
    get_api.clear()
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
    # Rest of month scheduled treatments
    month_end = end_date.replace(day=1) + timedelta(days=32)
    month_end = month_end.replace(day=1) - timedelta(days=1)
    future_start = (end_date + timedelta(days=1)).isoformat()
    future_end = month_end.isoformat()
    future_appts = load_appointments(future_start, future_end)
    future_df = appts_to_df(future_appts)
    future_items = load_appointment_items(future_start, future_end)
    try:
        treatment_plans = load_treatment_plans()
    except Exception:
        treatment_plans = []
    # Previous month comparison
    prev_appts = load_appointments(prev_month_start.isoformat(), prev_end_date.isoformat())
    prev_df = appts_to_df(prev_appts)
    prev_ledger = load_ledger(prev_month_start.isoformat(), prev_end_date.isoformat())
    # Full previous month for closing
    prev_month_end_full = prev_month_start.replace(day=1) + timedelta(days=32)
    prev_month_end_full = prev_month_end_full.replace(day=1)
    prev_full_ledger = load_ledger(prev_month_start.isoformat(), prev_month_end_full.isoformat())
except Exception as e:
    st.error(f"Could not load data from Curve API: {e}")
    st.info("Click Refresh data to renew the Curve connection. If access is still denied, check the app's Curve API permissions and Secrets.")
    st.stop()

clinic_name = clinics[0].get("name", "Practice") if clinics else "Practice"

# Header with logo
import os
col_logo, col_title = st.columns([1, 6])
with col_logo:
    logo_path = os.path.join(os.path.dirname(__file__), "logo.jpg")
    if not os.path.exists(logo_path):
        logo_path = "logo.jpg"
    if os.path.exists(logo_path):
        st.image(logo_path, width=180)
with col_title:
    st.title(clinic_name)

@st.fragment(run_every="60s")
def live_whatsapp_section():
    render_whatsapp_funnel(st, _get_secret, start_date, end_date, api=get_api())

live_whatsapp_section()
st.markdown("---")

# Labels for comparison (defined early)
prev_label = prev_month_start.strftime("%b %Y")
curr_label = start_date.strftime("%b %Y")
st.markdown(f"### Performance Scorecard · {curr_label} MTD ({start_date} to {end_date}) vs {prev_label}")

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
np_with_more = set()
production = Decimal(0)
payments = Decimal(0)
if ledger:
    for entry in ledger:
        calc = entry.get("calculations") or {}
        production += Decimal(str(calc.get("production") or 0))
        payments += Decimal(str(calc.get("payments") or 0))
        if entry.get("transactionType") == "Invoice":
            amt = Decimal(str(entry.get("amount") or 0))
            pid = str(entry.get("patientId") or "")
            if amt == Decimal("49.00") and pid:
                np_fee_count += 1
                np_patients.add(pid)
            elif amt > Decimal("49.00") and pid:
                np_with_more.add(pid)

# Patients who paid $49 AND have other invoices > $49
completed_np = np_patients & np_with_more

# Future scheduled treatment
future_np_patients = set()
future_np_value = Decimal(0)
if not future_df.empty and future_items:
    future_appt_ids = set(future_df["id"].astype(str))
    for pid in future_df["patientId"].astype(str).unique():
        if pid in np_patients:
            future_np_patients.add(pid)
    for item in future_items:
        appt_obj = item.get("appointment") or {}
        appt_id = str(appt_obj.get("id") or "")
        if appt_id in future_appt_ids:
            future_np_value += Decimal(str(item.get("cost") or 0))
    # Fallback: if no match, show total of all items in range
if future_items:
    for item in future_items:
        future_np_value += Decimal(str(item.get("cost") or 0))
elif not future_df.empty:
    for pid in future_df["patientId"].astype(str).unique():
        if pid in np_patients:
            future_np_patients.add(pid)

# Mutually exclusive buckets
completed_only = completed_np
future_only = future_np_patients - completed_np
no_activity_np = np_patients - completed_np - future_np_patients
np_total = len(np_patients) or 1
pct = lambda n: f"{n} ({100.0 * n / np_total:.0f}%)"

days_in_range = max(1, (end_date - start_date).days + 1)
prod_per_day = float(production) / days_in_range if production else 0
prod_per_patient = float(production) / unique_patients if unique_patients else 0
coll_ratio = (float(payments) / float(production) * 100) if production else 0

# Previous month metrics
prev_np_patients = set()
prev_np_with_more = set()
prev_production = Decimal(0)
prev_payments = Decimal(0)
prev_full_payments = Decimal(0)
if prev_ledger:
    for entry in prev_ledger:
        calc = entry.get("calculations") or {}
        prev_production += Decimal(str(calc.get("production") or 0))
        prev_payments += Decimal(str(calc.get("payments") or 0))
if prev_full_ledger:
    for entry in prev_full_ledger:
        calc = entry.get("calculations") or {}
        prev_full_payments += Decimal(str(calc.get("payments") or 0))
        if entry.get("transactionType") == "Invoice":
            amt = Decimal(str(entry.get("amount") or 0))
            pid = str(entry.get("patientId") or "")
            if amt == Decimal("49.00") and pid:
                prev_np_patients.add(pid)
            elif amt > Decimal("49.00") and pid:
                prev_np_with_more.add(pid)

prev_completed_np = prev_np_patients & prev_np_with_more
prev_unique = prev_df["patientId"].nunique() if not prev_df.empty else 0
prev_completed = 0
prev_noshow = 0
prev_cancelled = 0
if not prev_df.empty:
    prev_df["bucket"] = prev_df["status"].map(status_bucket)
    prev_completed = int((prev_df["bucket"] == "Completed").sum())
    prev_noshow = int((prev_df["bucket"] == "No-Show").sum())
    prev_cancelled = int((prev_df["bucket"] == "Cancelled").sum())
prev_days = max(1, (prev_end_date - prev_month_start).days + 1)
prev_prod_day = float(prev_production) / prev_days if prev_production else 0
prev_prod_pat = float(prev_production) / prev_unique if prev_unique else 0
prev_coll_ratio = (float(prev_payments) / float(prev_production) * 100) if prev_production else 0
prev_total_appts = len(prev_df) if not prev_df.empty else 1
prev_noshow_rate = 100.0 * prev_noshow / prev_total_appts if prev_total_appts else 0
prev_cancel_rate = 100.0 * prev_cancelled / prev_total_appts if prev_total_appts else 0
prev_np_total = len(prev_np_patients) or 1
prev_pct = lambda n: f"{n} ({100.0 * n / prev_np_total:.0f}%)"

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
        "Patients Seen",
        "Completed Appointments",
        "No-shows",
        "Cancelled",
    ],
    curr_label: [
        str(len(np_patients)),
        pct(len(completed_only)),
        pct(len(future_only)),
        pct(len(no_activity_np)),
        str(unique_patients),
        str(completed),
        f"{noshow} ({noshow_rate:.1f}%)",
        f"{cancelled} ({cancel_rate:.1f}%)",
    ],
    prev_label: [
        str(len(prev_np_patients)),
        prev_pct(len(prev_completed_np)),
        "—",
        "—",
        str(prev_unique),
        str(prev_completed),
        f"{prev_noshow} ({prev_noshow_rate:.1f}%)",
        f"{prev_cancelled} ({prev_cancel_rate:.1f}%)",
    ],
})
st.dataframe(scorecard, use_container_width=True, hide_index=True, height=380)

# Calculate scheduled treatments total early for projection
scheduled_total = Decimal(0)
if future_items:
    for item in future_items:
        scheduled_total += Decimal(str(item.get("amount") or item.get("cost") or 0))
month_projection = payments + scheduled_total

st.markdown("---")
st.subheader("Financials")

fin_scorecard = pd.DataFrame({
    "KPI": [
        "Adjusted Production",
        "Collections (Payments)",
        "Production / Day",
        "Production / Patient",
        "Days in range",
        "Month projection",
    ],
    curr_label: [
        f"${production:,.2f}",
        f"${payments:,.2f} ({coll_ratio:.1f}%)",
        f"${prod_per_day:,.2f}",
        f"${prod_per_patient:,.2f}",
        str(days_in_range),
        f"${payments + scheduled_total:,.2f}",
    ],
    prev_label: [
        f"${prev_production:,.2f}",
        f"${prev_payments:,.2f} ({prev_coll_ratio:.1f}%)",
        f"${prev_prod_day:,.2f}",
        f"${prev_prod_pat:,.2f}",
        str(prev_days),
        f"${prev_full_payments:,.2f}",
    ],
})
st.dataframe(fin_scorecard, use_container_width=True, hide_index=True, height=250)
st.caption("Month projection = Collections + scheduled treatments (rest of month)")

# ---------------------------------------------------------------------------
# 2. Treatment Plans (new patients)
# ---------------------------------------------------------------------------
st.subheader("2. Treatment Plans (new patients)")
if treatment_plans and np_patients:
    month_prefix = start_date.strftime("%Y-%m")
    patients_with_plan = set()
    for p in treatment_plans:
        pid = str(p.get("patient", {}).get("id") or "")
        created = str(p.get("createdAt") or "")
        if pid in np_patients and created.startswith(month_prefix):
            patients_with_plan.add(pid)
    converted = patients_with_plan & (np_with_more | future_np_patients)
    no_activity = patients_with_plan - converted
    no_plan = np_patients - patients_with_plan
    tp_rows = [
        {"Metric": "New patients", "Count": str(len(np_patients)), "%": "100%"},
        {"Metric": "With treatment plan", "Count": str(len(patients_with_plan)), "%": f"{100*len(patients_with_plan)/len(np_patients):.0f}%"},
        {"Metric": "→ Completed or scheduled", "Count": str(len(converted)), "%": f"{100*len(converted)/len(np_patients):.0f}%"},
        {"Metric": "→ No activity yet", "Count": str(len(no_activity)), "%": f"{100*len(no_activity)/len(np_patients):.0f}%"},
        {"Metric": "No treatment plan", "Count": str(len(no_plan)), "%": f"{100*len(no_plan)/len(np_patients):.0f}%"},
    ]
    st.dataframe(pd.DataFrame(tp_rows), use_container_width=True, hide_index=True, height=220)
else:
    st.info("Treatment plan data not available.")

# ---------------------------------------------------------------------------
# 3. Production by Provider (dollars from ledger)
# ---------------------------------------------------------------------------
st.subheader("3. Production by Provider")
if ledger:
    staff_by_id = {str(s.get("id")): f"{s.get('firstName','')} {s.get('lastName','')}".strip() or s.get("shortName") or str(s.get("id")) for s in staff}
    prov_prod = {}
    for entry in ledger:
        calc = entry.get("calculations") or {}
        prod = Decimal(str(calc.get("production") or 0))
        pay = Decimal(str(calc.get("payments") or 0))
        if prod == 0 and pay == 0:
            continue
        p2 = entry.get("provider2") or {}
        pid = str(p2.get("id") or "")
        name = staff_by_id.get(pid) or p2.get("kind") or "Unknown"
        if name not in prov_prod:
            prov_prod[name] = {"Production": Decimal(0), "Collections": Decimal(0)}
        prov_prod[name]["Production"] += prod
        prov_prod[name]["Collections"] += pay
    if prov_prod:
        total_prod = sum(v["Production"] for v in prov_prod.values())
        total_coll = sum(v["Collections"] for v in prov_prod.values())
        rows = []
        for name, vals in sorted(prov_prod.items(), key=lambda x: -x[1]["Production"]):
            pct_prod = (float(vals["Production"]) / float(total_prod) * 100) if total_prod else 0
            rows.append({
                "Provider": name,
                "Production": f"${vals['Production']:,.2f}",
                "% of Prod": f"{pct_prod:.1f}%",
                "Collections": f"${vals['Collections']:,.2f}",
            })
        rows.append({
            "Provider": "TOTAL",
            "Production": f"${total_prod:,.2f}",
            "% of Prod": "100%",
            "Collections": f"${total_coll:,.2f}",
        })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, height=min(450, 40 + 35 * len(rows)))
    else:
        st.info("No production data in ledger for this range.")
else:
    st.info("No ledger data.")

# ---------------------------------------------------------------------------
# 3. Scheduled Treatments (rest of month)
# ---------------------------------------------------------------------------
st.subheader("4. Scheduled Treatments (rest of month)")
st.caption(f"Looking from {future_start} to {future_end} · {len(future_appts)} appointments · {len(future_items)} items")
if future_items:
    appt_lookup = {str(row["id"]): row for _, row in future_df.iterrows()} if not future_df.empty else {}
    treat_rows = []
    by_prov = {}
    by_code = {}
    patients = set()
    total_treat = Decimal(0)
    for item in future_items:
        appt_id = str((item.get("appointment") or {}).get("id") or "")
        appt = appt_lookup.get(appt_id)
        cost = Decimal(str(item.get("amount") or item.get("cost") or 0))
        if cost == 0:
            continue
        total_treat += cost
        proc_name = (item.get("chargeableItem") or {}).get("name") or ""
        code = item.get("procedureCode") or "?"
        date_str = ""
        patient_id = ""
        prov_name = "Unknown"
        if appt is not None:
            date_str = str(appt.get("startTime") or "")[:16]
            patient_id = str(appt.get("patientId") or "")
            prov_name = appt.get("providerName") or "Unknown"
            if patient_id:
                patients.add(patient_id)
        by_prov[prov_name] = by_prov.get(prov_name, Decimal(0)) + cost
        if code not in by_code:
            by_code[code] = {"count": 0, "amount": Decimal(0), "name": proc_name}
        by_code[code]["count"] += 1
        by_code[code]["amount"] += cost
        treat_rows.append({
            "Date": date_str,
            "Provider": prov_name,
            "Patient ID": patient_id,
            "Procedure": proc_name,
            "Code": code,
            "Amount": f"${cost:,.2f}",
        })
    if treat_rows:
        st.markdown(f"**{len(patients)} patients** · **{len(treat_rows)} procedures** · **${total_treat:,.2f}** scheduled")
        # By provider
        prov_rows = []
        for prov, amt in sorted(by_prov.items(), key=lambda x: -x[1]):
            pct = float(amt) / float(total_treat) * 100 if total_treat else 0
            prov_rows.append({"Provider": prov, "Scheduled": f"${amt:,.2f}", "%": f"{pct:.1f}%"})
        prov_rows.append({"Provider": "TOTAL", "Scheduled": f"${total_treat:,.2f}", "%": "100%"})
        st.markdown("**By Provider**")
        st.dataframe(pd.DataFrame(prov_rows), use_container_width=True, hide_index=True, height=min(300, 40 + 35 * len(prov_rows)))
        # Detail
        with st.expander("Detail by appointment"):
            treat_rows.append({
                "Date": "", "Provider": "", "Patient ID": "",
                "Procedure": "TOTAL", "Code": "", "Amount": f"${total_treat:,.2f}",
            })
            st.dataframe(pd.DataFrame(treat_rows), use_container_width=True, hide_index=True, height=400)
    else:
        st.info("No scheduled treatments with cost for the rest of the month.")
else:
    st.info("No future appointment data.")

# ---------------------------------------------------------------------------
# 5. Accounts Receivable
# ---------------------------------------------------------------------------
st.subheader("5. Accounts Receivable")
# Load full month ledger for AR
try:
    month_start = end_date.replace(day=1).isoformat()
    ar_ledger = load_ledger(month_start, end_date.isoformat())
except Exception:
    ar_ledger = ledger
if ar_ledger:
    from collections import defaultdict
    prod_by_pat = defaultdict(lambda: Decimal(0))
    paid_by_pat = defaultdict(lambda: Decimal(0))
    adj_by_pat = defaultdict(lambda: Decimal(0))
    for e in ar_ledger:
        pid = str(e.get("patientId") or "")
        if not pid:
            continue
        calc = e.get("calculations") or {}
        prod_by_pat[pid] += Decimal(str(calc.get("production") or 0))
        paid_by_pat[pid] += Decimal(str(calc.get("payments") or 0))
        if e.get("transactionType") == "Adjustment":
            adj_by_pat[pid] += abs(Decimal(str(e.get("amount") or 0)))
    # Appointments in next 30 days
    try:
        next_30_end = (end_date + timedelta(days=30)).isoformat()
        next_30_appts = load_appointments((end_date + timedelta(days=1)).isoformat(), next_30_end)
        next_30_df = appts_to_df(next_30_appts)
    except Exception:
        next_30_df = pd.DataFrame()
    # Future appointments lookup (next 30 days)
    future_appt_by_pat = {}
    future_appt_sort = {}
    future_count_by_pat = {}
    if not next_30_df.empty:
        for _, row in next_30_df.iterrows():
            pid = str(row.get("patientId") or "")
            if pid:
                if pid not in future_appt_by_pat:
                    dt_str = str(row.get("startTime") or "")
                    if dt_str and len(dt_str) >= 16:
                        try:
                            from datetime import datetime as dt
                            dt_obj = dt.strptime(dt_str[:16], "%Y-%m-%d %H:%M")
                            hour = dt_obj.hour
                            ampm = "am" if hour < 12 else "pm"
                            hour_12 = hour if hour <= 12 else hour - 12
                            if hour_12 == 0:
                                hour_12 = 12
                            future_appt_by_pat[pid] = dt_obj.strftime(f"%a %b %d, {hour_12}{ampm}")
                            future_appt_sort[pid] = dt_str[:16]
                        except Exception:
                            future_appt_by_pat[pid] = dt_str[:16]
                            future_appt_sort[pid] = dt_str[:16]
                    else:
                        future_appt_by_pat[pid] = "—"
                future_count_by_pat[pid] = future_count_by_pat.get(pid, 0) + 1
    # Past appointments count (from current range)
    past_count_by_pat = {}
    if not df.empty:
        for _, row in df.iterrows():
            pid = str(row.get("patientId") or "")
            if pid and row.get("bucket") == "Completed":
                past_count_by_pat[pid] = past_count_by_pat.get(pid, 0) + 1
    pending_rows = []
    # Get patient names
    patient_names = {}
    for pid in set(prod_by_pat.keys()) | set(paid_by_pat.keys()):
        if prod_by_pat[pid] - paid_by_pat[pid] > 0:
            try:
                p = get_api()._get(f"patient/{pid}", cheetah=True)
                patient_names[pid] = f"{p.get('firstName','')} {p.get('lastName','')}".strip()
            except Exception:
                patient_names[pid] = ""
    for pid in set(prod_by_pat.keys()) | set(paid_by_pat.keys()):
        prod = prod_by_pat[pid]
        paid = paid_by_pat[pid]
        adj = adj_by_pat.get(pid, Decimal(0))
        balance = prod - paid - adj
        if balance > 0:
            next_appt = future_appt_by_pat.get(pid, "—")
            next_sort = future_appt_sort.get(pid, "9999")
            pending_rows.append({
                "Patient ID": pid,
                "Name": patient_names.get(pid, ""),
                "Production": f"${prod:,.2f}",
                "Paid": f"${paid:,.2f}",
                "Adj": f"${adj:,.2f}",
                "Balance": f"${balance:,.2f}",
                "Visits left": str(future_count_by_pat.get(pid, 0)),
                "Next appointment": next_appt,
                "_sort": next_sort,
            })
    if pending_rows:
        pending_rows.sort(key=lambda r: r.get("_sort", "9999"))
        for r in pending_rows:
            del r["_sort"]
        other_rows, missed_rows = group_missed_appointment_balances(pending_rows, ar_ledger)
        missed_total = sum(
            (Decimal(r["Balance"].replace("$", "").replace(",", "")) for r in missed_rows),
            Decimal(0),
        )
        st.markdown("#### Regular AR")
        regular_total = sum(
            (Decimal(r["Balance"].replace("$", "").replace(",", "")) for r in other_rows),
            Decimal(0),
        )
        regular_display = [dict(r) for r in other_rows]
        regular_summary = {
            "Patient ID": "", "Name": f"TOTAL · {len(other_rows)} patients",
            "Visits left": "", "Next appointment": "",
        }
        for column in ("Production", "Paid", "Adj", "Balance"):
            amount = sum(
                (Decimal(r[column].replace("$", "").replace(",", "")) for r in other_rows),
                Decimal(0),
            )
            regular_summary[column] = f"${amount:,.2f}"
        regular_display.append(regular_summary)
        st.dataframe(
            pd.DataFrame(regular_display),
            use_container_width=True,
            hide_index=True,
            height=min(600, 40 + 35 * len(regular_display)),
            column_order=["Patient ID", "Name", "Production", "Paid", "Adj",
                          "Balance", "Visits left", "Next appointment"],
            column_config={
                "Patient ID": st.column_config.TextColumn("ID"),
                "Visits left": st.column_config.TextColumn("Left", width="small"),
            },
        )
        st.markdown(f"**Total Regular AR: ${regular_total:,.2f}**")

        st.markdown("#### Missed Appointment · D9986")
        missed_display = [
            {
                "Patient ID": r["Patient ID"],
                "Name": r["Name"],
                "Code": "D9986",
                "Balance": r["Balance"],
                "Visits left": r["Visits left"],
                "Next appointment": r["Next appointment"],
            }
            for r in missed_rows
        ]
        missed_display.append({
            "Patient ID": "", "Name": f"TOTAL · {len(missed_rows)} patients",
            "Code": "D9986", "Balance": f"${missed_total:,.2f}",
            "Visits left": "", "Next appointment": "",
        })
        st.dataframe(
            pd.DataFrame(missed_display),
            use_container_width=True,
            hide_index=True,
            height=min(650, 40 + 35 * len(missed_display)),
            column_config={
                "Patient ID": st.column_config.TextColumn("ID"),
                "Visits left": st.column_config.TextColumn("Left", width="small"),
            },
        )
        st.markdown(f"**Total Missed Appointment: ${missed_total:,.2f}**")
        st.markdown(f"**Total AR: ${regular_total + missed_total:,.2f}**")
        total_pending = sum(Decimal(r["Balance"].replace("$","").replace(",","")) for r in pending_rows)
        no_appt = [r for r in other_rows if r.get("Visits left", "0") == "0"]
        no_appt_total = sum(Decimal(r["Balance"].replace("$","").replace(",","")) for r in no_appt)
        with_appt = [r for r in other_rows if r.get("Visits left", "0") != "0"]
        with_appt_total = sum(Decimal(r["Balance"].replace("$","").replace(",","")) for r in with_appt)
        col1, col2 = st.columns(2)
        with col1:
            st.caption(f"With appointments: {len(with_appt)} patients · ${with_appt_total:,.2f}")
            st.caption(f"No appointment: {len(no_appt)} patients · ${no_appt_total:,.2f}")
            st.caption(f"Missed Appointment: {len(missed_rows)} patients · ${missed_total:,.2f}")
            st.caption(f"**Total: {len(pending_rows)} patients · ${total_pending:,.2f}**")
        with col2:
            adj_by_type = {}
            for e in ar_ledger:
                if e.get("transactionType") == "Adjustment":
                    t = e.get("adjustmentType") or "Other"
                    pid = str(e.get("patientId") or "")
                    if t not in adj_by_type:
                        adj_by_type[t] = {"patients": set(), "amount": Decimal(0)}
                    adj_by_type[t]["patients"].add(pid)
                    adj_by_type[t]["amount"] += abs(Decimal(str(e.get("amount") or 0)))
            total_adj = sum(d["amount"] for d in adj_by_type.values())
            total_prod = sum(prod_by_pat.values())
            adj_pct = float(total_adj) / float(total_prod) * 100 if total_prod else 0
            for t, d in sorted(adj_by_type.items(), key=lambda x: -x[1]["amount"]):
                st.caption(f"{t}: {len(d['patients'])} patients · ${d['amount']:,.0f}")
            total_patients = len(set().union(*[d["patients"] for d in adj_by_type.values()])) if adj_by_type else 0
            st.caption(f"**Write-offs: {total_patients} patients · ${total_adj:,.2f} ({adj_pct:.1f}%)**")
        # Pie chart
        if with_appt_total > 0 or no_appt_total > 0 or missed_total > 0:
            import plotly.express as px
            pie_df = pd.DataFrame({
                "Category": ["With appointments", "No appointment", "Missed Appointment"],
                "Amount": [float(with_appt_total), float(no_appt_total), float(missed_total)],
            })
            fig = px.pie(pie_df, values="Amount", names="Category", 
                         color_discrete_sequence=["#2ecc71", "#e74c3c", "#f39c12"])
            fig.update_traces(texttemplate="$%{value:,.0f}<br>%{percent}")
            fig.update_layout(margin=dict(t=20, b=20, l=20, r=20), height=250, showlegend=True)
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No pending balances in this date range.")
else:
    st.info("No ledger data.")

st.markdown("---")
st.caption("Data from Curve Hero API. Production and payments from ledger entries. $49 = new patient fee invoices.")


