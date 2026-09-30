"""Lectura de notas desde URL: título, fechas, texto por párrafos y enlaces del cuerpo."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .config import TZ

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "es-AR,es;q=0.9", "Accept": "text/html,application/xhtml+xml"}


@dataclass
class Article:
    url: str
    title: str = ""
    deck: str = ""
    text: str = ""
    published: datetime | None = None
    modified: datetime | None = None
    site: str = ""
    links: list[str] = field(default_factory=list)
    via: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.text) and not self.error

    @property
    def paragraphs(self) -> list[str]:
        return [p.strip() for p in re.split(r"\n\s*\n|\n", self.text) if p.strip()]

    def as_prompt(self, limit: int = 9000) -> str:
        pub = self.published.strftime("%d/%m/%Y %H:%M") if self.published else "desconocida"
        mod = self.modified.strftime("%d/%m/%Y %H:%M") if self.modified else "—"
        body = "\n\n".join(f"[P{i + 1}] {p}" for i, p in enumerate(self.paragraphs))
        if len(body) > limit:
            body = body[:limit] + "\n[…recortado]"
        return (f"MEDIO: {self.site}\nURL: {self.url}\nPUBLICADA: {pub}\nMODIFICADA: {mod}\n"
                f"TÍTULO: {self.title}\nBAJADA: {self.deck}\n\nCUERPO:\n{body}")


def _parse_date(v: str | None) -> datetime | None:
    if not v:
        return None
    v = v.strip()
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00")) if fmt is None else datetime.strptime(v, fmt)
            return d.astimezone(TZ) if d.tzinfo else d.replace(tzinfo=TZ)
        except Exception:
            continue
    return None


def _jsonld(soup: BeautifulSoup) -> dict:
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except Exception:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for it in items:
            if isinstance(it, dict) and ("Article" in str(it.get("@type", "")) or it.get("datePublished")):
                return it
    return {}


def parse_html(url: str, html: str) -> Article:
    soup = BeautifulSoup(html, "lxml")
    meta = lambda **kw: (soup.find("meta", attrs=kw) or {}).get("content", "")  # noqa: E731
    ld = _jsonld(soup)
    art = Article(url=url, site=urlparse(url).hostname or "")
    art.site = (meta(property="og:site_name") or art.site).replace("www.", "")
    art.title = (meta(property="og:title") or ld.get("headline") or (soup.title.string if soup.title else "") or "").strip()
    art.deck = (meta(name="description") or meta(property="og:description") or ld.get("description") or "").strip()
    art.published = _parse_date(meta(property="article:published_time") or ld.get("datePublished")
                                or meta(name="date") or meta(itemprop="datePublished"))
    art.modified = _parse_date(meta(property="article:modified_time") or ld.get("dateModified"))

    text = ""
    try:
        import trafilatura

        text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False,
                                   favor_precision=True) or ""
        if not art.published:
            md = trafilatura.extract_metadata(html)
            if md and md.date:
                art.published = _parse_date(md.date)
    except Exception:
        pass
    body_node = soup.find("article") or soup.find("main") or soup.body
    if not text and body_node:
        for t in body_node(["script", "style", "noscript", "aside", "nav", "footer", "form", "figure"]):
            t.decompose()
        paras = [re.sub(r"\s+", " ", p.get_text(" ")).strip() for p in body_node.find_all(["p", "h2", "h3"])]
        text = "\n\n".join(p for p in paras if len(p) > 40)
    art.text = text.strip()

    if body_node:
        host = urlparse(url).hostname
        seen = set()
        for a in body_node.find_all("a", href=True):
            href = urljoin(url, a["href"]).split("#")[0]
            if href.startswith("http") and href != url and href not in seen:
                seen.add(href)
                art.links.append(href)
        # primero los enlaces internos del cuerpo, que son los que suelen quedar rotos
        art.links.sort(key=lambda h: urlparse(h).hostname != host)
    return art


def _via_jina(url: str) -> Article:
    r = requests.get(f"https://r.jina.ai/{url}", headers={"Accept": "text/plain", "X-Return-Format": "markdown"}, timeout=40)
    r.raise_for_status()
    raw = r.text
    art = Article(url=url, site=(urlparse(url).hostname or "").replace("www.", ""), via="lector alternativo")
    m = re.search(r"^Title:\s*(.+)$", raw, re.M)
    art.title = m.group(1).strip() if m else ""
    m = re.search(r"^Published Time:\s*(.+)$", raw, re.M)
    art.published = _parse_date(m.group(1)) if m else None
    body = raw.split("Markdown Content:", 1)[-1]
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
    art.links = list(dict.fromkeys(re.findall(r"\]\((https?://[^)\s]+)\)", body)))[:60]
    body = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body)
    paras = [re.sub(r"^[#>*\-\s]+", "", p).strip() for p in body.split("\n")]
    art.text = "\n\n".join(p for p in paras if len(p) > 40)
    return art


def fetch(url: str) -> Article:
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    first_error = ""
    try:
        r = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
        if r.ok and "html" in r.headers.get("content-type", "html"):
            r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else r.apparent_encoding
            art = parse_html(r.url, r.text)
            art.via = "directo"
            if len(art.text) > 300:
                return art
            first_error = "no se encontró el cuerpo de la nota"
        else:
            first_error = f"HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        first_error = type(e).__name__
    try:
        art = _via_jina(url)
        if len(art.text) > 300:
            return art
    except Exception:
        pass
    return Article(url=url, error=f"No se pudo leer la nota ({first_error}). Pegá el texto manualmente.")


def from_text(text: str, title: str = "", published: datetime | None = None, url: str = "") -> Article:
    lines = [l for l in text.strip().split("\n")]
    if not title and lines:
        title = lines[0].strip()
        text = "\n".join(lines[1:])
    return Article(url=url or "(texto pegado)", title=title, text=text.strip(), published=published,
                   site=(urlparse(url).hostname or "texto pegado").replace("www.", ""), via="texto pegado")


def check_links(links: list[str], limit: int = 25) -> list[dict]:
    """Chequea enlaces del cuerpo en paralelo. Devuelve sólo los problemáticos."""
    from concurrent.futures import ThreadPoolExecutor

    def one(u: str):
        try:
            r = requests.head(u, headers=HEADERS, timeout=8, allow_redirects=True)
            if r.status_code in (403, 405, 400):
                r = requests.get(u, headers=HEADERS, timeout=10, allow_redirects=True, stream=True)
            return {"url": u, "status": r.status_code, "final": r.url}
        except Exception as e:  # noqa: BLE001
            return {"url": u, "status": 0, "final": "", "error": type(e).__name__}

    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(one, links[:limit]))
    return [r for r in res if r["status"] == 0 or r["status"] >= 400]
