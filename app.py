import streamlit as st
import camelot, os, json, tempfile, datetime, re
# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN — API Keys (Streamlit Secrets)
# ─────────────────────────────────────────────────────────────────────────────
def get_secret(key):
    try: return st.secrets[key]
    except KeyError: return ""
GROQ_API_KEY   = get_secret("GROQ_API_KEY")
GEMINI_API_KEY = get_secret("GEMINI_API_KEY")
GROQ_MODEL   = "llama-3.3-70b-versatile"
GEMINI_MODEL = "gemini-2.5-flash"
# ─────────────────────────────────────────────────────────────────────────────
# DICCIONARIO DE ABREVIACIONES DE MUNICIPIOS
# ─────────────────────────────────────────────────────────────────────────────
MUNICIPIO_ABREV = {
    "MONTERIA": "Mtría.", "MONTERÍA": "Mtría.",
    "AYAPEL": "Aypl.",
    "BUENAVISTA": "Bvst.",
    "CANALETE": "Cnlte.",
    "CERETE": "Crté.", "CERETÉ": "Crté.",
    "CHIMA": "Chmá.", "CHIMÁ": "Chmá.",
    "CHINU": "Chnú.", "CHINÚ": "Chnú.",
    "CIENAGA DE ORO": "Cg.Oro.", "CIÉNAGA DE ORO": "Cg.Oro.",
    "COTORRA": "Ctrra.",
    "MUNICIPIO - LA - - CORDOBA": "L.Aprt.",
    "LORICA": "Lrca.",
    "LOS CORDOBAS": "L.Cdbas.", "LOS CÓRDOBAS": "L.Cdbas.",
    "MOMIL": "Mml.",
    "MONTELIBANO": "Mlbno.", "MONTELÍBANO": "Mlbno.",
    "MONITOS": "Mñts.", "MOÑITOS": "Mñts.",
    "PLANETA RICA": "Pl.Rca.",
    "PUEBLO NUEVO": "Pbl.Nvo.",
    "PUERTO ESCONDIDO": "Pt.Esc.",
    "PUERTO LIBERTADOR": "Pt.Lbd.",
    "PURISIMA": "Prsma.", "PURÍSIMA": "Prsma.",
    "SAHAGUN": "Shgn.", "SAHAGÚN": "Shgn.",
    "SAN ANDRES SOTAVENTO": "S.And.S.", "SAN ANDRÉS SOTAVENTO": "S.And.S.",
    "SAN ANTERO": "S.Antr.",
    "SAN BERNARDO DEL VIENTO": "S.Bdo.V.",
    "SAN CARLOS": "S.Crls.",
    "SAN JOSE DE URE": "S.Js.Uré.", "SAN JOSÉ DE URÉ": "S.Js.Uré.",
    "SAN PELAYO": "S.Plyo.",
    "TIERRALTA": "Trrlt.",
    "TUCHIN": "Tchn.", "TUCHÍN": "Tchn.",
    "VALENCIA": "Vlnca.",
}
# ─────────────────────────────────────────────────────────────────────────────
# EXTRACCIÓN DE TABLAS
# ─────────────────────────────────────────────────────────────────────────────
def extract_content(pdf_path):
    tablas_csv = []
    try:
        tables = camelot.read_pdf(pdf_path, pages='all', flavor='lattice')
        for i, tbl in enumerate(tables):
            csv_str = tbl.df.to_csv(index=False)
            tablas_csv.append(f'--- TABLA {i+1} ---\n{csv_str}')
    except Exception as e:
        tablas_csv.append(f'Error camelot: {e}')
    return '\n'.join(tablas_csv)
# ─────────────────────────────────────────────────────────────────────────────
# PROMPT (sin redundancias — tabla solo: nombre, días, municipios)
# ─────────────────────────────────────────────────────────────────────────────
PROMPT_TEMPLATE = """\
Eres un experto extrayendo datos de documentos SIIF Nación de Colombia.
Se te proporcionan TABLAS EN CSV (extraídas con camelot lattice) del PDF.
Extrae con MÁXIMA PRECISIÓN:
1. consecutivo_cdp: número del campo "Consecutivo CDP" (sección CDP de viáticos)
2. solicitud_comision_no: número de "Solicitud de Comisión No."
3. objeto_comision_general: texto de la sección "OBJETO DE LA COMISIÓN" (fuera de la tabla de comisionados)
4. total_solicitud: valor de "Valor total a pagar" que aparece en la FILA "Totales Solicitud de Comisión" (borra los 2 ultimos ceros)
5. comisionados: lista de personas DE LA TABLA. Por cada persona extrae SOLO:
   - nombre: nombre completo (columna Nombre)
   - dias_comision: lista de rangos {fi, ff} en formato YYYY-MM-DD. Si hay varias filas para la misma persona, un item por fila.
   - municipios_destino: lista de municipios destino tal cual aparecen
REGLAS:
- Un comisionado puede tener MÚLTIPLES filas (una por día o tramo)
- NO extraer valor_total_pagar ni objeto por comisionado (ya están fuera de la tabla)
- Devuelve ÚNICAMENTE JSON válido
JSON esperado:
{
  "consecutivo_cdp": "string",
  "solicitud_comision_no": "string",
  "objeto_comision_general": "string",
  "total_solicitud": "string",
  "comisionados": [
    {
      "nombre": "string",
      "dias_comision": [{"fi": "YYYY-MM-DD", "ff": "YYYY-MM-DD"}],
      "municipios_destino": ["string"]
    }
  ]
}
════════════════════════════════
TABLAS CSV:
%s
"""
# ─────────────────────────────────────────────────────────────────────────────
# FUNCIONES AUXILIARES
# ─────────────────────────────────────────────────────────────────────────────
MESES = ['','ENERO','FEBRERO','MARZO','ABRIL','MAYO','JUNIO',
         'JULIO','AGOSTO','SEPTIEMBRE','OCTUBRE','NOVIEMBRE','DICIEMBRE']
def clean(v): return ' '.join(str(v).split()) if v else ''
def raw_num(v): return re.sub(r'[^\d]', '', str(v)) if v else ''
def parse_date(s):
    m = re.search(r'(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}|\d{2}-\d{2}-\d{4}|\d{4}/\d{2}/\d{2})', clean(s))
    if not m: return None
    s = m.group(1)
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d'):
        try: return datetime.datetime.strptime(s, fmt)
        except: pass
    return None
def compactar_dias(dias):
    if not dias: return ''
    grupos, i, n = [], 0, len(dias)
    while i < n:
        j = i
        while j + 1 < n and dias[j+1] == dias[j] + 1: j += 1
        grupos.append(f'{dias[i]}={dias[j]}' if j > i else str(dias[i]))
        i = j + 1
    return '-'.join(grupos)
def dias_de_rango(fi_str, ff_str):
    fi = parse_date(fi_str)
    ff = parse_date(ff_str)
    if not fi or not ff: return []
    result, cur = [], fi
    while cur <= ff:
        result.append((cur.day, cur.month))
        cur += datetime.timedelta(days=1)
    return result
def abreviar_municipio(mpio):
    """Busca el municipio en el diccionario de abreviaciones.
    Si no lo encuentra, devuelve el nombre limpio en mayúsculas."""
    if not mpio: return ''
    # Limpiar: quitar "CORDOBA / ", "CÓRDOBA / ", etc.
    parts = re.split(r'[/\\|]', mpio)
    nombre = parts[-1].strip().upper()
    # Buscar en el diccionario (con y sin tildes)
    return MUNICIPIO_ABREV.get(nombre, nombre)
def build_text(data):
    cdp   = data.get('consecutivo_cdp') or '—'
    sol   = data.get('solicitud_comision_no') or '—'
    obj_g = (data.get('objeto_comision_general') or '').strip()
    total = raw_num(data.get('total_solicitud'))
    lines = [str(cdp), '']
    for c in data.get('comisionados') or []:
        # Días
        dias_meses_raw = []
        for rango in c.get('dias_comision') or []:
            for dm in dias_de_rango(rango.get('fi',''), rango.get('ff','')):
                if dm not in dias_meses_raw:
                    dias_meses_raw.append(dm)
        dm_list = sorted(dias_meses_raw, key=lambda x: (x[1], x[0]))
        dias_str = mes_str = ''
        if dm_list:
            meses_usados = list(dict.fromkeys(MESES[m] for _, m in dm_list))
            if len(meses_usados) == 1:
                dias_str = compactar_dias([d for d, _ in dm_list])
                mes_str  = meses_usados[0]
            else:
                grupos = {}
                for d, m in dm_list:
                    grupos.setdefault(m, []).append(d)
                partes = [compactar_dias(sorted(dias)) + ' ' + MESES[mes]
                          for mes, dias in sorted(grupos.items())]
                dias_str = ' / '.join(partes)
        # Municipios con abreviación
        mpios_raw = c.get('municipios_destino') or []
        mpios_clean = []
        for m in mpios_raw:
            mp = abreviar_municipio(m)
            if mp and mp not in mpios_clean:
                mpios_clean.append(mp)
        mpio_str = ' - '.join(mpios_clean)
        nombre = (c.get('nombre') or '').upper()
        p = [f'VIA FORM {sol}']
        if dias_str and mes_str: p.append(f'DIAS {dias_str} {mes_str}')
        elif dias_str:           p.append(f'DIAS {dias_str}')
        if mpio_str:             p.append(f'MPIO {mpio_str}')
        if obj_g:                p.append(f'OBJ  {obj_g}')
        lines += [' '.join(p), '', f'{nombre}  {total}']
    if not data.get('comisionados'):
        lines += [f'VIA FORM {sol} OBJ  {obj_g}', '', str(total)]
    return '\n'.join(lines)
# ─────────────────────────────────────────────────────────────────────────────
# LLAMADAS A IA (Grok vía Groq / Gemini)
# ─────────────────────────────────────────────────────────────────────────────
def call_groq(prompt):
    from groq import Groq
    client = Groq(api_key=GROQ_API_KEY.strip())
    resp = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        response_format={"type": "json_object"}
    )
    return resp.choices[0].message.content.strip()
def call_gemini(prompt):
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY.strip())
    model = genai.GenerativeModel(GEMINI_MODEL)
    resp = model.generate_content(
        prompt,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.0
        )
    )
    return resp.text.strip()
def parse_ai_response(raw):
    """Limpia bloques ```json ... ``` y parsea JSON."""
    if raw.startswith('```'):
        raw = raw.split('```')[1]
        if raw.startswith('json'): raw = raw[4:]
    return json.loads(raw)
# ─────────────────────────────────────────────────────────────────────────────
# UI STREAMLIT
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Extractor Viáticos", page_icon="📄", layout="centered")
st.markdown("""
<div style="background:linear-gradient(135deg,#1e3a8a,#1d4ed8);border-radius:12px;
            padding:22px 28px;margin-bottom:24px;font-family:'Segoe UI',sans-serif;color:white">
  <div style="font-size:20px;font-weight:700;margin-bottom:4px">📄 Extractor de Viáticos — SIIF Nación</div>
  <div style="opacity:.75;font-size:12px">Camelot Lattice + IA (Grok / Gemini) · Solo Tablas CSV</div>
</div>
""", unsafe_allow_html=True)
# Selector de motor IA
motor = st.radio(
    "🤖 Motor de IA",
    ["Grok (Groq)", "Gemini (Google)"],
    horizontal=True,
    help="Elige qué modelo procesará el documento"
)
# Validar API Key según motor elegido
if motor == "Grok (Groq)" and not GROQ_API_KEY:
    st.error("⚠️ GROQ_API_KEY no configurada. Agrégala en Configuración > Secrets.")
    st.stop()
elif motor == "Gemini (Google)" and not GEMINI_API_KEY:
    st.error("⚠️ GEMINI_API_KEY no configurada. Agrégala en Configuración > Secrets.")
    st.stop()
uploaded_file = st.file_uploader("📂 Selecciona el documento PDF", type=['pdf'])
if st.button("⚡ Generar Resumen", type="primary", use_container_width=True):
    if uploaded_file is None:
        st.warning("⚠️ Selecciona un PDF primero.")
    else:
        with st.status("Procesando documento...", expanded=True) as status:
            st.write("⏳ Guardando archivo temporal...")
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
                tmp.write(uploaded_file.getvalue())
                tmp_path = tmp.name
            try:
                st.write("⏳ Extrayendo tablas del PDF con Camelot...")
                tablas = extract_content(tmp_path)
                prompt = PROMPT_TEMPLATE % (tablas[:30000])
                if motor == "Grok (Groq)":
                    st.write("🤖 Consultando Grok (Groq)...")
                    raw = call_groq(prompt)
                else:
                    st.write("🤖 Consultando Gemini (Google)...")
                    raw = call_gemini(prompt)
                data = parse_ai_response(raw)
                texto_final = build_text(data)
                status.update(label="¡Extracción completada!", state="complete", expanded=False)
            except Exception as e:
                status.update(label="Ocurrió un error", state="error")
                st.error(f"❌ Error: {str(e)}")
                texto_final = None
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
        if texto_final:
            st.markdown('<div style="font-family:sans-serif;font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px">📋 Texto listo — Haz clic adentro y presiona Ctrl+A, Ctrl+C para copiar</div>', unsafe_allow_html=True)
            st.text_area("Resultado", value=texto_final, height=300, label_visibility="collapsed")
