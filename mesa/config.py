"""Lectura de claves y configuración: st.secrets primero, variables de entorno después."""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def secret(name: str, default: str = "") -> str:
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:
        pass
    return os.environ.get(name, default).strip() or default


def now() -> datetime:
    return datetime.now(TZ)


def today_str() -> str:
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
             "septiembre", "octubre", "noviembre", "diciembre"]
    n = now()
    return f"{dias[n.weekday()]} {n.day} de {meses[n.month - 1]} de {n.year}, {n:%H:%M} (hora argentina)"
