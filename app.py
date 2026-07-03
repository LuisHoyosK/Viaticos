import streamlit as st
import os, json, tempfile, datetime, re
import google.generativeai as genai

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN — API Keys (Streamlit Secrets)
# ─────────────────────────────────────────────────────────────────────────────
def get_secret(key):
    try: return st.secrets[key]
    except KeyError: return ""

GEMINI_API_KEY = get_secret("GEMINI_API_KEY")

# ─────────────────────────────────────────────────────────────────────────────
# DICCIONARIO DE ABREVIACIONES DE MUNICIPIOS
# ─────────────────────────────────────────────────────────────────────────────
MUNICIPIO_ABREV = {
    "MONTERIA": "", "MONTERÍA": "",
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
# PROMPT OPTIMIZADO PARA EXTRACCIÓN NATIVA
# ─────────────────────────────────────────────────────────────────────────────
PROMPT_TEMPLATE = """\
Eres un experto en auditoría fiscal y extracción de datos estructurados desde documentos oficiales del SIIF Nación de Colombia.
Analiza visual y textualmente el documento PDF proporcionado para extraer la información exacta y estructurarla en el formato JSON requerido.

### INSTRUCCIONES DE EXTRACCIÓN:

1. **consecutivo_cdp**: Busca el número de 4 dígitos correspondiente al Consecutivo del Certificado de Disponibilidad Presupuestal (CDP). Suele encontrarse bajo o al lado de la etiqueta "Consecutivo CDP" o en la sección de viáticos.
2. **solicitud_comision_no**: Identifica el número de 5 dígitos que representa la "Solicitud de Comisión No." en el encabezado del trámite.
3. **objeto_comision_general**: Extrae el texto completo que describe el propósito de la comisión. Se ubica típicamente en la celda o sección final rotulada como "OBJETO DE LA COMISIÓN" o "Objeto de la Comisión por Tercero". No lo trunques.
4. **total_solicitud**: Localiza el valor total acumulado a pagar por la solicitud de comisión (asociado a la sección de "Totales Solicitud de Comisión"). Devuelve el valor numérico como una cadena limpia (ej. si dice "1.414.740,00", devuélvelo como "1414740").

5. **comisionados**: Genera una lista con las personas asignadas a la comisión. Para cada comisionado:
   - **nombre**: Reconstruye el nombre completo de la persona de forma legible y unificada (por ejemplo, "LILIANA MARIA SIERRA HERNANDEZ"). Ignora cortes de línea de la tabla o palabras de cargo adyacentes como "CONTRATISTA".
   - **dias_comision**: Por cada fila de tramo/itinerario asignada a ese comisionado, extrae la fecha inicial (`fi`) y la fecha final (`ff`) en formato estricto `YYYY-MM-DD`. Debe haber un objeto de rango por cada tramo listado.
   - **municipios_destino**: Extrae una lista de los municipios o ciudades destino de los tramos (ej. "AYAPEL", "PLANETA RICA"). Limpia el nombre omitiendo el departamento si viene en formato "CORDOBA/AYAPEL" o "CORDOBA/ MONTERIA".

### REGLAS DE CONTROL:
- Un comisionado puede tener múltiples filas (tramos) que representan diferentes fechas y destinos en el mismo documento. Agrúpalas todas bajo el mismo comisionado.
- Devuelve **ÚNICAMENTE** el objeto JSON, sin código Markdown ni texto explicativo adicional.

### JSON ESPERADO:
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
      "municipios_destino": ["string"]
    }
  ]
}
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
        if j > i + 1:          # 3+ días consecutivos → rango
            grupos.append(f'{dias[i]} al {dias[j]}')
        else:                   # 1 o 2 días → listar individualmente
            for k in range(i, j + 1):
                grupos.append(str(dias[k]))
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
    parts = re.split(r'[/\\|]', mpio)
    nombre = parts[-1].strip().upper()
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
# LLAMADAS A GEMINI CON ARCHIVO NATIVO Y FALLBACK DE MODELO
# ─────────────────────────────────────────────────────────────────────────────
def call_gemini_native(prompt, pdf_path, model_name):
    genai.configure(api_key=GEMINI_API_KEY.strip())
    model = genai.GenerativeModel(model_name)
    
    # Subir archivo directamente a la API de Gemini
    uploaded_file = genai.upload_file(pdf_path, mime_type="application/pdf")
    try:
        resp = model.generate_content(
            [prompt, uploaded_file],
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.0
            )
        )
        return resp.text.strip()
    finally:
        # Borrar el archivo cargado para liberar cuota de almacenamiento
        uploaded_file.delete()

def parse_ai_response(raw):
    """Limpia bloques ```json ... ``` y parsea JSON."""
    if raw.startswith('```'):
        raw = raw.split('```')[1]
        if raw.startswith('json'): raw = raw[4:]
    return json.loads(raw)

# ─────────────────────────────────────────────────────────────────────────────
# INTERFAZ STREAMLIT
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Extractor Viáticos", page_icon="📄", layout="centered")

st.markdown("""
<div style="background:linear-gradient(135deg,#1e3a8a,#1d4ed8);border-radius:12px;
            padding:22px 28px;margin-bottom:24px;font-family:'Segoe UI',sans-serif;color:white">
  <div style="font-size:20px;font-weight:700;margin-bottom:4px">📄 Extractor de Viáticos — SIIF Nación</div>
  
</div>
""", unsafe_allow_html=True)

uploaded_file = st.file_uploader("📂 Selecciona el documento PDF", type=['pdf'])

if st.button("⚡ Generar Resumen", type="primary", use_container_width=True):
    if uploaded_file is None:
        st.warning("⚠️ Selecciona un PDF primero.")
    elif not GEMINI_API_KEY:
        st.error("⚠️ GEMINI_API_KEY no configurada. Agrégala en Configuración > Secrets.")
    else:
        with st.status("Procesando documento...", expanded=True) as status:
            st.write("⏳ Guardando archivo temporal...")
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
                tmp.write(uploaded_file.getvalue())
                tmp_path = tmp.name
                
            try:
                raw = None
                
                # Intentamos con Gemini 2.5 Flash primero
                st.write("🤖 Analizando documento con Gemini 2.5 Flash (Modelo Principal)...")
                try:
                    raw = call_gemini_native(PROMPT_TEMPLATE, tmp_path, "gemini-2.5-flash")
                except Exception as e25:
                    st.write(f"⚠️ Límite de consumo o error en Gemini 2.5: {str(e25)}")
                    st.write("🔄 Reintentando automáticamente con Gemini 3.5 Flash (Modelo de Respaldo)...")
                    raw = call_gemini_native(PROMPT_TEMPLATE, tmp_path, "gemini-3.5-flash")

                st.write("🧩 Parsea y construye el resultado final...")
                data = parse_ai_response(raw)
                texto_final = build_text(data)
                status.update(label="¡Extracción completada!", state="complete", expanded=False)

            except Exception as e:
                status.update(label="Ocurrió un error en el procesamiento", state="error")
                st.error(f"❌ Error final: {str(e)}")
                texto_final = None
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                    
        if texto_final:
            st.markdown('<div style="font-family:sans-serif;font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px">📋 Texto listo — Haz clic adentro y presiona Ctrl+A, Ctrl+C para copiar</div>', unsafe_allow_html=True)
            st.text_area("Resultado", value=texto_final, height=300, label_visibility="collapsed")
