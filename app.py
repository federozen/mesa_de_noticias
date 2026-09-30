"""Mesa · Notas vivas — herramientas de redacción a partir de un link."""
from __future__ import annotations

import re
from datetime import datetime, time as dtime

import pandas as pd
import streamlit as st

from mesa import llm, tools
from mesa.config import TZ, now, today_str
from mesa.fetch import Article, fetch, from_text
from mesa.textutils import diff_html

st.set_page_config(page_title="Mesa · Notas vivas", page_icon="📰", layout="wide")

st.markdown("""
<style>
del{background:#ffd9d3;color:#8a1c10;text-decoration:line-through}
ins{background:#dcf5c4;color:#1d5b13;text-decoration:none}
.diff{font-family:Georgia,serif;font-size:17px;line-height:1.6;padding:14px 18px;border:1px solid #ddd;border-radius:8px;background:#fffdf7;color:#171817}
.pill{display:inline-block;padding:2px 10px;border-radius:99px;font-size:12px;font-weight:700;margin-right:6px}
.alta{background:#ffd9d3}.media{background:#ffeab4}.baja{background:#e4f0e4}
.vigente{background:#dcf5c4}.revisar{background:#ffeab4}.vencida{background:#ffd9d3}
.meta{color:#777;font-size:12px}
</style>""", unsafe_allow_html=True)


# ── Utilidades de UI ─────────────────────────────────────────────────────────
@st.cache_data(ttl=600, show_spinner=False)
def cached_fetch(url: str) -> Article:
    return fetch(url)


def pill(txt: str, cls: str = "") -> str:
    return f'<span class="pill {cls or str(txt).lower()}">{txt}</span>'


def meta_line(m: dict):
    txt = f"Modelo: {m.get('proveedor')} · {m.get('modelo')}"
    if m.get("fallos_previos"):
        txt += f" · respaldo tras {len(m['fallos_previos'])} fallo(s)"
    st.markdown(f'<p class="meta">{txt}</p>', unsafe_allow_html=True)
    if m.get("fallos_previos"):
        with st.expander("Ver fallos de proveedores"):
            for e in m["fallos_previos"]:
                st.caption(e)


def copy_block(label: str, text: str):
    if text:
        st.caption(label)
        st.code(text, language=None, wrap_lines=True)


def fmt_dt(d: datetime | None) -> str:
    return d.strftime("%d/%m/%Y %H:%M") if d else "sin fecha"


def article_card(a: Article):
    age = ""
    if a.published:
        h = (now() - a.published).total_seconds() / 3600
        age = f" · hace {h:.0f} h" if h < 48 else f" · hace {h / 24:.0f} días"
    st.markdown(f"**{a.title or '(sin título)'}**  \n"
                f"<span class='meta'>{a.site} · publicada {fmt_dt(a.published)}{age} · "
                f"{len(a.paragraphs)} párrafos · lectura: {a.via}</span>", unsafe_allow_html=True)
    with st.expander("Ver texto leído"):
        if a.deck:
            st.markdown(f"*{a.deck}*")
        for i, p in enumerate(a.paragraphs, 1):
            st.markdown(f"<span class='meta'>P{i}</span> {p}", unsafe_allow_html=True)


def src_links(ids, fuentes: dict) -> str:
    out = []
    for i in ids or []:
        f = fuentes.get(str(i).strip())
        out.append(f"[{i} · {f['source']}]({f['url']})" if f else str(i))
    return ", ".join(out)


# ── Barra lateral ────────────────────────────────────────────────────────────
status = llm.provider_status()
with st.sidebar:
    st.header("Modelos")
    has_free = any(v for k, v in status.items() if k != "Claude")
    opts = {"Gratis": "gratis"}
    if status.get("Claude"):
        opts = {"Gratis, y Claude si todo falla": "gratis+premium", "Gratis": "gratis", "Claude primero (premium)": "premium"}
    mode = opts[st.radio("Modo", list(opts), label_visibility="collapsed")]
    st.caption("Orden de respaldo: Groq → Cerebras → OpenRouter → Gemini" + (" → Claude" if status.get("Claude") else ""))
    for name, ok in status.items():
        st.markdown(f"{'🟢' if ok else '⚪'} {name}" + ("" if ok else " <span class='meta'>(sin clave)</span>"),
                    unsafe_allow_html=True)
    if not any(status.values()):
        st.error("No hay claves cargadas. Configurá Secrets (ver README).")
    st.divider()
    st.caption(f"Hoy: {today_str()}")

st.title("Mesa · Notas vivas")
st.caption("Pegá un link y elegí qué hacer: actualizar, buscar novedades, chequear vencimientos o preparar el seguimiento. "
           "Para notas de otros medios: armar nuestra versión o comparar coberturas.")

tab_own, tab_other = st.tabs(["📝  Mi nota", "🔎  Otros medios"])

# ════════════════════════════════════════════════════════════════════════════
# MI NOTA
# ════════════════════════════════════════════════════════════════════════════
with tab_own:
    url = st.text_input("Link de la nota", placeholder="https://www.ole.com.ar/…", key="own_url")
    with st.expander("¿El link no carga? Pegá el texto"):
        pasted = st.text_area("Título en la primera línea, después el cuerpo", height=180, key="own_text")
        c1, c2 = st.columns(2)
        pdate = c1.date_input("Fecha de publicación", value=now().date(), format="DD/MM/YYYY", key="own_date")
        ptime = c2.time_input("Hora", value=dtime(9, 0), key="own_time")

    st.markdown("**¿Qué hacemos con la nota?**")
    c1, c2, c3, c4 = st.columns(4)
    do_update = c1.checkbox("✏️ Actualizar", help="Aplica un dato nuevo a la nota cambiando lo mínimo, con los cambios marcados")
    do_news = c2.checkbox("📡 Novedades", value=True, help="Busca qué pasó después de la publicación, con fuentes")
    do_expiry = c3.checkbox("⏳ Vencimiento", value=True, help="Marca lo que envejeció: fechas relativas, tabla, rachas, enlaces rotos")
    do_follow = c4.checkbox("🧭 Seguimiento", help="Qué viene, preguntas para las fuentes y esqueleto de la próxima nota")

    fact, fact_src, chk_links = "", "", True
    if do_update:
        fact = st.text_area("Dato nuevo", placeholder="Ej.: El club confirmó que la lesión es un desgarro y estará 3 semanas afuera.",
                            key="fact")
        fact_src = st.text_input("Fuente del dato (opcional)", placeholder="Parte médico oficial / fuente propia / conferencia…")
    if do_expiry:
        chk_links = st.toggle("Chequear también enlaces rotos", value=True)

    if st.button("Analizar nota →", type="primary", disabled=not (url or pasted)):
        if do_update and not fact.strip():
            st.warning("Escribí el dato nuevo para actualizar la nota.")
            st.stop()
        if not any([do_update, do_news, do_expiry, do_follow]):
            st.warning("Elegí al menos una acción.")
            st.stop()
        if pasted.strip():
            art = from_text(pasted, published=datetime.combine(pdate, ptime, tzinfo=TZ), url=url)
        else:
            with st.spinner("Leyendo la nota…"):
                art = cached_fetch(url)
        if not art.ok:
            st.error(art.error or "No se pudo leer la nota.")
            st.stop()
        results: dict = {"article": art}
        with st.status("Trabajando…", expanded=True) as s:
            steps = [("update", do_update, "Actualizando con el dato nuevo", lambda: tools.update_with_fact(art, fact, fact_src, mode)),
                     ("news", do_news, "Buscando novedades posteriores", lambda: tools.find_news(art, mode)),
                     ("expiry", do_expiry, "Chequeando vencimientos", lambda: tools.expiry_check(art, mode, chk_links)),
                     ("follow", do_follow, "Preparando el seguimiento", lambda: tools.follow_up(art, mode, results.get("news")))]
            for key, on, label, fn in steps:
                if not on:
                    continue
                st.write(label + "…")
                try:
                    results[key] = fn()
                except Exception as e:  # noqa: BLE001
                    results[key] = {"error": str(e)}
            s.update(label="Listo", state="complete", expanded=False)
        st.session_state["own"] = results

    R = st.session_state.get("own")
    if R:
        st.divider()
        article_card(R["article"])
        names = {"update": "✏️ Actualización", "news": "📡 Novedades", "expiry": "⏳ Vencimiento", "follow": "🧭 Seguimiento"}
        keys = [k for k in names if k in R]
        for tab, key in zip(st.tabs([names[k] for k in keys]), keys):
            with tab:
                r = R[key]
                if "error" in r:
                    st.error(r["error"])
                    continue

                if key == "update":
                    st.markdown(pill(f"Impacto {r['impacto']}", str(r["impacto"]).lower()) + f" {r['resumen']}",
                                unsafe_allow_html=True)
                    for a in r["alertas"]:
                        st.warning(a)
                    copy_block("Título", r["titulo"])
                    copy_block("Bajada", r["bajada"])
                    copy_block("Línea de actualización", r["linea"])
                    st.markdown("**Cambios propuestos**")
                    for c in r["cambios"]:
                        st.markdown(f"- **{c.get('parrafo')} · {c.get('accion')}** — {c.get('motivo', '')}")
                    v1, v2 = st.tabs(["Ver cambios marcados", "Texto final para copiar"])
                    with v1:
                        st.markdown(f"<div class='diff'>{diff_html(r['texto_original'], r['texto_nuevo'])}</div>",
                                    unsafe_allow_html=True)
                    with v2:
                        st.code(f"{r['linea']}\n\n{r['texto_nuevo']}", language=None, wrap_lines=True)

                elif key == "news":
                    F = r.get("fuentes", {})
                    if r.get("hay_novedades"):
                        st.success(r.get("resumen") or "Hay novedades.")
                    else:
                        st.info(r.get("resumen") or "No se encontraron novedades posteriores.")
                    if r.get("error_busqueda"):
                        st.caption(f"Aviso de búsqueda: {r['error_busqueda']}")
                    for n in r.get("novedades", []):
                        if not isinstance(n, dict):
                            continue
                        st.markdown(pill(n.get("relevancia", "media")) + pill(n.get("tipo", ""), "baja")
                                    + ("**Cambia la nota** · " if n.get("cambia_la_nota") else "")
                                    + f"{n.get('hecho')}  \n<span class='meta'>Fuentes:</span> "
                                    + src_links(n.get("fuentes"), F), unsafe_allow_html=True)
                    if r.get("contradicciones"):
                        st.markdown("**Contradicciones con lo publicado**")
                        for c in r["contradicciones"]:
                            if isinstance(c, dict):
                                st.markdown(f"- La nota dice: *{c.get('en_la_nota')}* → Ahora: **{c.get('ahora')}** "
                                            f"({src_links(c.get('fuentes'), F)})")
                    sug = r.get("sugerencia") or {}
                    if r.get("hay_novedades") and any(sug.values()):
                        st.markdown("**Propuesta de actualización**")
                        copy_block("Título", sug.get("titulo", ""))
                        copy_block("Línea de actualización", sug.get("linea_actualizacion", ""))
                        copy_block("Párrafo nuevo", sug.get("parrafo_nuevo", ""))
                        st.caption("Tip: copiá la novedad en «Actualizar con dato nuevo» para aplicar el cambio sobre la nota.")
                    with st.expander(f"Resultados revisados ({len(F)}) · búsquedas: {', '.join(r.get('queries', []))} · {r.get('motor')}"):
                        for k, f in F.items():
                            st.markdown(f"**{k}** · {f['source']} · {f['date'] or 'sin fecha'} — [{f['title']}]({f['url']})")

                elif key == "expiry":
                    st.markdown(pill(str(r["estado"]).upper(), str(r["estado"]).lower())
                                + f" {r['resumen']}" + (f" <span class='meta'>· antigüedad {r['antiguedad']}</span>" if r["antiguedad"] else ""),
                                unsafe_allow_html=True)
                    if not r.get("titulo_ok", True) and r.get("titulo_sugerido"):
                        copy_block("⚠️ El título envejeció. Sugerido:", r["titulo_sugerido"])
                    items = sorted(r["items"], key=lambda i: {"alta": 0, "media": 1, "baja": 2}.get(str(i.get("prioridad")), 3))
                    if items:
                        st.dataframe(pd.DataFrame([{"Prioridad": i.get("prioridad"), "Párrafo": i.get("parrafo"),
                                                    "Frase": i.get("frase"), "Problema": i.get("problema"),
                                                    "Sugerencia": i.get("sugerencia")} for i in items]),
                                     hide_index=True, use_container_width=True)
                    if r["links_revisados"]:
                        if r["links_rotos"]:
                            st.markdown(f"**Enlaces con problemas** ({len(r['links_rotos'])} de {r['links_revisados']} revisados)")
                            for l in r["links_rotos"]:
                                st.markdown(f"- `{l['status'] or l.get('error')}` {l['url']}")
                        else:
                            st.caption(f"✓ {r['links_revisados']} enlaces revisados, ninguno roto.")
                    with st.expander(f"Expresiones temporales detectadas automáticamente ({len(r['flags'])})"):
                        if r["flags"]:
                            st.dataframe(pd.DataFrame(r["flags"]), hide_index=True, use_container_width=True)

                elif key == "follow":
                    sk = r.get("esqueleto") or {}
                    if sk:
                        copy_block("Título tentativo", sk.get("titulo", ""))
                        copy_block("Bajada tentativa", sk.get("bajada", ""))
                        if sk.get("estructura"):
                            st.markdown("**Estructura**\n" + "\n".join(f"1. {x}" for x in sk["estructura"]))
                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown("**Qué viene**")
                        for h in r["que_viene"]:
                            if isinstance(h, dict):
                                st.markdown(f"- **{h.get('fecha') or 'sin fecha'}** · {h.get('hito')} <span class='meta'>({h.get('segun', '')})</span>",
                                            unsafe_allow_html=True)
                        st.markdown("**Datos a verificar**")
                        for d in r["datos_a_verificar"]:
                            st.markdown(f"- {d}")
                    with c2:
                        st.markdown("**Preguntas para las fuentes**")
                        for q in r["preguntas"]:
                            if isinstance(q, dict):
                                st.markdown(f"- {q.get('pregunta')} <span class='meta'>→ {q.get('a_quien', '')}</span>",
                                            unsafe_allow_html=True)
                        st.markdown("**Ángulos posibles**")
                        for a in r["angulos"]:
                            if isinstance(a, dict):
                                st.markdown(f"- **{a.get('titulo')}** — {a.get('enfoque')}")
                meta_line(r.get("meta", {}))

# ════════════════════════════════════════════════════════════════════════════
# OTROS MEDIOS
# ════════════════════════════════════════════════════════════════════════════
with tab_other:
    st.markdown("Con **un link** armamos nuestra versión. Con **varios links de la misma historia** comparamos coberturas.")
    urls_txt = st.text_area("Links (uno por línea, hasta 6)", height=120, key="other_urls",
                            placeholder="https://www.tycsports.com/…\nhttps://www.clarin.com/…")
    with st.expander("¿Algún link no carga? Pegá textos"):
        other_paste = st.text_area("Separá cada nota con una línea que diga solo ---  (título en la primera línea)",
                                   height=160, key="other_text")
    urls = [u.strip() for u in re.split(r"[\s,]+", urls_txt) if u.strip()][:6]
    pasted_notes = [t.strip() for t in re.split(r"^\s*---\s*$", other_paste, flags=re.M) if t.strip()]
    n_total = len(urls) + len(pasted_notes)
    also_ours = False
    if n_total >= 2:
        also_ours = st.checkbox("Además, armar nuestra versión con la nota más completa")

    if st.button("Analizar →", type="primary", disabled=n_total == 0, key="other_go"):
        arts: list[Article] = []
        with st.spinner("Leyendo notas…"):
            for u in urls:
                a = cached_fetch(u)
                if a.ok:
                    arts.append(a)
                else:
                    st.warning(f"{u}: {a.error}")
            arts += [from_text(t) for t in pasted_notes]
        if not arts:
            st.error("No se pudo leer ninguna nota.")
            st.stop()
        res: dict = {"articles": arts}
        with st.status("Trabajando…", expanded=True) as s:
            if len(arts) >= 2:
                st.write("Comparando coberturas…")
                try:
                    res["compare"] = tools.compare(arts, mode)
                except Exception as e:  # noqa: BLE001
                    res["compare"] = {"error": str(e)}
            if len(arts) == 1 or also_ours:
                st.write("Armando nuestra versión…")
                base = max(arts, key=lambda a: len(a.text))
                try:
                    res["ours"] = tools.our_version(base, mode)
                    res["ours"]["base"] = base.site
                except Exception as e:  # noqa: BLE001
                    res["ours"] = {"error": str(e)}
            s.update(label="Listo", state="complete", expanded=False)
        st.session_state["other"] = res

    O = st.session_state.get("other")
    if O:
        st.divider()
        for a in O["articles"]:
            article_card(a)
        names = {"compare": "⚖️ Comparación", "ours": "🖊️ Nuestra versión"}
        keys = [k for k in names if k in O]
        for tab, key in zip(st.tabs([names[k] for k in keys]), keys):
            with tab:
                r = O[key]
                if "error" in r:
                    st.error(r["error"])
                    continue
                if key == "compare":
                    st.success(r["sintesis"] or "—")
                    st.markdown("**Quién lo publicó primero**")
                    st.markdown("\n".join(f"{i + 1}. **{t['medio']} · {t['sitio']}** — {fmt_dt(t['publicada'])} — {t['titulo']}"
                                          for i, t in enumerate(r["timeline"])))
                    if r["matriz"]:
                        icon = {"confirma": "✅ confirma", "contradice": "❌ contradice", "matiza": "〰️ matiza", "no menciona": "·"}
                        rows = []
                        for m in r["matriz"]:
                            row = {"Afirmación": m.get("afirmacion"), "Tipo": m.get("tipo")}
                            for k, site in r["medios"].items():
                                row[f"{k} {site}"] = icon.get(str(m.get(k, "no menciona")).lower(), m.get(k, "·"))
                            rows.append(row)
                        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
                    if r["contradicciones"]:
                        st.markdown("**Contradicciones**")
                        for c in r["contradicciones"]:
                            if isinstance(c, dict):
                                st.markdown(f"- **{c.get('tema')}**: " + " / ".join(
                                    f"{v.get('medio')} ({r['medios'].get(v.get('medio'), '')}): {v.get('dice')}"
                                    for v in c.get("versiones", []) if isinstance(v, dict)))
                    if r["exclusivos"]:
                        st.markdown("**Lo que tiene un solo medio**")
                        for x in r["exclusivos"]:
                            if isinstance(x, dict):
                                st.markdown(f"- {x.get('medio')} ({r['medios'].get(x.get('medio'), '')}): {x.get('afirmacion')}")
                    if r["que_usar"]:
                        st.info(f"**Qué publicaríamos hoy:** {r['que_usar']}")
                else:
                    st.markdown(f"<span class='meta'>Basada en: {r.get('base', '')}</span>  \n{r['resumen']}", unsafe_allow_html=True)
                    c1, c2 = st.columns([3, 2])
                    with c1:
                        copy_block("Título", r["titulo"])
                        copy_block("Bajada", r["bajada"])
                        copy_block("Borrador", "\n\n".join(r["borrador"]))
                        pct = r["coincidencia"]
                        (st.success if pct < 5 else st.warning if pct < 15 else st.error)(
                            f"Coincidencia textual con la nota original: {pct}% (secuencias de 6 palabras)")
                        for sp in r["tramos_copiados"]:
                            st.caption(f"Tramo repetido: «{sp}»")
                    with c2:
                        st.markdown("**Cómo atribuir**")
                        st.write(r["como_atribuir"] or "—")
                        st.markdown("**Confirmar antes de publicar**")
                        for x in r["confirmar_antes"]:
                            st.markdown(f"- {x}")
                        st.markdown("**Ángulos que no usaron**")
                        for a in r["angulos"]:
                            if isinstance(a, dict):
                                st.markdown(f"- **{a.get('titulo')}** — {a.get('enfoque')}")
                    if r["hechos"]:
                        with st.expander("Hechos y su origen"):
                            st.dataframe(pd.DataFrame([{"Hecho": h.get("hecho"), "Origen": h.get("origen"),
                                                        "Párrafo": h.get("parrafo"), "Verificar": "sí" if h.get("verificar") else ""}
                                                       for h in r["hechos"] if isinstance(h, dict)]),
                                         hide_index=True, use_container_width=True)
                meta_line(r.get("meta", {}))
