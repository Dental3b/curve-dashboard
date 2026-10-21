#!/usr/bin/env python3
"""
Daily summary script for Dental 3B
Runs at 4pm, checks last appointment, sends WhatsApp 15 min after
"""
import os
import requests
from datetime import datetime, timedelta
from decimal import Decimal

# Config from environment
CURVE_CLIENT_ID = os.environ.get("CURVE_CLIENT_ID")
CURVE_CLIENT_SECRET = os.environ.get("CURVE_CLIENT_SECRET")
CURVE_TENANT = "dentalthreeb"

TWILIO_SID = os.environ.get("TWILIO_SID")
TWILIO_TOKEN = os.environ.get("TWILIO_TOKEN")
TWILIO_FROM = "whatsapp:+14155238886"
TWILIO_TO = "whatsapp:+14049666951"

def get_curve_token():
    r = requests.post(
        "https://sso.curvehero.com/connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": CURVE_CLIENT_ID,
            "client_secret": CURVE_CLIENT_SECRET,
            "scope": "ledger_entry:read appointment:read"
        }
    )
    return r.json().get("access_token")

def get_today_data(token):
    h = {"Authorization": f"Bearer {token}"}
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    
    # Ledger
    r = requests.get(
        f"https://{CURVE_TENANT}.curvehero.com/cheetah/ledger_entry?startDate={today}&endDate={tomorrow}",
        headers=h, timeout=60
    )
    ledger = r.json().get("objects", []) if r.ok else []
    
    production = sum(Decimal(str((e.get("calculations") or {}).get("production") or 0)) for e in ledger)
    collections = sum(Decimal(str((e.get("calculations") or {}).get("payments") or 0)) for e in ledger)
    patients = set(str(e.get("patientId")) for e in ledger if e.get("patientId"))
    
    # Appointments - check if all are checked out
    r2 = requests.get(
        f"https://api.curvehero.com/v1/{CURVE_TENANT}/appointment",
        headers=h,
        params={"startDate": f"{today} 00:00:00", "endDate": f"{today} 23:59:59", "pageSize": 100},
        timeout=30
    )
    appts = r2.json().get("objects", []) if r2.ok else []
    pending = [a for a in appts if a.get("appointmentStatus") not in ("Check Out", "Missed", "Cancel", "Cancelled")]
    all_done = len(pending) == 0 and len(appts) > 0
    
    return production, collections, all_done, len(patients)

def send_whatsapp(message):
    requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json",
        data={"To": TWILIO_TO, "From": TWILIO_FROM, "Body": message},
        auth=(TWILIO_SID, TWILIO_TOKEN)
    )

def main():
    # Check if already sent today
    flag_file = "/tmp/summary_sent_today"
    today = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(flag_file):
        with open(flag_file) as f:
            if f.read().strip() == today:
                print("Already sent today")
                return
    
    token = get_curve_token()
    if not token:
        print("Failed to get Curve token")
        return
    
    production, collections, all_done, patients_seen = get_today_data(token)
    
    if not all_done:
        print("Not all patients checked out yet")
        return
    
    ratio = float(collections) / float(production) * 100 if production else 0
    today_str = datetime.now().strftime("%d %b %Y")
    
    message = f"""🦷 Dental 3B - Resumen del día
📅 {today_str}

Production: ${production:,.0f}
Collections: ${collections:,.0f} ({ratio:.1f}%)
Patients seen: {patients_seen}"""
    
    send_whatsapp(message)
    
    # Mark as sent
    with open(flag_file, "w") as f:
        f.write(today)
    print("Summary sent!")

if __name__ == "__main__":
    main()
