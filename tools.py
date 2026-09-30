"""Las herramientas de la mesa. Cada una devuelve un dict listo para mostrar + metadatos del modelo."""
from __future__ import annotations

import re

from . import llm, prompts, search
from .config import now, today_str
from .fetch import Article, check_links
from .textutils import overlap, temporal_flags


def _meta(res: llm.Result) -> dict:
    return {"proveedor": res.provider, "modelo": res.model, "fallos_previos": res.errors}


def _list(v) -> list:
    return v if isinstance(v, list) else ([] if v in (None, "") else [v])


def _pidx(ref: str) -> int | None:
    m = re.search(r"(\d+)", str(ref or ""))
    return int(m.group(1)) if m else None


# 1 ─ Actualizar con dato nuevo ────────────────────────────────────────────────
def apply_changes(paragraphs: list[str], cambios: list[dict]) -> list[str]:
    """Aplica los cambios propuestos de forma determinística (el modelo no reescribe la nota entera)."""
    replace, delete, insert = {}, set(), {}
    for c in cambios:
        i = _pidx(c.get("parrafo"))
        if i is None or i > len(paragraphs):
            continue
        acc = str(c.get("accion", "")).lower()
        nuevo = str(c.get("nuevo") or "").strip()
        if acc.startswith("elim"):
            delete.add(i)
        elif acc.startswith("reempl") and nuevo and i >= 1:
            replace[i] = nuevo
        elif acc.startswith("insert") and nuevo:
            insert.setdefault(i, []).append(nuevo)
    out = list(insert.get(0, []))
    for i, p in enumerate(paragraphs, start=1):
        if i not in delete:
            out.append(replace.get(i, p))
        out.extend(insert.get(i, []))
    return out


def update_with_fact(art: Article, fact: str, source: str, mode: str) -> dict:
    user = (f"{art.as_prompt()}\n\nDATO NUEVO:\n{fact}\n\nFUENTE DEL DATO: {source or '(no indicada)'}")
    res = llm.run(prompts.UPDATE, user, mode=mode, tier="strong", max_tokens=3000)
    d = res.data
    cambios = [c for c in _list(d.get("cambios")) if isinstance(c, dict)]
    new_paras = apply_changes(art.paragraphs, cambios)
    stamp = now().strftime("%H:%M")
    return {"impacto": d.get("impacto", "medio"), "resumen": d.get("resumen", ""),
            "titulo": d.get("titulo_nuevo") or art.title, "bajada": d.get("bajada_nueva") or art.deck,
            "linea": f"Actualización {stamp} · {d.get('linea_actualizacion', '')}".strip(" ·"),
            "cambios": cambios, "alertas": _list(d.get("alertas")),
            "texto_nuevo": "\n\n".join(new_paras), "texto_original": "\n\n".join(art.paragraphs),
            "meta": _meta(res)}


# 2 ─ Buscar novedades ─────────────────────────────────────────────────────────
def find_news(art: Article, mode: str) -> dict:
    q = llm.run(prompts.QUERIES, art.as_prompt(4000), mode=mode, tier="fast", max_tokens=600)
    queries = [s for s in _list(q.data.get("queries")) if isinstance(s, str)][:3] or [art.title]
    results, engine, search_err = [], "DuckDuckGo/Bing", ""
    try:
        results = search.news(queries, art.published, exclude_url=art.url)
    except Exception as e:  # noqa: BLE001
        search_err = str(e)
    if not results:
        try:
            since = art.published.strftime("%d/%m/%Y %H:%M") if art.published else "los últimos días"
            _, results = llm.groq_compound(
                f"Buscá noticias publicadas después de {since} sobre: {q.data.get('tema') or art.title}. "
                f"Protagonistas: {', '.join(_list(q.data.get('protagonistas')))}. Priorizá medios argentinos. "
                "Listá qué hechos nuevos hay, con su fuente.")
            engine = "Groq compound"
        except Exception as e:  # noqa: BLE001
            search_err += f" | {e}"
    if not results:
        return {"hay_novedades": False, "resumen": "La búsqueda no devolvió resultados posteriores a la nota.",
                "queries": queries, "fuentes": {}, "motor": engine, "error_busqueda": search_err.strip(" |"),
                "meta": _meta(q)}
    fuentes = {f"F{i + 1}": r for i, r in enumerate(results)}
    listing = "\n\n".join(f"[{k}] {r['source']} · {r['date'] or 'sin fecha'}\n{r['title']}\n{r['body'][:700]}"
                          for k, r in fuentes.items())
    res = llm.run(prompts.NEWS, f"NOTA PUBLICADA:\n{art.as_prompt(6000)}\n\nRESULTADOS DE BÚSQUEDA:\n{listing}",
                  mode=mode, tier="strong", max_tokens=2500)
    d = res.data
    return {"hay_novedades": bool(d.get("hay_novedades")) and bool(_list(d.get("novedades"))),
            "resumen": d.get("resumen", ""), "novedades": _list(d.get("novedades")),
            "contradicciones": _list(d.get("contradicciones")), "sugerencia": d.get("sugerencia") or {},
            "queries": queries, "fuentes": fuentes, "motor": engine, "meta": _meta(res)}


# 3 ─ Chequeo de vencimiento ───────────────────────────────────────────────────
def expiry_check(art: Article, mode: str, links: bool = True) -> dict:
    flags = temporal_flags(art.paragraphs)
    age = ""
    if art.published:
        h = (now() - art.published).total_seconds() / 3600
        age = f"{h:.0f} horas" if h < 48 else f"{h / 24:.0f} días"
    flag_txt = "\n".join(f"- {f['párrafo']} [{f['tipo']}] «{f['expresión']}»" for f in flags[:60]) or "(ninguna)"
    user = (f"{art.as_prompt()}\n\nANTIGÜEDAD DE LA NOTA: {age or 'desconocida (sin fecha de publicación)'}\n"
            f"HOY: {today_str()}\n\nEXPRESIONES DETECTADAS AUTOMÁTICAMENTE:\n{flag_txt}")
    res = llm.run(prompts.EXPIRY, user, mode=mode, tier="strong", max_tokens=2500)
    d = res.data
    broken = check_links(art.links) if links and art.links else []
    return {"estado": d.get("estado", "revisar"), "resumen": d.get("resumen", ""), "antiguedad": age,
            "items": [i for i in _list(d.get("items")) if isinstance(i, dict)],
            "titulo_ok": d.get("titulo_ok", True), "titulo_sugerido": d.get("titulo_sugerido", ""),
            "flags": flags, "links_rotos": broken, "links_revisados": min(len(art.links), 25), "meta": _meta(res)}


# 4 ─ Nota de seguimiento ──────────────────────────────────────────────────────
def follow_up(art: Article, mode: str, news: dict | None = None) -> dict:
    extra = ""
    if news and news.get("novedades"):
        extra = "\n\nNOVEDADES DETECTADAS DESPUÉS DE LA PUBLICACIÓN:\n" + "\n".join(
            f"- {n.get('hecho')} ({', '.join(_list(n.get('fuentes')))})" for n in news["novedades"] if isinstance(n, dict))
    res = llm.run(prompts.FOLLOW, art.as_prompt() + extra, mode=mode, tier="strong", max_tokens=2500)
    d = res.data
    return {"que_viene": _list(d.get("que_viene")), "preguntas": _list(d.get("preguntas")),
            "datos_a_verificar": _list(d.get("datos_a_verificar")), "angulos": _list(d.get("angulos")),
            "esqueleto": d.get("esqueleto") or {}, "meta": _meta(res)}


# 5 ─ Nuestra versión (nota de la competencia) ─────────────────────────────────
def our_version(art: Article, mode: str) -> dict:
    res = llm.run(prompts.OURS, art.as_prompt(), mode=mode, tier="strong", max_tokens=3500)
    d = res.data
    borrador = [p for p in _list(d.get("borrador")) if isinstance(p, str)]
    if not borrador and isinstance(d.get("borrador"), str):
        borrador = [p for p in d["borrador"].split("\n") if p.strip()]
    pct, spans = overlap(" ".join(borrador), art.text)
    return {"resumen": d.get("resumen", ""), "hechos": _list(d.get("hechos")),
            "confirmar_antes": _list(d.get("confirmar_antes")), "angulos": _list(d.get("angulos")),
            "como_atribuir": d.get("como_atribuir", ""), "titulo": d.get("titulo", ""), "bajada": d.get("bajada", ""),
            "borrador": borrador, "coincidencia": pct, "tramos_copiados": spans, "meta": _meta(res)}


# 6 ─ Comparar versiones ───────────────────────────────────────────────────────
def compare(arts: list[Article], mode: str) -> dict:
    per = max(2500, 18000 // max(1, len(arts)))
    labels = {f"M{i + 1}": a for i, a in enumerate(arts)}
    blocks = "\n\n=====\n\n".join(f"[{k}]\n{a.as_prompt(per)}" for k, a in labels.items())
    res = llm.run(prompts.COMPARE, blocks, mode=mode, tier="strong", max_tokens=3500)
    d = res.data
    timeline = sorted(
        [{"medio": k, "sitio": a.site, "titulo": a.title, "publicada": a.published} for k, a in labels.items()],
        key=lambda x: (x["publicada"] is None, x["publicada"] or now()))
    return {"sintesis": d.get("sintesis", ""), "matriz": [m for m in _list(d.get("matriz")) if isinstance(m, dict)],
            "contradicciones": _list(d.get("contradicciones")), "exclusivos": _list(d.get("exclusivos")),
            "que_usar": d.get("que_usar", ""), "medios": {k: a.site for k, a in labels.items()},
            "timeline": timeline, "meta": _meta(res)}
