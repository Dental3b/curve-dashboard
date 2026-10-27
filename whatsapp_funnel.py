"""Optional authenticated WhatsApp funnel section for the existing dashboard."""
from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from whatsapp_webhook.funnel_report import report_rows, validate_report


def fetch_funnel_report(base_url: str, token: str, start: str, end: str, session=None):
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Invalid funnel service configuration")
    if not token:
        raise ValueError("Missing report credential")
    if session is None:
        import requests
        session = requests
    response = session.get(
        base_url.rstrip("/") + "/api/funnel",
        params={"start": start, "end": end},
        headers={"Authorization": "Bearer " + token},
        timeout=20,
        allow_redirects=False,
    )
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise RuntimeError("Funnel report could not be loaded")
    report = validate_report(response.json())
    if (report["start"], report["end"]) != (start, end):
        raise ValueError("Report does not match selected dates")
    return report


def render_whatsapp_funnel(st, secret, start_date, end_date):
    st.subheader("Embudo de WhatsApp")
    mode = st.radio("Periodo del embudo", ["Periodo seleccionado", "Hoy"], horizontal=True, key="wa_funnel_period")
    if mode == "Hoy":
        start_date = end_date = datetime.now(ZoneInfo("America/New_York")).date()
    st.caption(f"{start_date:%d/%m/%Y} al {end_date:%d/%m/%Y} · Hora de Atlanta")
    base_url = secret("WHATSAPP_FUNNEL_URL")
    token = secret("WHATSAPP_FUNNEL_REPORT_TOKEN")
    if not base_url or not token:
        st.info("El embudo está pendiente de conectar a sus reportes verificados.")
        return
    try:
        report = fetch_funnel_report(base_url, token, start_date.isoformat(), end_date.isoformat())
        if report is None:
            st.info("Todavía no hay un reporte verificado para estas fechas. No se muestra como cero.")
            return
        st.dataframe(report_rows(report), use_container_width=True, hide_index=True)
        as_of = datetime.fromisoformat(report["as_of"].replace("Z", "+00:00")).astimezone(ZoneInfo(report["timezone"]))
        st.caption(f"Datos verificados al {as_of:%d/%m/%Y %H:%M} · Porcentajes sobre personas que escribieron.")
        if report["quality"] == "minimum_verified":
            st.warning("Mínimo confirmado: el historial revisado no garantiza cubrir todos los contactos.")
        st.caption("Los pacientes existentes se separan primero. Las etapas de citas y pagos siguen a las mismas personas. Tratamiento realizado no equivale a aceptación formal verificada.")
    except Exception:
        # Never expose credential-bearing request details, patient data, or raw server errors.
        st.warning("No se pudo consultar el embudo. Los datos de Curve siguen disponibles; no se sustituyen los datos faltantes por ceros.")
