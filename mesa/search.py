"""Búsqueda de novedades posteriores a una nota. Sin clave: DuckDuckGo/Bing news vía ddgs. Respaldo: Groq compound."""
from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse

from .config import now
from .fetch import _parse_date


def _timelimit(since: datetime | None) -> str:
    if not since:
        return "w"
    days = (now() - since).days
    return "d" if days < 1 else "w" if days < 7 else "m" if days < 31 else "y"


def news(queries: list[str], since: datetime | None, exclude_url: str = "", max_per_query: int = 8) -> list[dict]:
    from ddgs import DDGS

    out, seen = [], set()
    tl = _timelimit(since)
    ex_host = urlparse(exclude_url).path if exclude_url else ""
    with DDGS() as d:
        for q in queries[:3]:
            for kind in ("news", "text"):
                try:
                    fn = d.news if kind == "news" else d.text
                    res = fn(q, region="ar-es", safesearch="off", timelimit=tl, max_results=max_per_query)
                except Exception:
                    res = []
                for r in res or []:
                    url = r.get("url") or r.get("href") or ""
                    if not url or url in seen or (ex_host and urlparse(url).path == ex_host):
                        continue
                    seen.add(url)
                    date = _parse_date(r.get("date")) if r.get("date") else None
                    out.append({"title": r.get("title", ""), "url": url, "body": r.get("body", ""),
                                "source": r.get("source") or (urlparse(url).hostname or "").replace("www.", ""),
                                "date": date.strftime("%d/%m/%Y %H:%M") if date else "", "_dt": date})
                if res and kind == "news":
                    break  # si news trajo resultados, no hace falta la búsqueda general
    if since:
        # descarta lo que es claramente anterior a la nota (las que no traen fecha se conservan)
        out = [r for r in out if not r["_dt"] or r["_dt"] >= since]
    out.sort(key=lambda r: r["_dt"] or datetime.min.replace(tzinfo=now().tzinfo), reverse=True)
    for r in out:
        r.pop("_dt", None)
    return out[:14]
