"""Chequeos determinísticos: expresiones que vencen, diff entre versiones y coincidencia textual."""
from __future__ import annotations

import difflib
import html
import re

DIAS = r"(lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bado|domingo)"
MESES = r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)"

PATTERNS: list[tuple[str, str]] = [
    ("Referencia relativa al día", r"\b(hoy|ayer|anoche|anteayer|mañana|pasado mañana|esta (mañana|tarde|noche)|esta jornada)\b"),
    ("Día de la semana", rf"\b(este|el pr[óo]ximo|el|este pasado)\s+{DIAS}\b"),
    ("Semana / fin de semana", r"\b(esta semana|la semana (que viene|pr[óo]xima|pasada)|este fin de semana|el fin de semana)\b"),
    ("Plazo que vence", r"\b(en las pr[óo]ximas (horas|d[íi]as|semanas)|en los pr[óo]ximos d[íi]as|a la brevedad|en breve|por estas horas|en este momento|a esta hora)\b"),
    ("Hecho pendiente", r"\b(se espera que|todo indica que|podr[íi]a|estar[íi]a|ser[íi]a|define|definir[áa]|se definir[áa]|resta confirmar|a confirmar|aguarda|aguardan|restan? (los )?detalles)\b"),
    ("Fecha concreta", rf"\b\d{{1,2}} de {MESES}\b"),
    ("Horario", r"\b(a las|desde las)\s+\d{1,2}(([:.]\d{2})|\s*h)?\b"),
    ("Edad", r"\b(de|con) \d{2} años\b"),
    ("Posición / tabla", r"\b(l[íi]der|escolta|colista|en la tabla|puntos? de diferencia|está? (a|en zona)|zona de (descenso|copas|clasificaci[óo]n))\b"),
    ("Próximo partido", r"\b(pr[óo]ximo (rival|partido|compromiso)|recibir[áa]|visitar[áa]|enfrentar[áa]|jugar[áa])\b"),
    ("Racha / cifra acumulada", r"\b(lleva|acumula|suma|van)\s+\d+\b|\b\d+ partidos (sin|con)\b"),
]


def temporal_flags(paragraphs: list[str]) -> list[dict]:
    out = []
    for i, p in enumerate(paragraphs):
        for label, pat in PATTERNS:
            for m in re.finditer(pat, p, re.I):
                a, b = max(0, m.start() - 70), min(len(p), m.end() + 70)
                out.append({"párrafo": f"P{i + 1}", "tipo": label, "expresión": m.group(0),
                            "contexto": ("…" if a else "") + p[a:b] + ("…" if b < len(p) else "")})
    return out


def diff_html(old: str, new: str) -> str:
    """Diff por palabras con marcas de agregado/eliminado para mostrar en Streamlit."""
    a, b = re.findall(r"\S+|\n+", old), re.findall(r"\S+|\n+", new)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(html.escape(" ".join(a[i1:i2])))
        if op in ("delete", "replace"):
            out.append(f"<del>{html.escape(' '.join(a[i1:i2]))}</del>")
        if op in ("insert", "replace"):
            out.append(f"<ins>{html.escape(' '.join(b[j1:j2]))}</ins>")
    joined = re.sub(r"\s*(\n\s*)+", "<br><br>", " ".join(out))
    return joined


def _ngrams(text: str, n: int = 6) -> set[tuple[str, ...]]:
    w = re.findall(r"\w+", text.lower())
    return {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}


def overlap(draft: str, source: str, n: int = 6) -> tuple[float, list[str]]:
    """Porcentaje de secuencias de 6 palabras del borrador que aparecen textuales en la fuente."""
    d, s = _ngrams(draft, n), _ngrams(source, n)
    if not d:
        return 0.0, []
    common = d & s
    # reconstruye tramos copiados legibles
    words = re.findall(r"\w+", draft.lower())
    spans, cur = [], []
    for i in range(len(words) - n + 1):
        if tuple(words[i:i + n]) in common:
            cur = cur or words[i:i + n - 1]
            cur.append(words[i + n - 1])
        elif cur:
            spans.append(" ".join(cur)); cur = []
    if cur:
        spans.append(" ".join(cur))
    return round(100 * len(common) / len(d), 1), spans[:8]
