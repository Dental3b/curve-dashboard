"""Curve Hero API client."""
from __future__ import annotations

import os
import time
from typing import Any

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

TOKEN_URL = "https://sso.curvehero.com/connect/token"
DEFAULT_SCOPES = (
    "appointment:read patient:read clinic:read staff:read schedule:read "
    "appointment_tag:read chargeable_item:read insurance:read"
)


def _get_secret(key: str, default: str = "") -> str:
    """Read from Streamlit secrets (cloud) or environment variables (local)."""
    try:
        import streamlit as st
        if hasattr(st, "secrets") and key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        pass
    return os.getenv(key, default)


class CurveAPI:
    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        instance: str | None = None,
    ) -> None:
        self.client_id = client_id or _get_secret("CURVE_CLIENT_ID")
        self.client_secret = client_secret or _get_secret("CURVE_CLIENT_SECRET")
        self.instance = instance or _get_secret("CURVE_INSTANCE", "dentalthreeb")
        self.base_url = f"https://api.curvehero.com/v1/{self.instance}"
        self._token: str | None = None
        self._token_expires_at: float = 0

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        resp = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": DEFAULT_SCOPES,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expires_at = time.time() + int(data.get("expires_in", 3600))
        return self._token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._get_token()}"}

    def _get(self, path: str, params: dict | None = None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = requests.get(url, headers=self._headers(), params=params, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def _get_all_pages(self, path: str, params: dict | None = None, max_pages: int = 20) -> list:
        """Follow meta.next pagination until exhausted or max_pages."""
        params = dict(params or {})
        objects: list = []
        page = 0
        data = self._get(path, params)
        if isinstance(data, list):
            return data
        objects.extend(data.get("objects") or [])
        while data.get("meta", {}).get("next") and page < max_pages:
            page += 1
            next_url = data["meta"]["next"]
            if "pageToken=" in next_url:
                token = next_url.split("pageToken=")[-1].split("&")[0]
                params = {**params, "pageToken": token}
            else:
                break
            data = self._get(path, params)
            objects.extend(data.get("objects") or [])
        return objects

    # --- Resources ---

    def get_clinics(self) -> list[dict]:
        data = self._get("clinic")
        if isinstance(data, list):
            return data
        return data.get("objects") or []

    def get_staff(self) -> list[dict]:
        data = self._get("staff")
        if isinstance(data, list):
            return data
        return data.get("objects") or []

    def get_operatories(self, clinic_id: int | str = 1) -> list[dict]:
        try:
            data = self._get(f"clinic/{clinic_id}/operatory")
            if isinstance(data, list):
                return data
            return data.get("objects") or []
        except Exception:
            return []

    def get_appointments(
        self,
        start,
        end,
        statuses: str = "active,check in,check out,missed,cancel",
        page_size: int = 500,
    ) -> list[dict]:
        params = {
            "startDate": start.strftime("%Y-%m-%d %H:%M:%S"),
            "endDate": end.strftime("%Y-%m-%d %H:%M:%S"),
            "statuses": statuses,
            "pageSize": page_size,
        }
        return self._get_all_pages("appointment", params)

    def get_patients(self, status: str | None = None, changed_since: str | None = None) -> list[dict]:
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        if changed_since:
            params["changedSince"] = changed_since
        return self._get_all_pages("patient", params, max_pages=10)

    def get_appointment_tags(self) -> list[dict]:
        try:
            data = self._get("appointment_tag")
            if isinstance(data, list):
                return data
            return data.get("objects") or []
        except Exception:
            return []
