import streamlit as st
import camelot, os, json, tempfile, datetime, re
from groq import Groq

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN SECRETA
# ─────────────────────────────────────────────────────────────────────────────
# Streamlit leerá esto de su sección "Secrets" en la nube.
# Si estás probando localmente, crea un archivo .streamlit/secrets.toml
try:
    GROQ_API_KEY = st.secrets["GROQ_API_KEY"]
except KeyError:
    # Fallback por si ejecutas en local y olvidaste el archivo secrets
    GROQ_API_KEY = ""
    
GROQ_MODEL = "llama-3.3-70b-versatile"

# ─────────────────────────────────────────────────────────────────────────────
# Funciones Auxiliares y Extracción (Iguales al Notebook)
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

PROMPT_TEMPLATE = """
Eres un experto extrayendo datos de documentos SIIF Nación de Colombia.
Se te proporcionan las TABLAS EN CSV (extraídas con camelot lattice) del PDF.

Usa la información de las tablas para extraer los siguientes campos con la MAYOR PRECISION posible:

- consecutivo_cdp: numero del campo "Consecutivo CDP" (seccion CDP de viaticos)
- solicitud_comision_no: numero de "Solicitud de Comision No."
- objeto_comision_general: texto completo de la seccion "OBJETO DE LA COMISION"
- total_solicitud: valor de "Valor total a pagar" en la fila "Totales Solicitud de Comision"
- comisionados: lista de personas. Por cada persona:
  - nombre: nombre completo (columna Nombre de la tabla)
  - dias_comision: lista de fechas inicial y final (por cada fila de la tabla). Si hay varias filas por persona, añade un item a la lista por cada fila.
    Ejemplo si hay una fila: [{"fi": "2026-05-15", "ff": "2026-05-15"}]
    Ejemplo si hay dos filas: [{"fi": "2026-05-19", "ff": "2026-05-20"}, {"fi": "2026-05-26", "ff": "2026-05-27"}]
  - municipios_destino: lista de municipios destino tal como aparecen en la tabla, si dice MUNICIPIO - lA CORDOBA reemplazalo por LA APARTADA.
  - valor_total_pagar: valor total a pagar de esa persona (quita los 2 ceros decimales al final)
  - objeto: objeto especifico si aparece en la tabla (Objeto de la Comision por Tercero)

REGLAS IMPORTANTES:
- Un comisionado puede tener MULTIPLES filas en la tabla (una por dia o tramo)
- Devuelve UNICAMENTE JSON valido

JSON esperado:
{
  "consecutivo_cdp": "string",
  "solicitud_comision_no": "string",
  "objeto_comision_general": "string",
  "total_solicitud": "string",
  "comisionados": [
    {
      "nombre": "string",
      "dias_comision": [
          {"fi": "YYYY-MM-DD", "ff": "YYYY-MM-DD"}
      ],
      "municipios_destino": ["CORDOBA / AYAPEL"],
      "valor_total_pagar": "string",
      "objeto": "string o null"
    }
  ]
}

════════════════════════════════
TABLAS CSV:
%s
"""

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
        grupos.append(f'{dias[i]} al {dias[j]}' if j > i else str(dias[i]))
        i = j + 1
    return '-'.join(grupos)

def dias_de_rango(fi_str, ff_str):
    fi = parse_date(fi_str)
    ff = parse_date(ff_str)
    if not fi or not ff: return []
    result = []
    cur = fi
    while cur <= ff:
        result.append((cur.day, cur.month))
        cur += datetime.timedelta(days=1)
    return result

def mpio_abrev(mpio):
    if not mpio: return ''
    parts = re.split(r'[/\\|]', mpio)
    return parts[-1].strip().upper()

def build_text(data):
    cdp   = data.get('consecutivo_cdp') or '—'
    sol   = data.get('solicitud_comision_no') or '—'
    obj_g = (data.get('objeto_comision_general') or '').strip()
    total = raw_num(data.get('total_solicitud'))

    lines = [str(cdp), '']
    for c in data.get('comisionados') or []:
        dias_meses_raw = []
        for rango in c.get('dias_comision') or []:
            fi_s = rango.get('fi') or ''
            ff_s = rango.get('ff') or ''
            for dm in dias_de_rango(fi_s, ff_s):
                if dm not in dias_meses_raw:
                    dias_meses_raw.append(dm)

        dm_list = sorted(dias_meses_raw, key=lambda x: (x[1], x[0]))
        dias_str = ''
        mes_str = ''

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
                mes_str  = ''

        mpios_raw = c.get('municipios_destino') or []
        mpios_clean = []
        for m in mpios_raw:
            mp = mpio_abrev(m)
            if mp and mp not in mpios_clean:
                mpios_clean.append(mp)
        mpio_str = ' - '.join(mpios_clean)

        obj       = (c.get('objeto') or obj_g or '').strip()
        nombre    = (c.get('nombre') or '').upper()
        val       = raw_num(c.get('valor_total_pagar')) or str(total)

        p = [f'VIA FORM {sol}']
        if dias_str and mes_str: p.append(f'DIAS {dias_str} {mes_str}')
        elif dias_str:           p.append(f'DIAS {dias_str}')
        if mpio_str:             p.append(f'MPIO {mpio_str}')
        if obj:                  p.append(f'OBJ  {obj}')
        lines += [' '.join(p), '', f'{nombre}  {val}']

    if not data.get('comisionados'):
        lines += [f'VIA FORM {sol} OBJ  {obj_g}', '', str(total)]

    return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# UI y Lógica de Streamlit
# ─────────────────────────────────────────────────────────────────────────────

# Ajustar el ancho de la página y el título del navegador
st.set_page_config(page_title="Extractor Viáticos", page_icon="📄", layout="centered")

st.markdown("""
<div style="background:linear-gradient(135deg,#1e3a8a,#1d4ed8);border-radius:12px;
            padding:22px 28px;margin-bottom:24px;font-family:'Segoe UI',sans-serif;color:white">
  <div style="font-size:20px;font-weight:700;margin-bottom:4px">📄 Extractor de Viáticos — SIIF Nación</div>
  <div style="opacity:.75;font-size:12px">Camelot Lattice + Groq AI · Solo Tablas CSV</div>
</div>
""", unsafe_allow_html=True)

if not GROQ_API_KEY:
    st.error("⚠️ La API Key no está configurada. Si estás en Streamlit Cloud, agrégala en Configuración > Secrets.")
    st.stop()

uploaded_file = st.file_uploader("📂 Selecciona el documento PDF", type=['pdf'])

if st.button("⚡ Generar Resumen", type="primary", use_container_width=True):
    if uploaded_file is None:
        st.warning("⚠️ Selecciona un PDF primero.")
    else:
        with st.status("Procesando documento...", expanded=True) as status:
            
            st.write("⏳ Guardando archivo temporal...")
            # Guardar en disco para camelot
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
                tmp.write(uploaded_file.getvalue())
                tmp_path = tmp.name

            try:
                st.write("⏳ Extrayendo contenido tabular del PDF con Camelot...")
                tablas = extract_content(tmp_path)
                
                st.write("🤖 Consultando Groq AI...")
                client = Groq(api_key=GROQ_API_KEY.strip())
                prompt = PROMPT_TEMPLATE % (tablas[:30000])

                response = client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.0,
                    response_format={"type": "json_object"}
                )
                raw = response.choices[0].message.content.strip()

                if raw.startswith('```'):
                    raw = raw.split('```')[1]
                    if raw.startswith('json'): raw = raw[4:]

                data = json.loads(raw)
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
