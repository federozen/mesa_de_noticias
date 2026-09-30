# Mesa · Notas vivas

Herramientas de redacción a partir de un link. No escribe notas desde cero: trabaja sobre lo ya publicado.

## Mi nota (un link propio)
| Acción | Qué hace | Qué parte hace el código y cuál la IA |
|---|---|---|
| ✏️ Actualizar | Aplica un dato nuevo cambiando lo mínimo: título, bajada, línea de "Actualización" y párrafos a cambiar. Muestra los cambios marcados (tachado/verde) y el texto final. | La IA propone los cambios por párrafo; el código los aplica, así no reescribe la nota entera. |
| 📡 Novedades | Busca qué pasó **después** de la publicación y lista novedades y contradicciones, cada una con su fuente. | Búsqueda gratis sin clave (DuckDuckGo/Bing news); si no trae nada, usa la búsqueda integrada de Groq. La IA sólo puede citar fuentes encontradas. |
| ⏳ Vencimiento | Marca lo que envejeció: "este domingo", "se espera que", tabla, rachas, edades, próximo rival. Revisa enlaces rotos. | Detección automática por reglas + evaluación de la IA con la fecha de hoy. Enlaces: chequeo HTTP real. |
| 🧭 Seguimiento | Qué viene (hitos y fechas), preguntas para las fuentes, datos a verificar, ángulos y esqueleto de la próxima nota. | IA sobre la nota (+ novedades si se buscaron). |

## Otros medios
- **1 link → Nuestra versión**: qué es oficial y qué es propio del otro medio, qué confirmar, cómo atribuir, ángulos que no usaron y un borrador. Mide la **coincidencia textual** con el original para evitar copias.
- **2 a 6 links → Comparar coberturas**: quién lo publicó primero, matriz afirmación × medio (confirma / contradice / matiza / no menciona), contradicciones, exclusivos y qué publicaríamos hoy.

Si un sitio bloquea la lectura, la app prueba un lector alternativo; si igual falla, se puede pegar el texto.

## Modelos
Cadena de respaldo automática: **Groq → Cerebras → OpenRouter → Gemini → (Claude)**. Si uno se cae, se queda sin cuota o devuelve algo roto, pasa al siguiente. La barra lateral muestra qué proveedores están activos y cada resultado indica qué modelo lo produjo.

Modos: *Gratis*, *Gratis y Claude si todo falla* (recomendado) o *Claude primero*.

Todos gratis y sin tarjeta: Groq (console.groq.com), Cerebras (cloud.cerebras.ai), OpenRouter (openrouter.ai). Con Groq sólo ya funciona; sumar Cerebras u OpenRouter evita cortes por el límite por minuto del plan gratis de Groq.

## Publicar en Streamlit Community Cloud (gratis)
1. Subí esta carpeta a un repositorio de GitHub.
2. Entrá a share.streamlit.io → **Create app** → elegí el repo, rama `main`, archivo `app.py`.
3. En **Advanced settings → Secrets** pegá el contenido de `.streamlit/secrets.toml.example` con tus claves.
4. Deploy. Para cambiar claves después: App → Settings → Secrets (se aplica solo).

## Correr local
```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # y completá las claves
streamlit run app.py
```

## Estructura
```
app.py              interfaz
mesa/llm.py         router de modelos con respaldo
mesa/fetch.py       lectura de notas (título, fechas, párrafos, enlaces)
mesa/search.py      búsqueda de novedades
mesa/textutils.py   reglas de vencimiento, diff, coincidencia textual
mesa/prompts.py     instrucciones y guía de estilo
mesa/tools.py       las seis herramientas
```
