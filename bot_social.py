from flask import Flask, render_template, jsonify, request, Response
from flask_socketio import SocketIO
import json
import os
import base64
import subprocess
import sqlite3
import csv
import io as io_module
from datetime import datetime, timedelta
import threading
from concurrent.futures import ThreadPoolExecutor
import schedule
import time
import random
from groq import Groq
import requests as req
from functools import wraps
from agentes_creativos import SuiteCreativaMultiAgente

# Auto-instalar Pillow si no está disponible (necesario para overlay de texto en imágenes)
try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
except ImportError:
    import sys
    print("📦 Instalando Pillow...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow", "--quiet"])
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    print("✅ Pillow instalado correctamente.")

app = Flask(__name__)
_fallback_secret = "social-bot-manager-default-secret-key-2026"
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY", _fallback_secret)
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='gevent',
    manage_session=False,
    ping_timeout=60,
    ping_interval=25,
    logger=False,
    engineio_logger=False,
)

# ============================================
# PERSISTENCIA SQLite
# ============================================
DB_PATH = os.environ.get("DB_PATH", "bot_social.db")

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS captions (
        id TEXT PRIMARY KEY,
        data TEXT NOT NULL,
        fecha TEXT
    );
    CREATE TABLE IF NOT EXISTS stats (
        cliente_id TEXT PRIMARY KEY,
        data TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS product_profiles (
        id TEXT PRIMARY KEY,
        nombre TEXT NOT NULL,
        data TEXT NOT NULL,
        fecha TEXT
    );
    CREATE TABLE IF NOT EXISTS productos (
        id TEXT PRIMARY KEY,
        nombre TEXT NOT NULL,
        precio TEXT,
        precio_anterior TEXT,
        duracion TEXT,
        categoria TEXT DEFAULT 'streaming',
        descripcion TEXT,
        imagen_ref_url TEXT,
        motion TEXT DEFAULT 'zoom_dramatico',
        color_grade TEXT DEFAULT 'cinematico',
        extra_effects TEXT DEFAULT '[]',
        hashtags TEXT DEFAULT '[]',
        lower_third_color TEXT DEFAULT 'white',
        mood TEXT DEFAULT 'energico',
        duracion_reel INTEGER DEFAULT 15,
        veces_usado INTEGER DEFAULT 0,
        ultimo_uso TEXT,
        fecha_creacion TEXT
    );
    CREATE TABLE IF NOT EXISTS post_analytics (
        id TEXT PRIMARY KEY,
        borrador_id TEXT,
        ig_media_id TEXT,
        reach INTEGER DEFAULT 0,
        impressions INTEGER DEFAULT 0,
        likes INTEGER DEFAULT 0,
        comments INTEGER DEFAULT 0,
        saves INTEGER DEFAULT 0,
        shares INTEGER DEFAULT 0,
        fecha_publicacion TEXT,
        ultima_sync TEXT
    );
    """)
    con.commit()
    con.close()

def _db_save_caption(entrada):
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute(
            "INSERT OR REPLACE INTO captions (id, data, fecha) VALUES (?, ?, ?)",
            (entrada['id'], json.dumps(entrada, ensure_ascii=False), entrada.get('fecha', ''))
        )
        con.commit()
        con.close()
    except Exception as e:
        print(f"[DB] Error guardando caption: {e}")

def _db_update_caption(borrador_id, updates):
    try:
        con = sqlite3.connect(DB_PATH)
        row = con.execute("SELECT data FROM captions WHERE id = ?", (borrador_id,)).fetchone()
        if row:
            data = json.loads(row[0])
            data.update(updates)
            con.execute("UPDATE captions SET data = ? WHERE id = ?",
                        (json.dumps(data, ensure_ascii=False), borrador_id))
            con.commit()
        con.close()
    except Exception as e:
        print(f"[DB] Error actualizando caption: {e}")

def _db_save_stats():
    try:
        con = sqlite3.connect(DB_PATH)
        for cid, data in stats_global.items():
            con.execute("INSERT OR REPLACE INTO stats (cliente_id, data) VALUES (?, ?)",
                        (cid, json.dumps(data, ensure_ascii=False)))
        con.commit()
        con.close()
    except Exception as e:
        print(f"[DB] Error guardando stats: {e}")

def _db_load_captions():
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute("SELECT data FROM captions ORDER BY fecha DESC LIMIT 200").fetchall()
        con.close()
        return [json.loads(r[0]) for r in rows]
    except Exception as e:
        print(f"[DB] Error cargando captions: {e}")
        return []

def _db_load_stats():
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute("SELECT cliente_id, data FROM stats").fetchall()
        con.close()
        return {r[0]: json.loads(r[1]) for r in rows}
    except Exception as e:
        print(f"[DB] Error cargando stats: {e}")
        return {}

def _db_save_profile(profile):
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute(
            "INSERT OR REPLACE INTO product_profiles (id, nombre, data, fecha) VALUES (?, ?, ?, ?)",
            (profile['id'], profile['nombre'], json.dumps(profile, ensure_ascii=False), profile.get('fecha', ''))
        )
        con.commit()
        con.close()
    except Exception as e:
        print(f"[DB] Error guardando perfil: {e}")

def _db_load_profiles():
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute("SELECT data FROM product_profiles ORDER BY fecha DESC").fetchall()
        con.close()
        return [json.loads(r[0]) for r in rows]
    except Exception as e:
        print(f"[DB] Error cargando perfiles: {e}")
        return []

def _db_delete_profile(profile_id):
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("DELETE FROM product_profiles WHERE id = ?", (profile_id,))
        con.commit()
        con.close()
        return True
    except Exception as e:
        print(f"[DB] Error eliminando perfil: {e}")
        return False

# ============================================
# CATÁLOGO DE PRODUCTOS
# ============================================

def _db_save_producto(p):
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("""
            INSERT OR REPLACE INTO productos
            (id,nombre,precio,precio_anterior,duracion,categoria,descripcion,
             imagen_ref_url,motion,color_grade,extra_effects,hashtags,
             lower_third_color,mood,duracion_reel,veces_usado,ultimo_uso,fecha_creacion)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            p['id'], p['nombre'], p.get('precio',''), p.get('precio_anterior',''),
            p.get('duracion',''), p.get('categoria','streaming'), p.get('descripcion',''),
            p.get('imagen_ref_url',''), p.get('motion','zoom_dramatico'),
            p.get('color_grade','cinematico'),
            json.dumps(p.get('extra_effects',[])), json.dumps(p.get('hashtags',[])),
            p.get('lower_third_color','white'), p.get('mood','energico'),
            int(p.get('duracion_reel',15)), int(p.get('veces_usado',0)),
            p.get('ultimo_uso',''), p.get('fecha_creacion', datetime.now().strftime('%d/%m/%Y'))
        ))
        con.commit()
        con.close()
    except Exception as e:
        print(f"[DB] Error guardando producto: {e}")

def _db_load_productos():
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute("SELECT * FROM productos ORDER BY veces_usado DESC, fecha_creacion DESC").fetchall()
        cols = ['id','nombre','precio','precio_anterior','duracion','categoria','descripcion',
                'imagen_ref_url','motion','color_grade','extra_effects','hashtags',
                'lower_third_color','mood','duracion_reel','veces_usado','ultimo_uso','fecha_creacion']
        con.close()
        result = []
        for row in rows:
            d = dict(zip(cols, row))
            d['extra_effects'] = json.loads(d['extra_effects'] or '[]')
            d['hashtags']      = json.loads(d['hashtags'] or '[]')
            result.append(d)
        return result
    except Exception as e:
        print(f"[DB] Error cargando productos: {e}")
        return []

def _db_update_producto(pid, updates):
    try:
        con = sqlite3.connect(DB_PATH)
        row = con.execute("SELECT * FROM productos WHERE id=?", (pid,)).fetchone()
        if not row:
            con.close(); return
        cols = ['id','nombre','precio','precio_anterior','duracion','categoria','descripcion',
                'imagen_ref_url','motion','color_grade','extra_effects','hashtags',
                'lower_third_color','mood','duracion_reel','veces_usado','ultimo_uso','fecha_creacion']
        d = dict(zip(cols, row))
        for k, v in updates.items():
            if k in ('extra_effects','hashtags') and isinstance(v, list):
                d[k] = json.dumps(v)
            else:
                d[k] = v
        con.execute("""
            UPDATE productos SET nombre=?,precio=?,precio_anterior=?,duracion=?,categoria=?,
            descripcion=?,imagen_ref_url=?,motion=?,color_grade=?,extra_effects=?,hashtags=?,
            lower_third_color=?,mood=?,duracion_reel=?,veces_usado=?,ultimo_uso=?,fecha_creacion=?
            WHERE id=?
        """, (d['nombre'],d['precio'],d['precio_anterior'],d['duracion'],d['categoria'],
              d['descripcion'],d['imagen_ref_url'],d['motion'],d['color_grade'],
              d['extra_effects'],d['hashtags'],d['lower_third_color'],d['mood'],
              d['duracion_reel'],d['veces_usado'],d['ultimo_uso'],d['fecha_creacion'],pid))
        con.commit()
        con.close()
    except Exception as e:
        print(f"[DB] Error actualizando producto: {e}")

def _db_delete_producto(pid):
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("DELETE FROM productos WHERE id=?", (pid,))
        con.commit()
        con.close()
    except Exception as e:
        print(f"[DB] Error eliminando producto: {e}")

def _db_producto_usado(pid):
    """Incrementa el contador de uso y actualiza ultimo_uso."""
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute(
            "UPDATE productos SET veces_usado=veces_usado+1, ultimo_uso=? WHERE id=?",
            (datetime.now().strftime('%d/%m %H:%M'), pid)
        )
        con.commit()
        con.close()
    except Exception as e:
        print(f"[DB] Error actualizando uso de producto: {e}")

def _db_save_analytics(data):
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("""
            INSERT OR REPLACE INTO post_analytics
            (id,borrador_id,ig_media_id,reach,impressions,likes,comments,saves,shares,fecha_publicacion,ultima_sync)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (data['id'], data.get('borrador_id',''), data.get('ig_media_id',''),
              data.get('reach',0), data.get('impressions',0), data.get('likes',0),
              data.get('comments',0), data.get('saves',0), data.get('shares',0),
              data.get('fecha_publicacion',''), datetime.now().strftime('%d/%m %H:%M')))
        con.commit()
        con.close()
    except Exception as e:
        print(f"[DB] Error guardando analytics: {e}")

# ============================================
# HASHTAGS POR CATEGORÍA CON ROTACIÓN
# ============================================

HASHTAGS_PRESET = {
    "streaming": [
        ["#amazontv","#primevideo","#streaming","#peliculas","#series","#chile","#oferta"],
        ["#netflix","#disneyplus","#hbo","#contenido","#entretenimiento","#chile","#oferta"],
        ["#streaminglatam","#peliculasonline","#seriesonline","#ocio","#chile","#oferta","#digital"],
    ],
    "gaming": [
        ["#xbox","#gamepass","#gaming","#videojuegos","#gamers","#chile","#oferta"],
        ["#gamer","#xboxlatam","#gaminglatam","#juegosonline","#xboxgamepass","#chile","#oferta"],
        ["#videogames","#gamingcommunity","#xboxone","#juegos","#chile","#oferta","#digital"],
    ],
    "software": [
        ["#software","#microsoft","#office","#productividad","#tech","#chile","#oferta"],
        ["#office365","#microsoft365","#windows","#antivirus","#herramientas","#chile","#oferta"],
        ["#productividad","#trabajoremoto","#homeoffice","#apps","#digital","#chile","#oferta"],
    ],
    "musica": [
        ["#spotify","#musica","#streaming","#playlist","#chile","#oferta","#digital"],
        ["#apple","#applemusic","#musica","#podcast","#entretenimiento","#chile","#oferta"],
        ["#youtubemusic","#musicalatam","#streaming","#chile","#oferta","#digital","#audio"],
    ],
    "educacion": [
        ["#educacion","#cursos","#online","#aprendizaje","#chile","#oferta","#digital"],
        ["#udemy","#coursera","#educaciondigital","#aprender","#chile","#oferta","#tech"],
        ["#capacitacion","#certificacion","#skill","#professional","#chile","#oferta","#futuro"],
    ],
    "general": [
        ["#oferta","#descuento","#precio","#compras","#digital","#chile","#ahorra"],
        ["#promocion","#ofertadigital","#ahorra","#deal","#latam","#chile","#descuento"],
        ["#exclusivo","#limitado","#especial","#recomendado","#calidad","#chile","#oferta"],
    ]
}

def _get_hashtags_rotados(categoria="general", veces_usado=0):
    """Rota sets de hashtags para evitar shadowban de Instagram."""
    sets = HASHTAGS_PRESET.get(categoria, HASHTAGS_PRESET["general"])
    return sets[veces_usado % len(sets)]

# ============================================
# SMART SCHEDULER — SLOTS ÓPTIMOS LATAM
# ============================================

SLOTS_OPTIMOS_LATAM = {
    0: ["12:00", "20:00"],   # lunes
    1: ["12:30", "20:30"],   # martes
    2: ["13:00", "19:30"],   # miércoles
    3: ["12:00", "20:30"],   # jueves
    4: ["13:00", "21:00"],   # viernes
    5: ["11:00", "20:00"],   # sábado
    6: ["19:30", "21:30"],   # domingo (mejor día LATAM)
}

def _proximo_slot_latam():
    """Devuelve el próximo slot óptimo de publicación para LATAM (próximos 8 días)."""
    ahora = datetime.now()
    for dias in range(8):
        fecha = ahora + timedelta(days=dias)
        dia = fecha.weekday()
        for slot_str in SLOTS_OPTIMOS_LATAM[dia]:
            h, m = map(int, slot_str.split(':'))
            slot_dt = fecha.replace(hour=h, minute=m, second=0, microsecond=0)
            if slot_dt > ahora + timedelta(minutes=5):
                return slot_dt
    return ahora + timedelta(hours=2)

def _segundos_para_proximo_slot():
    """Segundos que faltan para el próximo slot óptimo."""
    return max(60, int((_proximo_slot_latam() - datetime.now()).total_seconds()))

# ============================================
# AUTENTICACIÓN BÁSICA
# ============================================

DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "admin")
DASHBOARD_PASS = os.environ.get("DASHBOARD_PASS", "changeme")

def requiere_auth(f):
    @wraps(f)
    def decorado(*args, **kwargs):
        auth = request.authorization
        if not auth or auth.username != DASHBOARD_USER or auth.password != DASHBOARD_PASS:
            return Response(
                "Acceso denegado. Ingresa tus credenciales.",
                401,
                {"WWW-Authenticate": 'Basic realm="Social Bot Manager"'}
            )
        return f(*args, **kwargs)
    return decorado

# ============================================
# CONFIGURACIÓN GLOBAL E INICIALIZACIÓN
# ============================================
groq_api_key = os.environ.get("GROQ_API_KEY")

if not groq_api_key:
    raise ValueError("❌ ERROR: La variable de entorno GROQ_API_KEY no está configurada en Railway.")

groq_client = Groq(api_key=groq_api_key)
suite_creativa = SuiteCreativaMultiAgente()

# ── HTTP Session persistente con connection pooling ────────────────────────────
# Reutiliza conexiones TCP en lugar de abrir una nueva por cada req.get/post
# Reduce ~200-400ms por llamada HTTP al evitar el TCP handshake repetido
http_session = req.Session()
http_session.headers.update({'User-Agent': 'BotSocial/2.0'})
adapter = req.adapters.HTTPAdapter(
    pool_connections=10,
    pool_maxsize=20,
    max_retries=req.adapters.Retry(total=2, backoff_factor=0.3)
)
http_session.mount('https://', adapter)
http_session.mount('http://', adapter)

stats_global = {}
logs_global = []
bot_activo = False
_bot_lock = threading.Lock()

GRAPH_API_VERSION = "v21.0"

CLIENTES = {
    "aurakey": {
        "nombre": "Aurakey",
        "meta_token": os.environ.get("META_ACCESS_TOKEN"),
        "ig_user_id": os.environ.get("IG_USER_ID"),
    },
}

for clave, cliente in CLIENTES.items():
    stats_global[clave] = {
        'nombre': cliente['nombre'],
        'posts': 0,
        'comentarios': 0,
        'likes': 0,
        'interacciones': 0,
        'ultimo_ciclo': 'Nunca'
    }

# Inicializar DB y restaurar datos persistidos
init_db()
captions_guardados = _db_load_captions()
_stats_db = _db_load_stats()
for cid, sdata in _stats_db.items():
    if cid in stats_global:
        stats_global[cid].update(sdata)

# ============================================
# LOGS
# ============================================

def log(msg, tipo='info'):
    entrada = {'msg': msg, 'tipo': tipo, 'hora': datetime.now().strftime('%H:%M:%S')}
    logs_global.append(entrada)
    if len(logs_global) > 100:
        logs_global.pop(0)
    socketio.emit('log', entrada)
    try:
        print(f"[{tipo.upper()}] {msg}")
    except UnicodeEncodeError:
        print(f"[{tipo.upper()}] {msg}".encode('ascii', errors='replace').decode('ascii'))

# ============================================
# TENDENCIAS
# ============================================

# ⚡ Caché de sugerencias Google: TTL de 10 min por keyword (evita llamadas repetidas)
_tendencias_cache = {}  # {keyword: (timestamp, resultado)}
_TENDENCIAS_TTL = 600   # 10 minutos

def _fetch_google_suggest(keyword):
    """Llama a Google Suggest con caché TTL de 10 minutos."""
    ahora = time.time()
    if keyword in _tendencias_cache:
        ts, cached = _tendencias_cache[keyword]
        if ahora - ts < _TENDENCIAS_TTL:
            return cached  # ⚡ Caché hit — sin llamada HTTP
    try:
        url = f"https://suggestqueries.google.com/complete/search?client=firefox&q={keyword}"
        res = http_session.get(url, timeout=5)
        if res.status_code == 200:
            datos = res.json()
            if len(datos) > 1 and isinstance(datos[1], list):
                resultado = datos[1][:5]
                _tendencias_cache[keyword] = (ahora, resultado)
                return resultado
    except Exception:
        pass
    return []

def buscar_tendencias_reales_api(prod_info):
    keyword = prod_info["keyword_busqueda"]
    log(f"🌐 Escaneando tendencias globales para '{keyword}'...", "info")
    palabras_clave = _fetch_google_suggest(keyword)
    if palabras_clave:
        log(f"🔥 Datos frescos detectados en vivo: {', '.join(palabras_clave)}", "success")
    else:
        palabras_clave = [f"{keyword} 2026", f"best {keyword} tools", "productividad", "trabajo remoto", "ofertas chile"]
    return palabras_clave

# ============================================
# GENERACIÓN DE CONTENIDO
# ============================================

def filtrar_tendencias_con_llm(tendencias_reales, prod_info):
    """
    Usa el LLM para decidir qué tendencias son relevantes para ESTE producto específico.
    Funciona para cualquier producto — no depende de listas hardcodeadas.
    """
    if not tendencias_reales:
        return [prod_info.get("keyword_busqueda", "oferta chile")]

    detalle = prod_info.get("detalle_producto", "")
    lista = "\n".join(f"- {t}" for t in tendencias_reales)

    prompt_filtro = f"""Producto que se va a vender: {detalle}

Estas son tendencias de búsqueda detectadas hoy en Google:
{lista}

Tu tarea: devuelve SOLO las tendencias de esa lista que tienen relación directa con el producto, su categoría, su público objetivo o su beneficio principal.
Descarta cualquier tendencia que sea un fenómeno viral, meme, creepypasta, serie, canción, evento deportivo, noticia política o cualquier cosa que no tenga conexión real con lo que se vende.
Si ninguna es relevante, devuelve únicamente: {detalle}

Responde solo con las tendencias válidas separadas por coma, sin explicaciones, sin guiones, sin numeración."""

    try:
        # ⚡ llama-3.1-8b-instant: tarea simple de filtrado, 3x más rápido que 70b
        res = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt_filtro}],
            max_tokens=100,
            temperature=0.0,  # Determinista — solo filtrar, no crear
        )
        resultado = res.choices[0].message.content.strip()
        filtradas = [t.strip() for t in resultado.split(",") if t.strip()]
        log(f"🧹 Tendencias filtradas para '{detalle}': {filtradas}", "info")
        return filtradas if filtradas else [prod_info.get("keyword_busqueda", detalle)]
    except Exception as e:
        log(f"⚠️ Error filtrando tendencias: {e}. Usando keyword directo.", "warning")
        return [prod_info.get("keyword_busqueda", detalle)]


def normalizar_producto_info(titulo_manual, descripcion_vision):
    """
    Paso intermedio entre Vision y el copywriter.
    Toma el título del usuario + la descripción cruda de Vision y produce
    una ficha estructurada limpia en español, lista para el copywriter.
    Evita que datos vagos o en inglés lleguen al caption.
    """
    contexto = f"Título que escribió el usuario: {titulo_manual}\n" if titulo_manual else ""
    contexto += f"Descripción detectada por visión IA: {descripcion_vision}" if descripcion_vision else ""

    if not contexto:
        return {"nombre": "producto digital", "beneficio": "", "audiencia": "", "categoria": "digital"}

    prompt = f"""A partir de esta información sobre un producto que se va a vender en Instagram Chile:

{contexto}

Genera una ficha del producto en español. Responde SOLO con JSON válido, sin explicaciones ni backticks:
{{
  "nombre": "nombre comercial exacto del producto",
  "beneficio": "qué problema resuelve o qué gana el comprador en una línea",
  "audiencia": "a quién va dirigido (gamers, estudiantes, profesionales, familias, etc.)",
  "categoria": "una de estas: software | licencia | cuenta_juego | suscripcion | producto_fisico | servicio_digital | curso | otro"
}}

Reglas:
- "nombre" debe ser el nombre de marca real, no una descripción genérica
- Si el título del usuario es más específico que la visión, usa ese
- Todo en español salvo nombres de marcas en inglés (Xbox, Kaspersky, etc.)
- Máximo 15 palabras por campo"""

    try:
        # ⚡ llama-3.1-8b-instant: extracción estructurada simple, no necesita 70b
        res = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.0,
        )
        raw = res.choices[0].message.content.strip()
        # Limpiar posibles backticks o prefijos
        raw = raw.replace("```json", "").replace("```", "").strip()
        ficha = json.loads(raw)
        log(f"📋 Ficha producto: {ficha.get('nombre')} | {ficha.get('categoria')} | audiencia: {ficha.get('audiencia')}", "info")
        return ficha
    except Exception as e:
        log(f"⚠️ Error normalizando producto ({e}). Usando datos crudos.", "warning")
        return {
            "nombre": titulo_manual or (descripcion_vision.split(".")[0] if descripcion_vision else "producto digital"),
            "beneficio": descripcion_vision or "",
            "audiencia": "público general",
            "categoria": "digital"
        }


def _get_top_captions_producto(titulo, limit=3):
    """Obtiene los mejores captions históricos de un producto similar (few-shot para Groq)."""
    try:
        keyword = titulo.split()[0].lower() if titulo else ''
        if not keyword:
            return []
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT data FROM captions WHERE lower(data) LIKE ? AND data LIKE '%\"publicado\": true%' ORDER BY fecha DESC LIMIT ?",
            [f"%{keyword}%", limit]
        ).fetchall()
        con.close()
        return [json.loads(r[0]).get('caption','') for r in rows if json.loads(r[0]).get('caption','')]
    except Exception as e:
        print(f"[DB] Error obteniendo captions históricos: {e}")
        return []

def generar_post_estricto(prod_info, tendencias_reales, precio, hashtags_override=None):
    """
    NUEVA VERSIÓN: Utiliza la Suite Creativa de Multi-Agentes para una generación
    de contenido orquestada y de nivel profesional.
    """
    tendencias_filtradas = filtrar_tendencias_con_llm(tendencias_reales, prod_info)
    tendencias_str = ', '.join(tendencias_filtradas) if tendencias_filtradas else '—'
    
    # Obtener historial para alimentar a los agentes
    ficha = prod_info.get("ficha") or {}
    nombre = ficha.get("nombre") or prod_info.get("titulo_producto") or prod_info.get("detalle_producto", "Producto")
    captions_anteriores = _get_top_captions_producto(nombre, limit=2)
    ejemplos_str = "\n\n---\n".join(captions_anteriores) if captions_anteriores else ""

    # Preparar información para la suite
    info_agentes = {
        "nombre": nombre,
        "beneficio": ficha.get("beneficio", ""),
        "audiencia": ficha.get("audiencia", "Público general en Chile"),
        "precio": precio,
        "categoria": ficha.get("categoria", "digital")
    }

    # Ejecutar la orquestación multi-agente
    # Si tendencias_reales está vacío o es '-', la suite buscará tendencias automáticamente
    tendencias_para_suite = tendencias_str if tendencias_str and tendencias_str != '—' else ""
    
    log(f"🚀 Ejecutando Suite Creativa Multi-Agente para {nombre}...", "info")
    resultado = suite_creativa.generar_post_completo(
        info_agentes, 
        tendencias_manuales=tendencias_para_suite, 
        historial_captions=ejemplos_str
    )
    
    # Registrar la estrategia elegida por el Director Creativo
    estrategia = resultado.get("estrategia", {})
    log(f"🧠 Estrategia: {estrategia.get('tactica_venta')} | Tono: {estrategia.get('tono_sugerido')}", "success")
    
    return resultado.get("post_final", "Error en generación multi-agente")


def analizar_imagen_referencia(imagen_referencia_url):
    """
    Groq Vision analiza la imagen de referencia con nivel de arte director senior.
    Produce un análisis técnico que permite a Ideogram replicar el estilo con precisión quirúrgica.
    """
    if not imagen_referencia_url or not isinstance(imagen_referencia_url, str) or not imagen_referencia_url.startswith("http"):
        log("⚠️ URL de referencia inválida o vacía. Saltando análisis de visión.", "warning")
        return None
    try:
        log("🔍 Groq Vision analizando estilo de referencia...", "info")
        img_response = http_session.get(imagen_referencia_url, timeout=15)
        if img_response.status_code != 200:
            log(f"⚠️ No se pudo descargar imagen para análisis (HTTP {img_response.status_code}).", "warning")
            return None
        img_b64 = base64.b64encode(img_response.content).decode("utf-8")
        content_type = img_response.headers.get("Content-Type", "image/jpeg")
        if "png" in content_type:
            media_type = "image/png"
        elif "webp" in content_type:
            media_type = "image/webp"
        else:
            media_type = "image/jpeg"

        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a senior art director at a top-tier advertising agency. "
                        "Your specialty is reverse-engineering the exact visual DNA of commercial images "
                        "so that AI image generators can reproduce the style with photographic precision. "
                        "You describe images like a technical specification document — not like a human describing art. "
                        "Every detail you provide is a direct instruction for an AI model."
                    )
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{img_b64}"}
                        },
                        {
                            "type": "text",
                            "text": (
                                "Analyze this commercial image as a technical style specification. "
                                "Provide an EXHAUSTIVE breakdown covering ALL of the following — be brutally specific, no vague terms:\n\n"
                                "1) COLOR PALETTE: List every dominant color with precise descriptors (e.g. 'electric cobalt blue #0047FF', 'warm ivory #FFF8E7'). "
                                "Name gradient directions and color transitions.\n"
                                "2) VISUAL STYLE: Exact style classification (e.g. 'hyperrealistic 3D render with subsurface scattering', "
                                "'neon cyberpunk flat vector', 'luxury fashion editorial photography', 'bold graphic poster design'). "
                                "Name any specific artistic movement or design school visible.\n"
                                "3) LIGHTING: Type of lighting (studio, rim light, backlit, volumetric, god rays, neon glow, ambient occlusion). "
                                "Direction, intensity, color temperature of light sources. Shadow type and softness.\n"
                                "4) COMPOSITION: Exact layout (centered hero, rule of thirds, diagonal tension, symmetrical, etc.). "
                                "Foreground/midground/background separation. Use of negative space. Camera angle if applicable.\n"
                                "5) TEXTURE & MATERIALS: Surface qualities visible (matte, glossy, metallic, translucent, fabric, liquid, glass, etc.).\n"
                                "6) ATMOSPHERE & MOOD: Emotional tone and how the image achieves it technically (contrast, saturation, blur, depth of field, etc.).\n"
                                "7) KEY GRAPHIC ELEMENTS: Specific shapes, particles, effects, bokeh, glows, smoke, liquid, geometric patterns, or any distinctive visual motifs.\n"
                                "8) TYPOGRAPHY STYLE (if present): Font weight, style, case, color, size hierarchy, placement, any special effects.\n\n"
                                "Format your answer as a dense technical brief — maximum 200 words — that a prompt engineer can paste directly into Ideogram to replicate this exact style."
                            )
                        }
                    ]
                }
            ],
            max_tokens=400,
            temperature=0.1,
        )
        descripcion = response.choices[0].message.content.strip()
        log(f"✅ Groq Vision: análisis de estilo completado ({len(descripcion)} chars)", "success")
        return descripcion
    except Exception as e:
        log(f"⚠️ Error en Groq Vision: {e}. Continuando con estilo genérico.", "warning")
        return None


def generar_prompt_imagen(prod_info, caption, con_referencia=False, descripcion_referencia=None):
    """
    Genera el prompt para Ideogram v3 Balanced usando Groq como prompt engineer élite.
    Exprime al máximo el modelo de imagen con contexto visual específico por categoría.
    """
    ficha     = prod_info.get("ficha") or {}
    nombre    = ficha.get("nombre")    or prod_info.get("titulo_producto") or prod_info.get("detalle_producto", "producto")
    categoria = ficha.get("categoria") or "digital"
    audiencia = ficha.get("audiencia") or "público general"
    beneficio = ficha.get("beneficio") or ""

    # ── Identidad visual por categoría ───────────────────────────────────────
    # Cada categoría tiene su propio lenguaje visual comercial — lo usamos para
    # guiar a Ideogram hacia el estilo que más convierte para ese tipo de producto.
    estilos_categoria = {
        "software": (
            "Ultra-clean tech aesthetic: deep space navy or midnight blue background with electric cyan and violet gradients. "
            "Floating 3D UI screenshots of the actual software interface, glowing with subtle blue light. "
            "Sharp geometric shapes, holographic panels, digital grid lines. "
            "Cinematic product shot composition with dramatic rim lighting. "
            "Premium Apple-meets-Microsoft visual language — precision, power, and minimalism."
        ),
        "licencia": (
            "Premium digital product reveal: dark background with golden or platinum metallic accents. "
            "Holographic license key visualization or floating activation code effect (no actual text). "
            "Soft volumetric light beams hitting the product from above. "
            "Security shield or verified checkmark motif rendered in 3D chrome. "
            "Luxury unboxing composition — the product feels official, rare, and valuable."
        ),
        "cuenta_juego": (
            "Epic gaming universe aesthetic: dark cinematic background with explosive neon energy — electric blue, hot magenta, toxic green. "
            "Dynamic diagonal composition with motion blur and particle effects suggesting speed. "
            "Controller silhouette or game controller rendered in hyperrealistic 3D with glossy finish. "
            "Screen glow reflections, RGB lighting effects, holographic HUD overlays. "
            "Maximum hype energy — feels like a game launch trailer poster."
        ),
        "suscripcion": (
            "Premium streaming/subscription lifestyle: warm, inviting atmosphere with rich jewel tones. "
            "Glowing screen with content thumbnails arranged beautifully in 3D perspective. "
            "Soft bokeh background suggesting comfort and premium home environment. "
            "Floating play button or crown symbol in metallic gold. "
            "Aspirational and modern — feels like something worth paying for every month."
        ),
        "producto_fisico": (
            "High-end product photography style: studio lighting with dramatic shadows and highlights. "
            "Product as hero on clean gradient background — white, light grey, or deep black. "
            "Multiple viewing angles in same frame, exploded view showing quality details. "
            "Specular highlights on materials, texture detail visible at macro level. "
            "Clean, editorial, premium — same quality as Apple or Sony product photography."
        ),
        "servicio_digital": (
            "Professional digital services aesthetic: clean white and light blue background with modern geometric accents. "
            "Abstract visualization of digital workflows — connected nodes, data streams, network lines. "
            "Floating interface mockups showing the result/output of the service. "
            "Corporate premium look with trustworthy color palette: navy, white, accent green or gold. "
            "Conveys speed, reliability, and professional results."
        ),
        "curso": (
            "Transformational learning aesthetic: bright, energetic, optimistic composition. "
            "Gradient background from deep blue to vibrant purple or gold. "
            "Abstract 3D visualization of knowledge growth — ascending graphs, neural networks, brain illumination. "
            "Book or certificate rendered in premium 3D with metallic graduation details. "
            "Upward diagonal composition suggesting progress and achievement. "
            "Feels motivational and premium — not educational stock photography."
        ),
        "otro": (
            "Versatile premium commercial aesthetic: bold gradient background with sophisticated color pairing. "
            "Product or concept rendered as the clear visual hero with cinematic lighting. "
            "Clean negative space for visual breathing room. "
            "Professional advertising composition that commands attention and conveys quality. "
            "Timeless premium look — could appear in a high-end magazine or billboard."
        ),
    }

    estilo_base = estilos_categoria.get(categoria, estilos_categoria["otro"])

    # ── Contexto de estilo según si hay imagen de referencia ─────────────────
    if con_referencia and descripcion_referencia:
        instruccion_estilo = (
            f"STYLE REPLICATION MANDATE: Analyze and apply this EXACT visual DNA from the reference image: "
            f"{descripcion_referencia}\n"
            f"Every element of that style specification must be visible in the output. "
            f"The reference style takes absolute priority over any default aesthetic. "
            f"Merge the reference style seamlessly with the product identity of '{nombre}'."
        )
    elif con_referencia:
        instruccion_estilo = (
            f"Apply an INTENSE premium commercial style: explosive fluid dynamics, "
            f"hyperrealistic 3D liquid splashes with chromatic aberration, "
            f"electric neon color bursts, cinematic depth of field, "
            f"and a vibrant color palette that perfectly matches '{nombre}' brand colors. "
            f"Dramatically detailed, award-winning commercial photography level."
        )
    else:
        instruccion_estilo = (
            f"Apply this category-specific visual language: {estilo_base} "
            f"Adapt these visual principles precisely to '{nombre}' — "
            f"incorporate the real brand colors, iconography, and visual identity of this specific product."
        )

    system_prompt = (
        "You are the world's best prompt engineer for Ideogram v3 Balanced, "
        "specializing in commercial advertising imagery for Latin American social media. "
        "You write prompts that consistently win Ideogram's highest quality ratings. "
        "Your prompts are technical masterpieces: they specify lighting, materials, composition, "
        "color grading, rendering style, and camera angle with surgical precision. "
        "You never use vague adjectives like 'beautiful' or 'amazing' — "
        "you describe exactly what should be rendered and how. "
        "Every prompt you write produces an image that could be a professional Instagram ad."
    )

    user_prompt = f"""Write a MAXIMUM-POWER Ideogram v3 Balanced prompt for this commercial advertisement:

PRODUCT: "{nombre}"
CATEGORY: {categoria}
AUDIENCE: {audiencia}
CORE BENEFIT: {beneficio or 'not specified — infer from product name and category'}

STYLE DIRECTIVE:
{instruccion_estilo}

ABSOLUTE RULES FOR THIS PROMPT:
1. "{nombre}" must be the undeniable HERO — the first thing eyes are drawn to. Visually dominant, accurate, recognizable.
2. Show REAL product-specific elements:
   - Software/apps → actual UI elements, icons, interface screenshots in perspective
   - Games/gaming → controller, console, in-game energy, RGB effects
   - Streaming → glowing screen, content grid, play interface elements
   - Physical products → tactile materials, reflections, multiple angles
   - Services → process visualization, result metaphors, workflow abstraction
3. TYPOGRAPHY: Include ONLY "{nombre}" as a single bold display headline. ZERO other text.
4. TEXT CONTAMINATION RULE (CRITICAL — violations destroy the image):
   - ZERO background text patterns or letter textures
   - ZERO paragraph text, body copy, fine print
   - ZERO random characters used as decoration
   - Background = ONLY: gradients, light effects, particles, geometric shapes, bokeh
5. Format: vertical 9:16 composition, optimized for Instagram Stories and Reels.
6. Quality: 8K commercial photography or top-tier 3D render quality. No stock photo aesthetics.
7. End with this exact sentence to anchor quality: "Absolutely no background text, no decorative letter patterns, no fine print, no fake paragraph text anywhere. Ultra-clean premium commercial design."

OUTPUT: Write ONLY the Ideogram prompt, in English, 120-160 words. Start directly with the visual description. No preamble, no explanation, no headers:"""

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        max_tokens=400,
        temperature=0.4,
        top_p=0.9,
    )
    return response.choices[0].message.content.strip()

# ============================================
# MÚSICA LOCAL POR MOOD
# ============================================

def buscar_musica_pixabay(mood="energico"):
    moods_disponibles = ["energico", "motivador", "relajado", "corporativo", "misterioso", "alegre"]
    mood_key = mood if mood in moods_disponibles else "energico"

    if os.path.exists("music"):
        mp3s = [f for f in os.listdir("music") if f.endswith(".mp3") and f.startswith(mood_key)]
        if mp3s:
            elegido = random.choice(mp3s)
            log(f"🎵 Música seleccionada → music/{elegido}", "success")
            return f"music/{elegido}"

        todos = [f for f in os.listdir("music") if f.endswith(".mp3")]
        if todos:
            elegido = random.choice(todos)
            log(f"🎵 Música fallback → music/{elegido}", "success")
            return f"music/{elegido}"

    log("⚠️ No se encontró música local. Agrega MP3s a la carpeta /music/", "warning")
    return None

# ============================================
# EDICIÓN PREMIUM — WATERMARK + COLOR GRADE
# ============================================

# Presets de color grading — 100% ffmpeg, 100% gratis
COLOR_GRADES = {
    "none":        "",
    "cinematico":  "curves=r='0/0 0.35/0.40 1/0.88':g='0/0.01 0.5/0.47 1/0.92':b='0/0.02 0.5/0.44 1/0.78',eq=contrast=1.10:saturation=0.88",
    "calido":      "curves=r='0/0 0.5/0.58 1/1.0':g='0/0 0.5/0.52 1/0.98':b='0/0 0.5/0.42 1/0.82',eq=saturation=1.10",
    "frio":        "curves=r='0/0 0.5/0.42 1/0.85':g='0/0 0.5/0.50 1/0.96':b='0/0 0.5/0.58 1/1.0',eq=saturation=1.05",
    "dramatico":   "eq=contrast=1.35:saturation=1.15:brightness=-0.04,vignette=angle=PI/2.5",
    "vibrante":    "eq=saturation=1.55:contrast=1.08:brightness=0.02,unsharp=3:3:1.5:3:3:0",
    "vintage":     "curves=r='0/0.04 0.5/0.48 1/0.88':g='0/0.02 0.5/0.45 1/0.84':b='0/0 0.5/0.38 1/0.72',eq=saturation=0.75",
    "neon":        "eq=saturation=1.8:contrast=1.2:brightness=0.03,curves=r='0/0 0.6/0.7 1/1':b='0/0 0.4/0.5 1/1',unsharp=3:3:2.0:3:3:0",
}

LOGO_PATH_DEFAULT = os.environ.get("LOGO_PATH", "static/logo/watermark.png")

# Fuentes disponibles en contenedor Debian/Railway
_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]
DRAWTEXT_FONT = next((f for f in _FONT_PATHS if os.path.exists(f)), None)


def _esc_dt(text):
    """Escapa texto para el filtro drawtext de ffmpeg."""
    if not text:
        return ""
    return (text.replace('\\', '\\\\')
                .replace("'", "’")   # reemplaza comilla simple para evitar rotura
                .replace(':', '\\:')
                .replace('%', '\\%'))


def _build_motion_overlay_filter(fa, titulo, precio, color='white', extra_effects=None, precio_anterior=None):
    """
    Sistema de diseño en movimiento cinematográfico — 6 capas base + efectos extra.

    CAPA 1 · Scan Line     — línea de luz barre la imagen de arriba abajo (0-1.5s)
    CAPA 2 · Corner Accents— marcos cyan en esquinas superiores
    CAPA 3 · Price Badge   — tarjeta de precio pulsante (esquina superior, si hay precio)
    CAPA 4 · Lower Third   — barra inferior + separador + título slide-in (si hay titulo)
    CAPA 5 · Arrow Bounce  — flecha animada antes del lower-third
    CAPA 6 · CTA Blink     — llamada a acción parpadeante

    EXTRA  · neon_frame    — marco neon pulsante 4 lados
    EXTRA  · glitch        — glitch cyberpunk: flashes RGB + bloques corrupción
    EXTRA  · ticker        — ticker scrolleante de oferta en banda inferior
    EXTRA  · intro_cine    — overlay oscuro + título central cinematográfico
    EXTRA  · badge_viral   — badge VIRAL pulsante en esquina superior derecha
    """
    parts = []

    # ══════════════════════════════════════════════════════════════════
    # CAPA 2 — CORNER ACCENTS
    # Aparecen a los 0.3s (durante el scan), desaparecen con fade-out
    # ══════════════════════════════════════════════════════════════════
    parts.append("drawbox=x=22:y=30:w=90:h=4:color=0x00E5FF@0.85:t=fill:enable='gte(t\\,0.3)'")
    parts.append("drawbox=x=22:y=30:w=4:h=70:color=0x00E5FF@0.85:t=fill:enable='gte(t\\,0.4)'")
    parts.append("drawbox=x=968:y=30:w=90:h=4:color=0x00E5FF@0.85:t=fill:enable='gte(t\\,0.3)'")
    parts.append("drawbox=x=1054:y=30:w=4:h=70:color=0x00E5FF@0.85:t=fill:enable='gte(t\\,0.4)'")


    # ══════════════════════════════════════════════════════════════════
    # CAPA 4 — LOWER THIRD  (solo si hay titulo Y fuente disponible)
    # Barra oscura + separador cyan + título slide-in + precio pulsante
    # ══════════════════════════════════════════════════════════════════
    if titulo and fa:
        # Barra oscura inferior (cubre y=1421-1920)
        parts.append(
            "drawbox=x=0:y=ih*0.74:w=iw:h=ih*0.26:color=black@0.76:t=fill"
        )
        # Separador cyan — aparece al empezar el slide-in
        parts.append(
            "drawbox=x=0:y=ih*0.74:w=iw:h=4:color=0x00E5FF@0.95:t=fill:"
            "enable='gte(t\\,0.5)'"
        )
        # Título: slide-in lateral + fade
        parts.append(
            f"drawtext={fa}text='{titulo}':fontsize=66:fontcolor={color}:"
            f"x='if(lt(t\\,0.7)\\,-w\\,if(lt(t\\,1.2)\\,-w+(w+30)*(t-0.7)/0.5\\,30))':y=h*0.775:"
            f"shadowcolor=black@0.9:shadowx=3:shadowy=3:"
            f"alpha='if(lt(t\\,0.7)\\,0\\,if(lt(t\\,1.2)\\,(t-0.7)/0.5\\,1))'"
        )
        if precio:
            # Precio anterior tachado (si existe) — encima del precio actual
            if precio_anterior and fa:
                _pa_esc = _esc_dt(precio_anterior)
                _pa_w   = max(80, len(precio_anterior) * 24)
                parts.append(
                    f"drawtext={fa}text='{_pa_esc}':fontsize=46:fontcolor=0x999999:"
                    f"x=30:y=h*0.840:"
                    f"shadowcolor=black@0.5:shadowx=1:shadowy=1:"
                    f"alpha='if(lt(t\\,1.0)\\,0\\,if(lt(t\\,1.4)\\,(t-1.0)/0.4\\,0.70))'"
                )
                # Línea roja de tachado sobre el precio anterior
                parts.append(
                    f"drawbox=x=28:y=ih*0.858:w={_pa_w}:h=4:color=0xFF3333@0.92:t=fill:"
                    f"enable='gte(t\\,1.0)'"
                )
            # Precio actual: slide-in con delay + pulso continuo
            parts.append(
                f"drawtext={fa}text='{precio}':fontsize=84:fontcolor=yellow:"
                f"x='if(lt(t\\,1.0)\\,-w\\,if(lt(t\\,1.5)\\,-w+(w+30)*(t-1.0)/0.5\\,30))':y=h*0.862:"
                f"shadowcolor=black@0.9:shadowx=4:shadowy=4:"
                f"alpha='if(lt(t\\,1.0)\\,0\\,if(lt(t\\,1.5)\\,(t-1.0)/0.5\\,0.75+0.25*sin(t*PI*1.6)))'"
            )

    # ══════════════════════════════════════════════════════════════════
    # CAPA 5 — ARROW BOUNCE  (solo con lower-third activo)
    # Tres chevrons "v" centrados que rebotan sobre el separador
    # Aparecen a los 2.0s, rebotan a 2.5 ciclos por segundo
    # ══════════════════════════════════════════════════════════════════
    if titulo and fa:
        parts.append(
            f"drawtext={fa}text='v   v   v':fontsize=32:fontcolor=0x00E5FF:"
            f"x=(w-text_w)/2:y='h*0.718+9*sin(t*PI*2.5)':"
            f"shadowcolor=0x003366@0.6:shadowx=0:shadowy=5:"
            f"alpha='if(lt(t\\,2.0)\\,0\\,if(lt(t\\,2.6)\\,(t-2.0)/0.6\\,0.88))'"
        )

    # ══════════════════════════════════════════════════════════════════
    # CAPA 6 — CTA BLINK  (solo con lower-third activo)
    # ">> CONSULTAR AHORA" centrado en la barra inferior
    # Aparece a los 3.0s, parpadea suavemente a ~0.7Hz
    # ══════════════════════════════════════════════════════════════════
    if titulo and fa:
        parts.append(
            f"drawtext={fa}text='>> CONSULTAR AHORA':fontsize=32:fontcolor=0x00FF9F:"
            f"x=(w-text_w)/2:y=h*0.928:"
            f"shadowcolor=black@0.9:shadowx=2:shadowy=2:"
            f"alpha='if(lt(t\\,3.0)\\,0\\,if(lt(t\\,3.6)\\,(t-3.0)/0.6\\,0.45+0.55*sin(t*PI*1.4)))'"
        )

    # ══════════════════════════════════════════════════════════════════
    # EFECTOS EXTRA
    # ══════════════════════════════════════════════════════════════════
    _ex = extra_effects or []

    # NEON FRAME PULSANTE — marco 4 lados con glow externo + capa interior pulsante ~1.2Hz
    if 'neon_frame' in _ex:
        parts.append("drawbox=x=0:y=0:w=1080:h=7:color=0x00E5FF@0.35:t=fill:enable='gte(t\\,0.4)'")
        parts.append("drawbox=x=0:y=1913:w=1080:h=7:color=0x00E5FF@0.35:t=fill:enable='gte(t\\,0.4)'")
        parts.append("drawbox=x=0:y=0:w=7:h=1920:color=0x00E5FF@0.35:t=fill:enable='gte(t\\,0.4)'")
        parts.append("drawbox=x=1073:y=0:w=7:h=1920:color=0x00E5FF@0.35:t=fill:enable='gte(t\\,0.4)'")
        parts.append("drawbox=x=0:y=0:w=1080:h=4:color=0x00E5FF@0.92:t=fill:enable='gt(sin(t*PI*1.2)\\,0.2)'")
        parts.append("drawbox=x=0:y=1916:w=1080:h=4:color=0x00E5FF@0.92:t=fill:enable='gt(sin(t*PI*1.2)\\,0.2)'")
        parts.append("drawbox=x=0:y=0:w=4:h=1920:color=0x00E5FF@0.92:t=fill:enable='gt(sin(t*PI*1.2)\\,0.2)'")
        parts.append("drawbox=x=1076:y=0:w=4:h=1920:color=0x00E5FF@0.92:t=fill:enable='gt(sin(t*PI*1.2)\\,0.2)'")
        parts.append("drawbox=x=0:y=0:w=22:h=22:color=0x00E5FF@1.0:t=fill:enable='gte(t\\,0.4)'")
        parts.append("drawbox=x=1058:y=0:w=22:h=22:color=0x00E5FF@1.0:t=fill:enable='gte(t\\,0.4)'")
        parts.append("drawbox=x=0:y=1898:w=22:h=22:color=0x00E5FF@1.0:t=fill:enable='gte(t\\,0.4)'")
        parts.append("drawbox=x=1058:y=1898:w=22:h=22:color=0x00E5FF@1.0:t=fill:enable='gte(t\\,0.4)'")

    # GLITCH CYBERPUNK — 4 bursts dramáticos con flashes largos + aberración RGB intensa
    if 'glitch' in _ex:
        # Burst 1 (t≈0.1s) — doble flash
        parts.append("drawbox=x=0:y=0:w=1080:h=1920:color=white@0.70:t=fill:enable='between(t\\,0.08\\,0.16)'")
        parts.append("drawbox=x=0:y=0:w=1080:h=1920:color=white@0.45:t=fill:enable='between(t\\,0.20\\,0.25)'")
        # Burst 2 (t≈1.2s)
        parts.append("drawbox=x=0:y=0:w=1080:h=1920:color=white@0.60:t=fill:enable='between(t\\,1.18\\,1.28)'")
        # Burst 3 (t≈3.5s)
        parts.append("drawbox=x=0:y=0:w=1080:h=1920:color=white@0.65:t=fill:enable='between(t\\,3.48\\,3.58)'")
        parts.append("drawbox=x=0:y=0:w=1080:h=1920:color=white@0.40:t=fill:enable='between(t\\,3.65\\,3.70)'")
        # Burst 4 mid-video (t≈7s)
        parts.append("drawbox=x=0:y=0:w=1080:h=1920:color=white@0.55:t=fill:enable='between(t\\,6.95\\,7.08)'")
        # Aberración cromática RGB — barras anchas y vistosas
        parts.append("drawbox=x=0:y=180:w=1080:h=22:color=0xFF00CC@0.80:t=fill:enable='between(t\\,0.07\\,0.17)'")
        parts.append("drawbox=x=0:y=620:w=1080:h=16:color=0x00FFCC@0.75:t=fill:enable='between(t\\,0.08\\,0.17)'")
        parts.append("drawbox=x=0:y=1100:w=1080:h=20:color=0xFF3300@0.70:t=fill:enable='between(t\\,0.09\\,0.16)'")
        parts.append("drawbox=x=0:y=320:w=1080:h=18:color=0x0055FF@0.78:t=fill:enable='between(t\\,1.17\\,1.29)'")
        parts.append("drawbox=x=0:y=900:w=1080:h=24:color=0xFF00AA@0.72:t=fill:enable='between(t\\,1.18\\,1.28)'")
        parts.append("drawbox=x=0:y=1500:w=1080:h=16:color=0x00FF55@0.68:t=fill:enable='between(t\\,1.19\\,1.27)'")
        parts.append("drawbox=x=0:y=480:w=1080:h=20:color=0xFF4400@0.75:t=fill:enable='between(t\\,3.47\\,3.59)'")
        parts.append("drawbox=x=0:y=1200:w=1080:h=18:color=0x00CCFF@0.80:t=fill:enable='between(t\\,3.48\\,3.58)'")
        parts.append("drawbox=x=0:y=750:w=1080:h=22:color=0xFF0066@0.72:t=fill:enable='between(t\\,6.94\\,7.09)'")
        parts.append("drawbox=x=0:y=1400:w=1080:h=16:color=0x66FF00@0.68:t=fill:enable='between(t\\,6.95\\,7.08)'")
        # Bloques negros de corrupción
        parts.append("drawbox=x=60:y=200:w=200:h=24:color=black@0.98:t=fill:enable='between(t\\,0.08\\,0.16)'")
        parts.append("drawbox=x=700:y=640:w=260:h=16:color=black@0.95:t=fill:enable='between(t\\,0.08\\,0.17)'")
        parts.append("drawbox=x=200:y=1120:w=300:h=22:color=black@0.98:t=fill:enable='between(t\\,0.09\\,0.16)'")
        parts.append("drawbox=x=400:y=340:w=180:h=20:color=black@0.96:t=fill:enable='between(t\\,1.17\\,1.29)'")
        parts.append("drawbox=x=100:y=920:w=350:h=18:color=black@0.95:t=fill:enable='between(t\\,1.18\\,1.28)'")
        parts.append("drawbox=x=550:y=490:w=220:h=22:color=black@0.97:t=fill:enable='between(t\\,3.47\\,3.59)'")
        parts.append("drawbox=x=80:y=1210:w=280:h=18:color=black@0.96:t=fill:enable='between(t\\,6.94\\,7.09)'")

    # TICKER SCROLLEANTE — banda roja siempre visible + texto si hay fuente
    if 'ticker' in _ex:
        # Banda y decoración siempre visibles (no necesitan fuente)
        parts.append("drawbox=x=0:y=1804:w=1080:h=56:color=0xBB2000@0.96:t=fill:enable='gte(t\\,1.0)'")
        parts.append("drawbox=x=0:y=1804:w=1080:h=3:color=0xFFCC00@1.0:t=fill:enable='gte(t\\,1.0)'")
        parts.append("drawbox=x=0:y=1855:w=1080:h=3:color=0xFF6600@0.6:t=fill:enable='gte(t\\,1.0)'")
        # Badge izquierdo: fondo negro + 3 "puntos" pulsantes simulados con boxes
        parts.append("drawbox=x=0:y=1804:w=130:h=56:color=black@0.94:t=fill:enable='gte(t\\,1.0)'")
        parts.append("drawbox=x=10:y=1820:w=18:h=18:color=0xFFCC00@0.9:t=fill:enable='gt(sin(t*PI*2.5)\\,0.1)'")
        parts.append("drawbox=x=36:y=1820:w=18:h=18:color=0xFFCC00@0.7:t=fill:enable='gt(sin(t*PI*2.5+1)\\,0.1)'")
        parts.append("drawbox=x=62:y=1820:w=18:h=18:color=0xFFCC00@0.5:t=fill:enable='gt(sin(t*PI*2.5+2)\\,0.1)'")
        # Separador vertical
        parts.append("drawbox=x=130:y=1807:w=3:h=50:color=0xFFCC00@0.8:t=fill:enable='gte(t\\,1.0)'")
        # Líneas decorativas "de velocidad" que simulan texto en movimiento
        for _li, (_lx, _lw, _la) in enumerate([(140,180,0.6),(330,220,0.5),(560,160,0.55),(730,180,0.5),(920,140,0.45)]):
            parts.append(f"drawbox=x={_lx}:y=1820:w={_lw}:h=14:color=white@{_la}:t=fill:enable='gte(t\\,1.0)'")
            parts.append(f"drawbox=x={_lx}:y=1838:w={_lw//2}:h=8:color=0xFFCC00@{round(_la-0.1,2)}:t=fill:enable='gte(t\\,1.0)'")
        # Texto scrolleante si hay fuente
        if fa:
            _tk_titulo = titulo if titulo else 'PRECIO ESPECIAL'
            _tk_precio = _esc_dt(precio) if precio else 'CONSULTAR'
            _tk_content = f"{_tk_titulo} · {_tk_precio} · SOLO POR HOY · PEDILO YA · "
            _tk_text = (_tk_content * 4)
            parts.append(
                f"drawtext={fa}text='OFERTA':fontsize=22:fontcolor=0xFFCC00:"
                f"x=10:y=1815:"
                f"shadowcolor=black@0.9:shadowx=1:shadowy=1:"
                f"alpha='if(lt(t\\,1.0)\\,0\\,if(lt(t\\,1.4)\\,(t-1.0)/0.4\\,1))'"
            )
            parts.append(
                f"drawtext={fa}text='{_tk_text}':fontsize=24:fontcolor=white:"
                f"x='1080-140*(t-1.0)':y=1818:"
                f"shadowcolor=black@0.8:shadowx=1:shadowy=1:"
                f"alpha='if(lt(t\\,1.0)\\,0\\,if(lt(t\\,1.4)\\,(t-1.0)/0.4\\,1))'"
            )

    # INTRO CINEMATICO — overlay oscuro escalonado + título central + precio centrado
    if 'intro_cine' in _ex:
        for _ic_a, _ic_t0, _ic_t1 in [
            (0.88, 0.0, 0.5), (0.78, 0.5, 1.0), (0.62, 1.0, 1.5),
            (0.42, 1.5, 2.0), (0.16, 2.0, 2.5)
        ]:
            parts.append(f"drawbox=x=0:y=0:w=1080:h=1920:color=black@{_ic_a}:t=fill:enable='between(t\\,{_ic_t0}\\,{_ic_t1})'")
        # Líneas separadoras cyan siempre visibles (no necesitan fuente)
        parts.append("drawbox=x=60:y=915:w=960:h=2:color=0x00E5FF@0.85:t=fill:enable='between(t\\,0.4\\,2.7)'")
        parts.append("drawbox=x=60:y=1005:w=960:h=2:color=0x00E5FF@0.85:t=fill:enable='between(t\\,0.4\\,2.7)'")
        # Bloque central decorativo (visible sin fuente)
        parts.append("drawbox=x=200:y=920:w=680:h=82:color=0x00E5FF@0.08:t=fill:enable='between(t\\,0.4\\,2.7)'")
        if fa:
            _ic_titulo = titulo or 'EXCLUSIVO'
            parts.append(
                f"drawtext={fa}text='{_ic_titulo}':fontsize=100:fontcolor=white:"
                f"x=(w-text_w)/2:y=855:"
                f"shadowcolor=0x00E5FF@0.65:shadowx=5:shadowy=5:"
                f"alpha='if(lt(t\\,0.3)\\,0\\,if(lt(t\\,1.1)\\,(t-0.3)/0.8\\,if(lt(t\\,2.2)\\,1\\,if(lt(t\\,2.8)\\,(2.8-t)/0.6\\,0))))'"
            )
            if precio:
                parts.append(
                    f"drawtext={fa}text='{precio}':fontsize=72:fontcolor=0xFFCC00:"
                    f"x=(w-text_w)/2:y=1025:"
                    f"shadowcolor=black@0.9:shadowx=3:shadowy=3:"
                    f"alpha='if(lt(t\\,0.7)\\,0\\,if(lt(t\\,1.4)\\,(t-0.7)/0.7\\,if(lt(t\\,2.2)\\,1\\,if(lt(t\\,2.8)\\,(2.8-t)/0.6\\,0))))'"
                )

    # BADGE VIRAL — siempre visible con boxes; texto "VIRAL" si hay fuente
    if 'badge_viral' in _ex:
        # Marco exterior e interior (siempre visible, sin fuente)
        parts.append("drawbox=x=774:y=146:w=292:h=128:color=0xFF4400@0.95:t=fill:enable='gte(t\\,0.7)'")
        parts.append("drawbox=x=778:y=150:w=284:h=120:color=black@0.82:t=fill:enable='gte(t\\,0.7)'")
        parts.append("drawbox=x=774:y=146:w=292:h=5:color=0xFFCC00@1.0:t=fill:enable='gte(t\\,0.7)'")
        parts.append("drawbox=x=774:y=268:w=292:h=5:color=0xFFCC00@0.8:t=fill:enable='gte(t\\,0.7)'")
        # Barra de color pulsante dentro del badge (simula energía aunque no haya texto)
        parts.append("drawbox=x=778:y=150:w=284:h=8:color=0xFF4400@0.8:t=fill:enable='gt(sin(t*PI*2.2)\\,0.0)'")
        # 5 cuadraditos dorados simulando estrellas (siempre visible)
        for _si in range(5):
            _sx = 786 + _si * 52
            parts.append(f"drawbox=x={_sx}:y=228:w=34:h=34:color=0xFFCC00@0.88:t=fill:enable='gte(t\\,1.0)'")
            parts.append(f"drawbox=x={_sx+4}:y=232:w=26:h=26:color=0xFF8800@0.60:t=fill:enable='gte(t\\,1.0)'")
        if fa:
            parts.append(
                f"drawtext={fa}text='VIRAL':fontsize=58:fontcolor=0xFF4400:"
                f"x=812:y=163:"
                f"shadowcolor=black@0.9:shadowx=2:shadowy=2:"
                f"alpha='if(lt(t\\,0.7)\\,0\\,if(lt(t\\,1.3)\\,(t-0.7)/0.6\\,0.75+0.25*sin(t*PI*2.2)))'"
            )

    return ",".join(parts)


def aplicar_watermark_imagen(imagen_path, logo_path=None, opacidad=0.75, posicion='br', margen=25):
    """Aplica logo PNG como watermark sobre imagen usando Pillow. Gratis."""
    lpath = logo_path or LOGO_PATH_DEFAULT
    if not lpath or not os.path.exists(lpath):
        return imagen_path
    try:
        from PIL import Image
        img  = Image.open(imagen_path).convert('RGBA')
        logo = Image.open(lpath).convert('RGBA')

        logo_w = max(80, int(img.width * 0.14))
        logo_h = int(logo.height * logo_w / logo.width)
        logo   = logo.resize((logo_w, logo_h), Image.LANCZOS)

        r, g, b, a = logo.split()
        a = a.point(lambda x: int(x * opacidad))
        logo = Image.merge('RGBA', (r, g, b, a))

        w, h = img.size
        pos_map = {
            'tl': (margen, margen),
            'tr': (w - logo_w - margen, margen),
            'bl': (margen, h - logo_h - margen),
            'br': (w - logo_w - margen, h - logo_h - margen),
            'center': ((w - logo_w) // 2, (h - logo_h) // 2),
        }
        x, y = pos_map.get(posicion, pos_map['br'])
        capa = Image.new('RGBA', img.size, (0, 0, 0, 0))
        capa.paste(logo, (x, y), logo)
        resultado = Image.alpha_composite(img, capa).convert('RGB')
        resultado.save(imagen_path, 'JPEG', quality=92)
        log(f"✅ Watermark aplicado en imagen ({posicion}, {int(opacidad*100)}%)", "success")
        return imagen_path
    except Exception as e:
        log(f"⚠️ Error watermark imagen: {e}. Continuando sin watermark.", "warning")
        return imagen_path


def _overlay_watermark_video(input_path, output_path, logo_path, lower_third=None, extra_effects=None):
    """
    Segunda pasada ffmpeg: aplica motion overlay + watermark sobre un video ya generado.
    Siempre incluye corner accents; si lower_third tiene texto agrega barra + slide-in.
    """
    os.makedirs("static", exist_ok=True)

    fa              = f"fontfile={DRAWTEXT_FONT}:" if DRAWTEXT_FONT else ""
    titulo          = _esc_dt(lower_third['texto'].upper()) if lower_third and lower_third.get('texto') else None
    precio          = _esc_dt(lower_third.get('precio', '')) if lower_third else None
    precio_anterior = _esc_dt(lower_third.get('precio_anterior', '')) if lower_third else None
    color           = lower_third.get('color', 'white') if lower_third else 'white'

    motion_filters = _build_motion_overlay_filter(fa, titulo, precio, color, extra_effects=extra_effects, precio_anterior=precio_anterior)
    wm_w = 220

    try:
        filter_complex = (
            f"[0:v]{motion_filters}[vlt];"
            f"[1:v]scale={wm_w}:-1,format=rgba,colorchannelmixer=aa=0.72[vwm];"
            f"[vlt][vwm]overlay=main_w-overlay_w-30:main_h-overlay_h-30[vout]"
        )
        cmd = [
            "ffmpeg", "-y", "-i", input_path, "-i", logo_path,
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "0:a",
            "-c:v", "libx264", "-preset", "fast", "-crf", "17",
            "-c:a", "copy", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            output_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if res.returncode != 0:
            log(f"⚠️ Motion+watermark falló ({res.stderr[-150:].strip()}). Copiando sin overlay.", "warning")
            import shutil; shutil.copy2(input_path, output_path)
        else:
            log("✅ Motion overlay + watermark aplicados al Reel ✅", "success")
    except Exception as e:
        log(f"⚠️ Error overlay video: {e}", "warning")
        import shutil; shutil.copy2(input_path, output_path)

    return output_path


def _only_lower_third_video(input_path, output_path, lower_third, extra_effects=None):
    """Aplica motion overlay (corner accents + lower-third opcional) sin watermark."""
    fa              = f"fontfile={DRAWTEXT_FONT}:" if DRAWTEXT_FONT else ""
    titulo          = _esc_dt(lower_third['texto'].upper()) if lower_third and lower_third.get('texto') else None
    precio          = _esc_dt(lower_third.get('precio', '')) if lower_third else None
    precio_anterior = _esc_dt(lower_third.get('precio_anterior', '')) if lower_third else None
    color           = lower_third.get('color', 'white') if lower_third else 'white'

    motion_filters = _build_motion_overlay_filter(fa, titulo, precio, color, extra_effects=extra_effects, precio_anterior=precio_anterior)

    try:
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-vf", motion_filters,
            "-c:v", "libx264", "-preset", "fast", "-crf", "17",
            "-c:a", "copy", "-movflags", "+faststart", output_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if res.returncode != 0:
            log(f"⚠️ Motion overlay falló ({res.stderr[-150:].strip()}). Copiando sin motion.", "warning")
            import shutil; shutil.copy2(input_path, output_path)
        else:
            log("✅ Motion overlay aplicado ✅", "success")
    except Exception as e:
        log(f"⚠️ Error motion overlay: {e}", "warning")
        import shutil; shutil.copy2(input_path, output_path)
    return output_path


# ============================================
# FFMPEG — COMBINAR IMAGEN + AUDIO → VIDEO
# ============================================

def generar_video_reel(imagen_path, audio_path, duracion=15, mood="energico",
                       color_grade="none", watermark_path=None, lower_third=None,
                       usar_watermark=True, movimiento=None, extra_effects=None):
    """
    Genera un Reel cinematográfico premium usando ffmpeg (gratis).
    - movimiento: estilo de animación independiente del mood musical
    - mood: solo para selección de música
    - color_grade: 8 presets de corrección de color
    - duracion: 7, 15, 30 o 60 segundos
    Fallback: Ken Burns estándar si cualquier filtro falla.
    """
    import subprocess

    ZOOM_FPS = 30
    TOTAL_FRAMES = duracion * ZOOM_FPS

    # ── Filtros de movimiento (sin scale — se maneja en filter_complex) ────
    # El scale lo hace el filter_complex con fondo blur para preservar AR.
    MOTION_FILTERS = {
        "zoom_dramatico": (
            f"zoompan=z='min(zoom+0.0022,1.40)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={TOTAL_FRAMES}:s=1080x1920:fps={ZOOM_FPS},"
            "vignette=angle=PI/3.5:mode=backward,"
            "eq=saturation=1.30:contrast=1.12:brightness=0.02,"
            "unsharp=5:5:1.5:5:5:0"
        ),
        "ken_burns": (
            f"zoompan=z='min(zoom+0.0012,1.25)':x='(iw/2-(iw/zoom/2))+{TOTAL_FRAMES}*0.15-n*0.15':y='ih/2-(ih/zoom/2)-n*0.08':d={TOTAL_FRAMES}:s=1080x1920:fps={ZOOM_FPS},"
            "eq=saturation=1.15:brightness=0.03,"
            "unsharp=3:3:0.8:3:3:0"
        ),
        "pan_lateral": (
            f"zoompan=z='1.18':x='(iw/2-(iw/zoom/2))+n*0.45':y='ih/2-(ih/zoom/2)':d={TOTAL_FRAMES}:s=1080x1920:fps={ZOOM_FPS},"
            f"fade=t=in:st=0:d=0.8,"
            "eq=saturation=0.95:contrast=1.05:brightness=0.01,"
            "unsharp=3:3:0.6:3:3:0"
        ),
        "pan_vertical": (
            f"zoompan=z='1.18':x='iw/2-(iw/zoom/2)':y='(ih-(ih/zoom))-n*0.40':d={TOTAL_FRAMES}:s=1080x1920:fps={ZOOM_FPS},"
            f"fade=t=in:st=0:d=1.0,"
            "eq=saturation=1.05:contrast=1.08,"
            "unsharp=3:3:0.7:3:3:0"
        ),
        "drift_flotante": (
            f"zoompan=z='1.12+0.06*sin(2*PI*n/{TOTAL_FRAMES})':x='iw/2-(iw/zoom/2)+25*sin(2*PI*n/{TOTAL_FRAMES}/1.5)':y='ih/2-(ih/zoom/2)-18*cos(2*PI*n/{TOTAL_FRAMES}/2)':d={TOTAL_FRAMES}:s=1080x1920:fps={ZOOM_FPS},"
            "vignette=angle=PI/5,"
            "eq=saturation=0.90:contrast=1.02:brightness=0.01,"
            "gblur=sigma=0.5"
        ),
        "revelado": (
            f"zoompan=z='min(zoom+0.0010,1.20)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={TOTAL_FRAMES}:s=1080x1920:fps={ZOOM_FPS},"
            f"fade=t=in:st=0:d=2.5,"
            "vignette=angle=PI/3:mode=backward,"
            "eq=saturation=0.72:contrast=1.20:brightness=-0.05,"
            "unsharp=5:5:1.5:5:5:0"
        ),
        "diagonal": (
            f"zoompan=z='min(zoom+0.0016,1.30)':x='(iw/2-(iw/zoom/2))+n*0.28':y='ih/2-(ih/zoom/2)-n*0.18':d={TOTAL_FRAMES}:s=1080x1920:fps={ZOOM_FPS},"
            "vignette=angle=PI/4:mode=backward,"
            "eq=saturation=1.20:contrast=1.10:brightness=0.02,"
            "unsharp=5:5:1.2:5:5:0"
        ),
        "impacto": (
            f"zoompan=z='max(1.55-0.0030*n,1.05)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={TOTAL_FRAMES}:s=1080x1920:fps={ZOOM_FPS},"
            f"fade=t=in:st=0:d=0.5,"
            "vignette=angle=PI/2.8:mode=backward,"
            "eq=saturation=1.30:contrast=1.20:brightness=0.03,"
            "unsharp=5:5:1.8:5:5:0"
        ),
    }

    _MOOD_TO_MOTION = {
        "energico":    "zoom_dramatico",
        "motivador":   "ken_burns",
        "corporativo": "pan_lateral",
        "relajado":    "drift_flotante",
        "misterioso":  "revelado",
        "alegre":      "diagonal",
    }

    FALLBACK_MOTION = (
        f"zoompan=z='min(zoom+0.0010,1.20)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={TOTAL_FRAMES}:s=1080x1920:fps={ZOOM_FPS},"
        "eq=saturation=1.10:contrast=1.04"
    )

    # ── Seleccionar movimiento y preparar cadena de filtros ─────────────────
    motion_key    = movimiento or _MOOD_TO_MOTION.get(mood, "ken_burns")
    motion_filter = MOTION_FILTERS.get(motion_key, FALLBACK_MOTION)

    cg     = COLOR_GRADES.get(color_grade or "none", "")
    cg_str = "," + cg if cg else ""
    fade_str = f",fade=t=out:st={max(0, duracion-1.2):.1f}:d=1.2"

    def _build_fc(motion_vf):
        """
        filter_complex con fondo blur para preservar el aspect ratio original.
        - bg: imagen escalada para cubrir 2160x3840 (fill) + desenfoque fuerte
        - fg: imagen escalada para caber en 2160x3840 (fit, sin deformar)
        - overlay centrado de fg sobre bg
        - zoompan + color grade + fade aplicado al compuesto
        """
        return (
            "[0:v]scale=2160:3840:force_original_aspect_ratio=increase,"
            "crop=2160:3840,gblur=sigma=45[bg];"
            "[0:v]scale=2160:3840:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[composed];"
            f"[composed]{motion_vf}{cg_str}{fade_str}[out]"
        )

    os.makedirs("static", exist_ok=True)
    ts = int(time.time())
    tmp_path   = f"static/reel_tmp_{ts}.mp4"
    final_path = f"static/reel_{ts}.mp4"

    def _run_ffmpeg(filter_complex, out_path):
        # Sin -loop ni -framerate: la imagen se pasa como un único frame.
        # zoompan con d=TOTAL_FRAMES genera todos los frames animados desde ese
        # único frame, y la variable n va de 0 a TOTAL_FRAMES-1 sin resetear.
        # Con -loop 1 -framerate 30 el input era un stream continuo y n se
        # reseteaba a 0 en cada frame nuevo, rompiendo pan/diagonal/impacto.
        cmd = [
            "ffmpeg", "-y",
            "-i", imagen_path,
            "-i", audio_path,
            "-t", str(duracion),
            "-filter_complex", filter_complex,
            "-map", "[out]", "-map", "1:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-profile:v", "high", "-level", "4.0",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-shortest",
            out_path
        ]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    try:
        wm_log = "+ wm" if usar_watermark and (watermark_path or os.path.exists(LOGO_PATH_DEFAULT)) else ""
        lt_log = "+ lower3rd" if (lower_third and lower_third.get('texto')) else ""
        cg_log = f"+ {color_grade}" if cg else ""
        log(f"🎬 Generando Reel — mov:{motion_key} | música:{mood}{cg_log}{wm_log}{lt_log} ({duracion}s)...", "info")

        result = _run_ffmpeg(_build_fc(motion_filter), tmp_path)

        if result.returncode != 0:
            log(f"⚠️ Filtro '{motion_key}' falló. Reintentando con Ken Burns estándar...", "warning")
            result = _run_ffmpeg(_build_fc(FALLBACK_MOTION), tmp_path)

        if result.returncode != 0:
            log(f"❌ ffmpeg error (fallback): {result.stderr[-300:]}", "error")
            return None

        # ── Segunda pasada: motion overlay + watermark opcional ─────────────
        # El motion overlay (corner accents + lower-third) SIEMPRE se aplica.
        lpath  = watermark_path or (LOGO_PATH_DEFAULT if (usar_watermark and os.path.exists(LOGO_PATH_DEFAULT)) else None)
        use_wm = bool(lpath and os.path.exists(lpath))

        if use_wm:
            _overlay_watermark_video(tmp_path, final_path,
                                     logo_path=lpath,
                                     lower_third=lower_third,
                                     extra_effects=extra_effects)
        else:
            _only_lower_third_video(tmp_path, final_path, lower_third, extra_effects=extra_effects)
        try: os.remove(tmp_path)
        except: pass

        size_mb = os.path.getsize(final_path) / (1024 * 1024)
        log(f"✅ Reel generado → {final_path} ({size_mb:.1f} MB) | mov:{motion_key} | música:{mood}{wm_log}{lt_log}", "success")
        return final_path

    except FileNotFoundError:
        log("❌ ffmpeg no está instalado.", "error")
        return None
    except Exception as e:
        log(f"❌ Error generando video: {e}", "error")
        return None

# ============================================
# GENERACIÓN DE IMAGEN — DINÁMICA DE MODELOS
# ============================================

def generar_imagen_dalle(prompt_imagen, imagen_referencia_url=None, style_weight=0.5):
    """
    Genera imagen con Ideogram v3 Balanced al máximo de sus capacidades.
    Resolución máxima disponible, negative prompt quirúrgico, parámetros de calidad extrema.
    """
    replicate_token = os.environ.get("REPLICATE_API_TOKEN")
    if not replicate_token:
        log("⚠️ REPLICATE_API_TOKEN no configurada. Saltando generación de imagen.", "warning")
        return None
    try:
        import replicate
        import io
        client = replicate.Client(api_token=replicate_token)

        # ── Negative prompt quirúrgico — máxima supresión de artefactos ──────
        negative_prompt = (
            # Texto falso e ilegible — la plaga de los generadores de imagen
            "illegible text, blurry text, garbled text, scrambled letters, random letters, "
            "fake text, lorem ipsum, gibberish words, nonsense text, decorative fake words, "
            "misspelled words, corrupted text, distorted letters, abstract letterforms, "
            "typographic noise, pseudo-text, simulated text, placeholder text, "
            "small print, fine print, body copy text, paragraph text, running text, "
            "background text, texture text, pattern made of letters, text wallpaper, "
            "footer text, header text, disclaimer text, terms and conditions, legal text, "
            "caption text, subtitle text, watermark text, stamp text, "
            # Caracteres no latinos
            "chinese characters, japanese characters, arabic script, cyrillic text, "
            "korean characters, hindi characters, thai script, hebrew letters, "
            "greek letters used decoratively, runes, symbols as text, "
            # Problemas de diseño y calidad
            "cluttered layout, busy background, overlapping elements, messy composition, "
            "stock photo watermark, draft quality, low resolution, pixelated, noisy image, "
            "amateur design, ugly fonts, deformed letters, broken typography, "
            # Artefactos visuales
            "jpeg artifacts, compression artifacts, aliasing, chromatic aberration on edges, "
            "oversaturation, blown highlights, muddy shadows, washed out colors, "
            "plastic skin, uncanny valley, deformed hands, extra fingers, "
            # Elementos no deseados en comercial
            "people in background, crowd, street photography, candid shot, "
            "amateur photography, tourist photo, snapshot aesthetic, "
            "clipart, cartoon unless specified, flat illustration unless specified"
        )

        # ── Parámetros Ideogram v3 Balanced al máximo ────────────────────────
        # Resolución: 1024x1792 es el máximo 9:16 disponible en Ideogram v3
        parametros = {
            "prompt": (
                prompt_imagen
                + "\n\nQUALITY ANCHORS: 8K resolution, hyperdetailed, award-winning commercial photography, "
                "shot on Phase One IQ4 150MP, color graded in DaVinci Resolve, "
                "perfect exposure, tack sharp focus on product hero, "
                "professional studio lighting setup, zero post-processing artifacts. "
                "NO background text patterns. NO decorative letters as texture. "
                "NO small print. NO paragraph text blocks. NO fake words anywhere. "
                "Ultra-clean premium commercial design only."
            ),
            "negative_prompt": negative_prompt,
            "resolution": "768x1344",   # 9:16 válido para Ideogram v3 en Replicate
            "style_type": "Realistic",   # Realismo comercial premium
            "magic_prompt_option": "Auto",  # Ideogram mejora el prompt internamente
            "num_outputs": 1,
        }

        # ── Inyectar referencia de estilo si existe ───────────────────────────
        if imagen_referencia_url:
            log("🖼️ Descargando imagen de referencia de estilo...", "info")
            try:
                img_response = http_session.get(imagen_referencia_url, timeout=15)
                if img_response.status_code == 200:
                    imagen_ref_bytes = io.BytesIO(img_response.content)
                    parametros["style_reference_images"] = [imagen_ref_bytes]
                    parametros["style_type"] = "Auto"   # Auto detecta el estilo de la referencia
                    parametros["style_weight"] = style_weight
                    log(f"✅ Referencia de estilo inyectada (style_weight={style_weight})", "success")
                else:
                    log(f"⚠️ No se pudo descargar referencia (HTTP {img_response.status_code}). Generando sin referencia.", "warning")
            except Exception as ref_err:
                log(f"⚠️ Error cargando referencia: {ref_err}. Continuando sin referencia.", "warning")

        modo_log = "+ referencia de estilo" if imagen_referencia_url else "— 768x1344 9:16"
        log(f"🖼️ Generando con Ideogram v3 Balanced {modo_log}...", "info")

        output = client.run(
            "ideogram-ai/ideogram-v3-balanced",
            input=parametros
        )

        image_url = str(output)
        img_bytes = http_session.get(image_url, timeout=30).content
        os.makedirs("static", exist_ok=True)
        filepath = f"static/img_{int(time.time())}.png"
        with open(filepath, "wb") as f:
            f.write(img_bytes)

        size_kb = len(img_bytes) / 1024
        log(f"🖼️ Imagen generada ✅ — {size_kb:.0f} KB | resolución: 768x1344", "success")
        return filepath

    except Exception as e:
        error_str = str(e)
        if "402" in error_str or "Insufficient credit" in error_str:
            log("💳 Sin créditos en Replicate. Recarga en: https://replicate.com/account/billing", "warning")
        elif "422" in error_str or "invalid" in error_str.lower():
            log(f"⚠️ Parámetro inválido en Ideogram: {e}. Reintentando con configuración base...", "warning")
            # Fallback: si algún parámetro nuevo no está soportado, reintenta con config mínima
            return _generar_imagen_fallback(prompt_imagen, replicate_token)
        else:
            log(f"❌ Error generando imagen: {e}", "error")
        return None


def _generar_imagen_fallback(prompt_imagen, replicate_token):
    """Fallback seguro si Ideogram rechaza parámetros avanzados. Reintenta 1 vez en 429."""
    import replicate
    client = replicate.Client(api_token=replicate_token)
    params = {
        "prompt": prompt_imagen + " Clean premium commercial design. No background text.",
        "negative_prompt": "text, watermark, blurry, low quality, amateur",
        "resolution": "768x1344",
        "style_type": "Design",
        "magic_prompt_option": "Off",
    }
    for intento in range(2):
        try:
            if intento > 0:
                log("⏳ Rate limit — esperando 15s antes de reintentar...", "warning")
                time.sleep(15)
            log(f"🔄 Reintentando con configuración base (intento {intento+1}/2)...", "info")
            output = client.run("ideogram-ai/ideogram-v3-balanced", input=params)
            image_url = str(output)
            img_bytes = http_session.get(image_url, timeout=30).content
            os.makedirs("static", exist_ok=True)
            filepath = f"static/img_{int(time.time())}_fb.png"
            with open(filepath, "wb") as f:
                f.write(img_bytes)
            log("🖼️ Imagen generada con fallback ✅", "success")
            return filepath
        except Exception as e2:
            if "429" in str(e2) and intento == 0:
                continue
            log(f"❌ Fallback falló: {e2}", "error")
            if "429" in str(e2):
                log("💳 Rate limit activo — recarga créditos en replicate.com/account/billing para aumentar el límite.", "warning")
            return None
    return None

# ============================================
# FLUX 1.1 PRO — MOTOR DE IMAGEN PREMIUM
# ============================================

def generar_imagen_flux(prompt_imagen, imagen_referencia_url=None):
    """
    Genera imagen con FLUX 1.1 Pro de Black Forest Labs via Replicate.
    Superior en fotorrealismo y calidad de producto vs Ideogram.
    Ideal para: tecnología, gaming, electrónica, productos sin texto.
    Costo: ~$0.04 USD por imagen (~$36 CLP).
    Fallback automático a Ideogram v3 si falla.
    """
    replicate_token = os.environ.get("REPLICATE_API_TOKEN")
    if not replicate_token:
        log("⚠️ REPLICATE_API_TOKEN no configurada. Usando Ideogram como fallback.", "warning")
        return generar_imagen_dalle(prompt_imagen, imagen_referencia_url)
    try:
        import replicate
        import io
        client = replicate.Client(api_token=replicate_token)

        # Prompt enriquecido para FLUX — optimizado para fotografía de producto comercial
        prompt_flux = (
            prompt_imagen
            + " | FLUX QUALITY ANCHORS: professional product photography, studio lighting, "
            "8K ultra-detailed, sharp focus, commercial advertising quality, "
            "clean composition, premium aesthetic, photorealistic render, "
            "cinematic color grading, no text, no watermark, no logo"
        )

        parametros = {
            "prompt": prompt_flux,
            "aspect_ratio": "9:16",
            "output_format": "png",
            "output_quality": 100,
            "safety_tolerance": 2,
            "prompt_upsampling": True,
        }

        # Si hay imagen de referencia, usarla como image_prompt (FLUX Redux)
        if imagen_referencia_url:
            try:
                img_response = http_session.get(imagen_referencia_url, timeout=15)
                if img_response.status_code == 200:
                    parametros["image_prompt"] = io.BytesIO(img_response.content)
                    parametros["image_prompt_strength"] = 0.15
                    log("✅ Referencia de estilo inyectada en FLUX", "success")
            except Exception as ref_err:
                log(f"⚠️ No se pudo cargar referencia para FLUX: {ref_err}", "warning")

        log("🖤 Generando con FLUX 1.1 Pro (fotorrealismo premium)...", "info")
        output = client.run("black-forest-labs/flux-1.1-pro", input=parametros)
        image_url = str(output)

        img_bytes = http_session.get(image_url, timeout=30).content
        os.makedirs("static", exist_ok=True)
        filepath = f"static/flux_{int(time.time())}.png"
        with open(filepath, "wb") as f:
            f.write(img_bytes)

        size_kb = len(img_bytes) / 1024
        log(f"🖼️ Imagen FLUX 1.1 Pro generada ✅ — {size_kb:.0f} KB | 9:16 vertical", "success")
        return filepath

    except Exception as e:
        error_str = str(e)
        if "402" in error_str or "Insufficient credit" in error_str:
            log("💳 Sin créditos en Replicate para FLUX. Recarga en: https://replicate.com/account/billing", "warning")
        elif "429" in error_str:
            log("⏳ Rate limit en FLUX. Reintentando con Ideogram...", "warning")
        else:
            log(f"❌ Error generando imagen con FLUX: {e}", "error")
        log("🔄 Fallback automático a Ideogram v3...", "info")
        return generar_imagen_dalle(prompt_imagen, imagen_referencia_url)


def seleccionar_motor_imagen(busqueda, motor_forzado=None):
    """
    Selecciona automáticamente el mejor motor de imagen según el tipo de producto.
    - FLUX 1.1 Pro: tecnología, gaming, electrónica, streaming, productos sin texto visible.
    - Ideogram v3: productos que requieren texto legible en la imagen, banners, diseño gráfico.
    Puede ser forzado con motor_forzado='flux' o motor_forzado='ideogram'.
    """
    if motor_forzado == 'flux':
        log("🖤 Motor forzado: FLUX 1.1 Pro", "info")
        return 'flux'
    if motor_forzado == 'ideogram':
        log("🎨 Motor forzado: Ideogram v3", "info")
        return 'ideogram'

    keywords_flux = [
        'xbox', 'playstation', 'nintendo', 'gaming', 'game pass', 'ps5', 'ps4',
        'disney', 'netflix', 'spotify', 'apple', 'iphone', 'samsung', 'laptop',
        'auricular', 'headset', 'teclado', 'mouse', 'monitor', 'consola',
        'duolingo', 'canva', 'software', 'app', 'suscripción', 'streaming',
        'producto', 'electrónica', 'gadget', 'tech', 'tecnología', 'vpn',
        'antivirus', 'office', 'windows', 'adobe', 'youtube', 'twitch'
    ]
    keywords_ideogram = [
        'oferta', 'descuento', 'precio', 'promo', 'banner', 'flyer', 'cartel',
        'texto', 'letras', 'tipografía', 'diseño', 'poster', 'anuncio', 'cupón'
    ]

    busqueda_lower = busqueda.lower()
    score_flux     = sum(1 for k in keywords_flux     if k in busqueda_lower)
    score_ideogram = sum(1 for k in keywords_ideogram if k in busqueda_lower)

    if score_flux >= score_ideogram:
        log(f"🖤 Motor auto-seleccionado: FLUX 1.1 Pro (score flux={score_flux} vs ideogram={score_ideogram})", "info")
        return 'flux'
    else:
        log(f"🎨 Motor auto-seleccionado: Ideogram v3 (score ideogram={score_ideogram} vs flux={score_flux})", "info")
        return 'ideogram'


# ============================================
# CLOUDINARY — SUBIR VIDEO
# ============================================

def subir_video_a_cdn(video_path):
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME")
    api_key = os.environ.get("CLOUDINARY_API_KEY")
    api_secret = os.environ.get("CLOUDINARY_API_SECRET")
    if cloud_name and api_key and api_secret:
        try:
            import cloudinary
            import cloudinary.uploader
            cloudinary.config(cloud_name=cloud_name, api_key=api_key, api_secret=api_secret)
            result = cloudinary.uploader.upload(video_path, resource_type="video", folder="reels")
            url = result.get("secure_url")
            if url:
                log("☁️ Video subido a Cloudinary ✅", "success")
                return url
        except Exception as e:
            log(f"⚠️ Error subiendo a Cloudinary: {e}", "warning")
    log("⚠️ Sin CDN de video configurado.", "warning")
    return None

# ============================================
# GRAPH API — PUBLICAR REEL
# ============================================

def publicar_reel_instagram(video_path, caption, cliente_id="aurakey"):
    cliente = CLIENTES.get(cliente_id)
    if not cliente:
        log(f"❌ Cliente '{cliente_id}' no encontrado.", "error")
        return False
    meta_token = cliente.get("meta_token")
    ig_user_id = cliente.get("ig_user_id")
    if not meta_token or not ig_user_id:
        log(f"⚠️ Credenciales no configuradas para {cliente['nombre']}.", "warning")
        return False
    try:
        video_url = subir_video_a_cdn(video_path)
        if not video_url:
            log("❌ No se pudo obtener URL pública del video.", "error")
            return False
        log(f"📤 Creando contenedor Reel en Graph API para {cliente['nombre']}...", "info")
        res = http_session.post(
            f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media",
            data={"media_type": "REELS", "video_url": video_url, "caption": caption, "access_token": meta_token}
        )
        container_id = res.json().get("id")
        if not container_id:
            log(f"❌ Error creando contenedor Reel: {res.json()}", "error")
            return False
        log("⏳ Esperando que Meta procese el video...", "info")
        listo = False
        # ⚡ Polling adaptativo para Reels: empieza en 4s, sube gradualmente
        tiempos_espera_reel = [4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 10]
        for intento, espera in enumerate(tiempos_espera_reel):
            time.sleep(espera)
            check = http_session.get(
                f"https://graph.facebook.com/{GRAPH_API_VERSION}/{container_id}",
                params={"fields": "status_code", "access_token": meta_token}
            ).json()
            status = check.get("status_code")
            log(f"📡 Estado Reel ({intento+1}/15): {status}", "info")
            if status == "FINISHED":
                listo = True
                break
            elif status == "ERROR":
                log(f"❌ Meta rechazó el video: {check}", "error")
                return False
        if not listo:
            log("❌ Timeout: Meta no procesó el Reel.", "error")
            return False
        log(f"🚀 Publicando Reel en Instagram de {cliente['nombre']}...", "info")
        res2 = http_session.post(
            f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media_publish",
            data={"creation_id": container_id, "access_token": meta_token}
        )
        data2 = res2.json()
        if data2.get("id"):
            log(f"🎬 Reel publicado! ID: {data2['id']}", "success")
            return True
        else:
            log(f"❌ Error publicando Reel: {data2}", "error")
            return False
    except Exception as e:
        log(f"❌ Error en Graph API (Reel): {e}", "error")
        return False

# ============================================
# OVERLAY DE TEXTO CON GLOW NEÓN — PILLOW
# ============================================

def aplicar_overlay_texto(imagen_path, texto, posicion='center', glow_color='#00e5ff',
                          font_size_override=None, x_frac=None, y_frac=None):
    """Dibuja texto con banda oscura + glow neón. Visible sobre cualquier fondo.

    Si se pasan font_size_override (px del slider del dashboard), x_frac e y_frac
    (coordenadas 0-1 del canvas preview), se usan esos valores exactos en lugar de
    calcularlos automaticamente. Lo que el usuario vio en preview es lo que sale.
    """
    try:
        import textwrap
        img = Image.open(imagen_path).convert("RGBA")
        w, h = img.size

        # ── Tamano de fuente ──────────────────────────────────────────────────
        # Prioridad: valor del slider del dashboard → fallback automatico
        if font_size_override and int(font_size_override) > 0:
            # El slider fue disenado para un canvas de ~400px de ancho (preview).
            # Escalamos al ancho real de la imagen para mantener proporciones.
            scale = w / 400.0
            font_size = max(12, int(int(font_size_override) * scale))
        else:
            font_size = max(40, int(w * 0.07))  # fallback mas discreto

        font = None
        font_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
            "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        ]
        for fp in font_candidates:
            if os.path.exists(fp):
                try:
                    font = ImageFont.truetype(fp, font_size)
                    break
                except Exception:
                    continue
        if font is None:
            font = ImageFont.load_default()

        # Parsear color glow hex → RGB
        glow_hex = glow_color.lstrip('#')
        glow_rgb = tuple(int(glow_hex[i:i+2], 16) for i in (0, 2, 4))

        # Wrap de texto
        max_chars = max(8, int(w / (font_size * 0.62)))
        lines = textwrap.wrap(texto.upper(), width=max_chars)
        if not lines:
            return imagen_path

        # Medir bloque de texto
        dummy_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        line_heights, line_widths = [], []
        for line in lines:
            bbox = dummy_draw.textbbox((0, 0), line, font=font)
            line_widths.append(bbox[2] - bbox[0])
            line_heights.append(bbox[3] - bbox[1])
        line_spacing = int(font_size * 0.30)
        block_h = sum(line_heights) + line_spacing * (len(lines) - 1)
        max_line_w = max(line_widths)

        # Padding interno de la banda
        band_pad_v = int(font_size * 0.55)
        band_pad_h = int(font_size * 0.70)
        band_h = block_h + band_pad_v * 2
        band_w = min(w, max_line_w + band_pad_h * 2)

        # ── Calcular posicion Y ───────────────────────────────────────────────
        # Prioridad: coordenadas exactas del canvas (y_frac) →
        #            luego string 'top'/'bottom'/'center'
        if y_frac is not None:
            # y_frac es el centro del bloque en el canvas preview (0.0 - 1.0)
            band_y = int(float(y_frac) * h) - band_h // 2
            band_y = max(0, min(h - band_h, band_y))
        else:
            padding = int(h * 0.07)
            if posicion == 'top':
                band_y = padding
            elif posicion == 'bottom':
                band_y = h - band_h - padding
            else:  # center
                band_y = (h - band_h) // 2

        block_y = band_y + band_pad_v

        # ── Calcular posicion X ───────────────────────────────────────────────
        if x_frac is not None:
            band_x = int(float(x_frac) * w) - band_w // 2
            band_x = max(0, min(w - band_w, band_x))
        else:
            band_x = (w - band_w) // 2

        # ── Capa 1: fondo negro semitransparente limpio (sin borde ni blur) ──
        band_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        band_draw  = ImageDraw.Draw(band_layer)
        band_draw.rectangle(
            [band_x, band_y, band_x + band_w, band_y + band_h],
            fill=(0, 0, 0, 160)
        )

        # ── Capa 2: glow amplio del texto (halo exterior de color) ──
        glow_layer2 = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        glow_draw2  = ImageDraw.Draw(glow_layer2)
        cur_y = block_y
        for i, line in enumerate(lines):
            x = band_x + (band_w - line_widths[i]) // 2
            glow_draw2.text((x, cur_y), line, font=font, fill=(*glow_rgb, 90))
            cur_y += line_heights[i] + line_spacing
        glow_layer2 = glow_layer2.filter(ImageFilter.GaussianBlur(radius=font_size // 2))

        # ── Capa 3: glow medio del texto (halo interior mas intenso) ──
        glow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        glow_draw  = ImageDraw.Draw(glow_layer)
        cur_y = block_y
        for i, line in enumerate(lines):
            x = band_x + (band_w - line_widths[i]) // 2
            for alpha in [180, 140, 100]:
                glow_draw.text((x, cur_y), line, font=font, fill=(*glow_rgb, alpha))
            cur_y += line_heights[i] + line_spacing
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=font_size // 5))

        # ── Capa 4: texto blanco nitido con sombra oscura ──
        text_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        text_draw  = ImageDraw.Draw(text_layer)
        cur_y = block_y
        for i, line in enumerate(lines):
            x = band_x + (band_w - line_widths[i]) // 2
            for dx, dy in [(3, 3), (2, 2), (-1, -1)]:
                text_draw.text((x + dx, cur_y + dy), line, font=font, fill=(0, 0, 0, 180))
            text_draw.text((x, cur_y), line, font=font, fill=(255, 255, 255, 255))
            cur_y += line_heights[i] + line_spacing

        # Combinar: imagen → fondo → glow exterior → glow interior → texto
        resultado = img.copy()
        resultado = Image.alpha_composite(resultado, band_layer)
        resultado = Image.alpha_composite(resultado, glow_layer2)
        resultado = Image.alpha_composite(resultado, glow_layer)
        resultado = Image.alpha_composite(resultado, text_layer)

        # Guardar como JPEG
        resultado_rgb = resultado.convert("RGB")
        out_path = imagen_path.replace(".jpg", "_overlay.jpg").replace(".png", "_overlay.jpg")
        resultado_rgb.save(out_path, "JPEG", quality=92)
        log(f"✍️ Overlay de texto aplicado ✅ — '{texto}' ({posicion}, glow #{glow_hex})", "success")
        return out_path

    except ImportError:
        log("⚠️ Pillow no está instalado. Ejecuta: pip install Pillow", "error")
        return imagen_path
    except Exception as e:
        log(f"⚠️ Error aplicando overlay: {e}. Continuando sin texto.", "warning")
        return imagen_path


# ============================================
# GRAPH API — PUBLICAR POST
# ============================================

def subir_imgbb(filepath):
    imgbb_key = os.environ.get("IMGBB_API_KEY")
    if not imgbb_key:
        log("⚠️ IMGBB_API_KEY no configurada.", "warning")
        return None
    try:
        with open(filepath, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        res = http_session.post("https://api.imgbb.com/1/upload", data={"key": imgbb_key, "image": img_b64})
        url = res.json().get("data", {}).get("url")
        if url:
            log("☁️ Imagen subida a ImgBB ✅", "success")
        return url
    except Exception as e:
        log(f"❌ Error subiendo a ImgBB: {e}", "error")
        return None

def publicar_en_instagram(imagen_path, caption, cliente_id="aurakey"):
    cliente = CLIENTES.get(cliente_id)
    if not cliente:
        log(f"❌ Cliente '{cliente_id}' no encontrado.", "error")
        return False
    meta_token = cliente.get("meta_token")
    ig_user_id = cliente.get("ig_user_id")
    if not meta_token or not ig_user_id:
        log(f"⚠️ Credenciales no configuradas para {cliente['nombre']}.", "warning")
        return False
    try:
        imagen_url = subir_imgbb(imagen_path)
        if not imagen_url:
            log("❌ No se pudo obtener URL pública de la imagen.", "error")
            return False
        log(f"📤 Creando contenedor en Graph API para {cliente['nombre']}...", "info")
        res = http_session.post(
            f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media",
            data={"image_url": imagen_url, "caption": caption, "access_token": meta_token}
        )
        container_id = res.json().get("id")
        if not container_id:
            log(f"❌ Error creando contenedor: {res.json()}", "error")
            return False
        log("⏳ Esperando que Meta procese la imagen...", "info")
        listo = False
        # ⚡ Polling adaptativo: empieza rápido (2s) y aumenta si Meta tarda
        tiempos_espera = [2, 2, 3, 3, 4, 4, 5, 5, 6, 6]
        for intento, espera in enumerate(tiempos_espera):
            time.sleep(espera)
            check = http_session.get(
                f"https://graph.facebook.com/{GRAPH_API_VERSION}/{container_id}",
                params={"fields": "status_code", "access_token": meta_token}
            ).json()
            status = check.get("status_code")
            log(f"📡 Estado contenedor ({intento+1}/10): {status}", "info")
            if status == "FINISHED":
                listo = True
                break
            elif status == "ERROR":
                log(f"❌ Meta rechazó la imagen: {check}", "error")
                return False
        if not listo:
            log("❌ Timeout: Meta no procesó la imagen.", "error")
            return False
        log(f"🚀 Publicando en Instagram de {cliente['nombre']}...", "info")
        res2 = http_session.post(
            f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media_publish",
            data={"creation_id": container_id, "access_token": meta_token}
        )
        data2 = res2.json()
        if data2.get("id"):
            log(f"✅ Post publicado! ID: {data2['id']}", "success")
            return True
        else:
            log(f"❌ Error publicando: {data2}", "error")
            return False
    except Exception as e:
        log(f"❌ Error en Graph API: {e}", "error")
        return False

# ============================================
# CICLO PRINCIPAL
# ============================================

def ciclo_libre(busqueda, precio_manual="No especificado", cliente_id="aurakey", mood="energico",
                hacer_reel=True, imagen_referencia_url=None, style_weight=0.5, titulo_producto=None,
                color_grade="none", lower_third=None, usar_watermark=True, duracion_reel=15,
                movimiento=None, extra_effects=None, hashtags_override=None, producto_id=None,
                motor_imagen=None):
    global bot_activo

    # 🔒 Check-and-set atómico: evita que dos ciclos corran al mismo tiempo
    with _bot_lock:
        if bot_activo:
            log("⚠️ Ciclo rechazado: ya hay un ciclo activo. Esperá que termine.", "warning")
            return
        bot_activo = True

    socketio.emit('bot_status', {'activo': True})
    cliente = CLIENTES.get(cliente_id, CLIENTES["aurakey"])
    nombre_cliente = cliente["nombre"]

    # titulo_producto = nombre exacto del producto (campo nuevo del dashboard)
    # busqueda = contexto/descripción libre de lo que se quiere vender
    # Si ambos existen y son distintos, combinamos para darle al LLM el máximo contexto
    if titulo_producto and titulo_producto.lower() != busqueda.lower():
        detalle = f"{titulo_producto} — {busqueda}"
    else:
        detalle = titulo_producto or busqueda

    log(f'🔍 Ciclo libre para "{detalle}" — Cliente: {nombre_cliente}...', 'info')
    prod_info = {
        "nombre": nombre_cliente,
        "titulo_producto": titulo_producto or busqueda,
        "detalle_producto": detalle,
        "keyword_busqueda": (titulo_producto or busqueda).split()[0],
        "nicho": titulo_producto or busqueda,
        "tono": "profesional, vendedor, directo y confiable"
    }
    try:

        # ⚡ OPTIMIZACIÓN: buscar_tendencias y normalizar_producto corren en PARALELO
        # Ahorro estimado: ~2-4s (antes eran secuenciales)
        log(f'🔍 Preparando ciclo en paralelo para "{detalle}"...', 'info')
        with ThreadPoolExecutor(max_workers=2) as executor:
            fut_tendencias = executor.submit(buscar_tendencias_reales_api, prod_info)
            fut_ficha      = executor.submit(normalizar_producto_info, titulo_producto or busqueda, None)
            tendencias_reales = fut_tendencias.result()
            ficha             = fut_ficha.result()

        gancho_usado = f"Tendencias en vivo: {', '.join(tendencias_reales[:2])}"
        prod_info['ficha'] = ficha
        prod_info['titulo_producto'] = ficha.get("nombre") or prod_info['titulo_producto']
        log(f'✍️ Redactando post para "{prod_info["titulo_producto"]}"...', 'info')

        # Incrementar uso del producto si viene desde el catálogo
        if producto_id:
            _db_producto_usado(producto_id)

        # ⚡ OPTIMIZACIóN: caption + vision corren en PARALELO cuando hay imagen de referencia
        # Ahorro estimado: ~3-6s cuando hay imagen de referencia
        descripcion_referencia = None
        tiene_ref = bool(imagen_referencia_url and isinstance(imagen_referencia_url, str) and imagen_referencia_url.startswith("http"))

        if tiene_ref:
            log('💬 Generando caption + analizando imagen de referencia en paralelo...', 'info')
            with ThreadPoolExecutor(max_workers=2) as executor:
                fut_caption = executor.submit(
                    generar_post_estricto, prod_info, tendencias_reales, precio_manual,
                    hashtags_override
                )
                fut_vision  = executor.submit(analizar_imagen_referencia, imagen_referencia_url)
                caption_completo = fut_caption.result()
                try:
                    descripcion_referencia = fut_vision.result()
                except Exception as e_vision:
                    log(f"⚠️ Groq Vision falló ({e_vision}). Continuando sin referencia.", "warning")
                    descripcion_referencia = None
                    imagen_referencia_url  = None
        else:
            caption_completo = generar_post_estricto(prod_info, tendencias_reales, precio_manual, hashtags_override=hashtags_override)

        log(f'🎨 Generando prompt visual para "{busqueda}"...', 'info')
        prompt_imagen = generar_prompt_imagen(
            prod_info,
            caption_completo,
            con_referencia=bool(imagen_referencia_url),
            descripcion_referencia=descripcion_referencia
        )
        
        imagen_filepath = None
        imagen_url_publica = None
        reel_generado = False  # Se actualiza al generar el video más adelante

        # Influencia 100% → usar la imagen de referencia directamente, sin IA
        if imagen_referencia_url and style_weight >= 1.0:
            log("🖼️ Influencia 100% — usando imagen de referencia tal cual (sin generar nueva)...", "info")
            try:
                img_bytes = http_session.get(imagen_referencia_url, timeout=20).content
                os.makedirs("static", exist_ok=True)
                imagen_filepath = f"static/img_ref_{int(time.time())}.jpg"
                with open(imagen_filepath, "wb") as _f:
                    _f.write(img_bytes)
                log(f"✅ Imagen de referencia descargada ({len(img_bytes)//1024} KB)", "success")
            except Exception as e_ref:
                log(f"⚠️ No se pudo descargar la referencia ({e_ref}). Generando con IA...", "warning")
                imagen_filepath = generar_imagen_dalle(prompt_imagen, imagen_referencia_url, style_weight=0.99)
        else:
            # Selección automática del motor según el tipo de producto (o forzado desde el dashboard)
            motor = seleccionar_motor_imagen(busqueda, motor_forzado=motor_imagen)
            if motor == 'flux':
                imagen_filepath = generar_imagen_flux(prompt_imagen, imagen_referencia_url)
            else:
                # Ideogram v3 — mejor para productos con texto visible
                imagen_filepath = generar_imagen_dalle(prompt_imagen, imagen_referencia_url, style_weight=style_weight)

                # Aplicar watermark a la imagen (si está configurado)
        if imagen_filepath and usar_watermark and os.path.exists(LOGO_PATH_DEFAULT):
            imagen_filepath = aplicar_watermark_imagen(imagen_filepath, posicion='br')

        # ⚡ OPTIMIZACIÓN: subir imagen a ImgBB en background thread mientras el bot
        # prepara la entrada del borrador (ahorra ~1-2s de espera bloqueante)
        imagen_url_publica = None
        if imagen_filepath:
            # Subir a ImgBB directamente — el with cierra el executor y espera el resultado
            with ThreadPoolExecutor(max_workers=1) as executor:
                fut_imgbb = executor.submit(subir_imgbb, imagen_filepath)
                imagen_url_publica = fut_imgbb.result()  # Bloqueante dentro del with (correcto)

        # Nuevo flujo seguro: generar primero, publicar solo después de aprobación manual.
        publicado = False
        entrada = {
            'id': _nuevo_borrador_id(),
            'cliente': f"{nombre_cliente} — {busqueda.upper()}",
            'cliente_id': cliente_id,
            'tendencia': gancho_usado,
            'caption': caption_completo,
            'prompt_imagen': prompt_imagen,
            'imagen_url': imagen_url_publica or '',
            'imagen_path_local': imagen_filepath,
            'publicado': publicado,
            'estado': 'pendiente',
            'tipo_publicacion': 'reel' if hacer_reel else 'post',
            'mood': mood,
            'color_grade': color_grade or 'none',
            'movimiento': movimiento,
            'duracion_reel': int(duracion_reel or 15),
            'lower_third': lower_third or {},
            'extra_effects': extra_effects or [],
            'usar_watermark': usar_watermark,
            'reel_generado': reel_generado,  # Error 8 fix: usar la variable correcta
            'con_referencia': bool(imagen_referencia_url),
            'fecha': datetime.now().strftime('%d/%m %H:%M')
        }
        captions_guardados.insert(0, entrada)
        _db_save_caption(entrada)
        socketio.emit('caption', entrada)
        log(f'✅ Borrador generado — pendiente de aprobación para {"Reel" if hacer_reel else "Post"}', 'success')
    except Exception as e:
        clave_debug = os.environ.get("GROQ_API_KEY", "NO_ENCONTRADA")
        log(f'❌ Error en ciclo libre: {e} | GROQ_KEY: {clave_debug[:6]}...{clave_debug[-4:]} | len:{len(clave_debug)}', 'error')
    finally:
        # 🔒 Liberar el lock de forma segura al terminar (o si hubo error)
        with _bot_lock:
            bot_activo = False
        socketio.emit('bot_status', {'activo': False})
    socketio.emit('stats', stats_global)

# ============================================
# RUTAS API
# ============================================

@app.route('/')
@requiere_auth
def index():
    return render_template('dashboard.html')

@app.route('/api/clientes')
@requiere_auth
def api_clientes():
    lista = [{"id": k, "nombre": v["nombre"]} for k, v in CLIENTES.items()]
    return jsonify(lista)

@app.route('/api/stats')
@requiere_auth
def api_stats():
    return jsonify(stats_global)

@app.route('/api/captions')
@requiere_auth
def api_captions():
    return jsonify(captions_guardados)

@app.route('/api/subir_referencia', methods=['POST'])
@requiere_auth
def api_subir_referencia():
    if 'imagen' not in request.files:
        return jsonify({'error': 'No se envió imagen'}), 400
    archivo = request.files['imagen']
    os.makedirs("static", exist_ok=True)
    filepath = f"static/ref_{int(time.time())}.png"
    archivo.save(filepath)
    url = subir_imgbb(filepath)
    if url:
        log("🖼️ Imagen de referencia subida ✅", "success")
        return jsonify({'url': url})
    return jsonify({'error': 'No se pudo subir la imagen a ImgBB'}), 500

@app.route('/api/subir_imagen_propia', methods=['POST'])
@requiere_auth
def api_subir_imagen_propia():
    if 'imagen' not in request.files:
        return jsonify({'error': 'No se envió imagen'}), 400
    archivo = request.files['imagen']
    os.makedirs("static", exist_ok=True)
    filepath = f"static/propia_{int(time.time())}.jpg"
    archivo.save(filepath)
    url = subir_imgbb(filepath)
    if url:
        log("🖼️ Imagen propia subida ✅", "success")
        return jsonify({'url': url})
    return jsonify({'error': 'No se pudo subir la imagen a ImgBB'}), 500


def _nuevo_borrador_id():
    return f"draft_{int(time.time() * 1000)}"


def _buscar_borrador(borrador_id):
    for entrada in captions_guardados:
        if entrada.get('id') == borrador_id:
            return entrada
    return None


def publicar_post_instagram_url(imagen_url, caption, cliente_id="aurakey"):
    """Publica un post usando una URL pública ya existente de la imagen."""
    cliente = CLIENTES.get(cliente_id)
    if not cliente:
        log(f"❌ Cliente '{cliente_id}' no encontrado.", "error")
        return False
    meta_token = cliente.get("meta_token")
    ig_user_id = cliente.get("ig_user_id")
    if not meta_token or not ig_user_id:
        log(f"⚠️ Credenciales no configuradas para {cliente['nombre']}.", "warning")
        return False
    try:
        log(f"📤 Creando contenedor Post en Graph API para {cliente['nombre']}...", "info")
        res = http_session.post(
            f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media",
            data={"image_url": imagen_url, "caption": caption, "access_token": meta_token}
        )
        container_id = res.json().get("id")
        if not container_id:
            log(f"❌ Error creando contenedor Post: {res.json()}", "error")
            return False
        log("⏳ Esperando que Meta procese la imagen...", "info")
        listo = False
        for intento in range(10):
            time.sleep(4)
            check = http_session.get(
                f"https://graph.facebook.com/{GRAPH_API_VERSION}/{container_id}",
                params={"fields": "status_code", "access_token": meta_token}
            ).json()
            status = check.get("status_code")
            log(f"📡 Estado Post ({intento+1}/10): {status}", "info")
            if status == "FINISHED":
                listo = True
                break
            if status == "ERROR":
                log(f"❌ Meta rechazó el post: {check}", "error")
                return False
        if not listo:
            log("❌ Timeout: Meta no procesó la imagen.", "error")
            return False
        res2 = http_session.post(
            f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media_publish",
            data={"creation_id": container_id, "access_token": meta_token}
        )
        data2 = res2.json()
        if data2.get("id"):
            log(f"📸 Post publicado! ID: {data2['id']}", "success")
            return True
        log(f"❌ Error publicando Post: {data2}", "error")
        return False
    except Exception as e:
        log(f"❌ Error publicando Post desde URL: {e}", "error")
        return False


def generar_borrador_imagen_propia_task(imagen_url, cliente_id, precio, modo, mood, overlay, titulo_producto=None,
                                         color_grade="none", movimiento=None, duracion=15):
    """Analiza imagen, aplica overlay opcional, genera caption y guarda un borrador pendiente."""
    global bot_activo
    with _bot_lock:
        if bot_activo:
            log("⚠️ Ya hay un ciclo corriendo.", "warning")
            return
        bot_activo = True
    socketio.emit('bot_status', {'activo': True})

    try:
        cliente = CLIENTES.get(cliente_id)
        if not cliente:
            log(f"❌ Cliente '{cliente_id}' no encontrado.", "error")
            return

        img_bytes = http_session.get(imagen_url, timeout=30).content
        os.makedirs("static", exist_ok=True)
        img_path = f"static/propia_work_{int(time.time())}.jpg"
        with open(img_path, "wb") as f:
            f.write(img_bytes)

        if overlay and overlay.get('texto'):
            log(f"✍️ Aplicando texto '{overlay['texto']}' en posición {overlay.get('posicion', 'center')}...", "info")
            img_path = aplicar_overlay_texto(
                img_path,
                texto=overlay['texto'],
                posicion=overlay.get('posicion', 'center'),
                glow_color=overlay.get('glow_color', '#00e5ff'),
                font_size_override=overlay.get('font_size'),
                x_frac=overlay.get('x'),
                y_frac=overlay.get('y'),
            )

        imagen_url_final = subir_imgbb(img_path)
        if not imagen_url_final:
            log("⚠️ No se pudo subir imagen procesada. Usando original.", "warning")
            imagen_url_final = imagen_url

        log("🔍 Detectando producto en la imagen...", "info")
        img_response = http_session.get(imagen_url_final, timeout=15)
        img_b64 = base64.b64encode(img_response.content).decode("utf-8")
        content_type = img_response.headers.get("Content-Type", "image/jpeg")
        media_type = "image/png" if "png" in content_type else "image/jpeg"

        if titulo_producto:
            vision_text = (
                f"The user says this image is about: '{titulo_producto}'. "
                "Look at the image and confirm if that's correct, then provide: "
                "1) The exact commercial product name. 2) Main benefit. 3) Target audience. "
                "Max 80 words. Spanish preferred, but English brand names are fine."
            )
        else:
            vision_text = (
                "You are a product analyst. Look at this image and identify: "
                "1) Exact commercial product name and brand. 2) Main benefit. 3) Target audience. "
                "If you see text in the image, use it. Max 80 words. Spanish preferred."
            )

        vision_response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{img_b64}"}},
                {"type": "text", "text": vision_text}
            ]}],
            max_tokens=150,
            temperature=0.1,
        )
        descripcion_producto = vision_response.choices[0].message.content.strip()
        log(f"✅ Producto detectado: {descripcion_producto[:100]}...", "success")

        nombre_final = titulo_producto if titulo_producto else descripcion_producto.split(".")[0]
        detalle_final = f"{titulo_producto}. {descripcion_producto}" if titulo_producto else descripcion_producto
        ficha = normalizar_producto_info(titulo_producto, descripcion_producto)
        prod_info = {
            'nombre': cliente['nombre'],
            'titulo_producto': ficha.get("nombre") or nombre_final,
            'detalle_producto': detalle_final,
            'keyword_busqueda': (ficha.get("nombre") or nombre_final).split()[0],
            'ficha': ficha,
        }
        tendencias = buscar_tendencias_reales_api(prod_info)
        caption = generar_post_estricto(prod_info, tendencias, precio)
        log("✍️ Caption generado ✅ — queda pendiente de aprobación", "success")

        entrada = {
            'id': _nuevo_borrador_id(),
            'cliente': cliente['nombre'],
            'cliente_id': cliente_id,
            'tendencia': tendencias[0] if tendencias else '—',
            'caption': caption,
            'prompt_imagen': f"[Imagen propia{' + overlay: ' + overlay['texto'] if overlay and overlay.get('texto') else ''}]",
            'imagen_url': imagen_url_final,
            'imagen_path_local': img_path,
            'publicado': False,
            'estado': 'pendiente',
            'tipo_publicacion': modo,
            'mood': mood,
            'color_grade': color_grade,
            'movimiento': movimiento,
            'duracion_reel': duracion,
            'reel_generado': False,
            'con_referencia': False,
            'fecha': datetime.now().strftime('%d/%m %H:%M')
        }
        captions_guardados.insert(0, entrada)
        _db_save_caption(entrada)
        socketio.emit('caption', entrada)
        log("📝 Borrador listo. Revísalo y apruébalo desde el panel.", "success")
        socketio.emit('stats', stats_global)

    except Exception as e:
        log(f"❌ Error generando borrador de imagen propia: {e}", "error")
    finally:
        with _bot_lock:
            bot_activo = False
        socketio.emit('bot_status', {'activo': False})


def publicar_imagen_propia_task(imagen_url, cliente_id, precio, modo, mood, overlay, titulo_producto=None,
                                color_grade="none", movimiento=None, duracion=15, lower_third=None,
                                extra_effects=None):
    """Analiza imagen con visión, aplica overlay opcional, genera caption y publica."""
    global bot_activo
    with _bot_lock:
        if bot_activo:
            log("⚠️ Ya hay un ciclo corriendo.", "warning")
            return
        bot_activo = True
    socketio.emit('bot_status', {'activo': True})

    try:
        cliente = CLIENTES.get(cliente_id)
        if not cliente:
            log(f"❌ Cliente '{cliente_id}' no encontrado.", "error")
            return

        # 1. Descargar imagen localmente para procesarla
        img_bytes = http_session.get(imagen_url, timeout=30).content
        os.makedirs("static", exist_ok=True)
        img_path = f"static/propia_work_{int(time.time())}.jpg"
        with open(img_path, "wb") as f:
            f.write(img_bytes)

        # 2. Aplicar overlay de texto si el usuario lo configuró
        if overlay and overlay.get('texto'):
            log(f"✍️ Aplicando texto '{overlay['texto']}' en posición {overlay['posicion']}...", "info")
            img_path = aplicar_overlay_texto(
                img_path,
                texto=overlay['texto'],
                posicion=overlay.get('posicion', 'center'),
                glow_color=overlay.get('glow_color', '#00e5ff'),
                font_size_override=overlay.get('font_size'),
                x_frac=overlay.get('x'),
                y_frac=overlay.get('y'),
            )

        # 3. Subir imagen procesada (con o sin overlay) a ImgBB
        imagen_url_final = subir_imgbb(img_path)
        if not imagen_url_final:
            log("⚠️ No se pudo subir imagen procesada. Usando original.", "warning")
            imagen_url_final = imagen_url

        # 4. Analizar imagen con Groq Vision para detectar el producto
        log("🔍 Detectando producto en la imagen...", "info")
        img_response = http_session.get(imagen_url_final, timeout=15)
        img_b64 = base64.b64encode(img_response.content).decode("utf-8")
        content_type = img_response.headers.get("Content-Type", "image/jpeg")
        media_type = "image/png" if "png" in content_type else "image/jpeg"

        # Si hay título manual, se lo pasamos a Vision como pista para que confirme/enriquezca
        if titulo_producto:
            vision_text = (
                f"The user says this image is about: '{titulo_producto}'. "
                f"Look at the image and confirm if that's correct, then provide: "
                f"1) The exact commercial product name (use the user's title if visible or confirmed). "
                f"2) What it does / its main benefit in one sentence. "
                f"3) Target audience (gamers, students, professionals, etc.). "
                f"Max 80 words. Spanish preferred, but English brand names are fine."
            )
        else:
            vision_text = (
                "You are a product analyst. Look at this image and identify: "
                "1) Exact commercial product name and brand (be specific — not 'software box' but 'Kaspersky Total Security'). "
                "2) What it does / its main benefit in one sentence. "
                "3) Target audience (gamers, students, professionals, etc.). "
                "If you see text in the image, use it — it's the most reliable source. "
                "Max 80 words. Spanish preferred, but English brand names are fine."
            )

        vision_response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{img_b64}"}},
                {"type": "text", "text": vision_text}
            ]}],
            max_tokens=150,
            temperature=0.1,
        )
        descripcion_producto = vision_response.choices[0].message.content.strip()
        log(f"✅ Producto detectado: {descripcion_producto[:100]}...", "success")

        # Título manual tiene prioridad; Vision enriquece con contexto de beneficio y audiencia
        nombre_final   = titulo_producto if titulo_producto else descripcion_producto.split(".")[0]
        detalle_final  = f"{titulo_producto}. {descripcion_producto}" if titulo_producto else descripcion_producto

        # 5. Normalizar ficha del producto antes de pasarla al copywriter
        ficha = normalizar_producto_info(titulo_producto, descripcion_producto)

        prod_info = {
            'nombre': cliente['nombre'],
            'titulo_producto': ficha.get("nombre") or nombre_final,
            'detalle_producto': detalle_final,
            'keyword_busqueda': (ficha.get("nombre") or nombre_final).split()[0],
            'ficha': ficha,
        }
        tendencias = buscar_tendencias_reales_api(prod_info)
        caption = generar_post_estricto(prod_info, tendencias, precio)
        log("✍️ Caption generado ✅", "success")

        meta_token = cliente['meta_token']
        ig_user_id = cliente['ig_user_id']
        publicado = False
        reel_generado = False

        if modo == 'reel':
            audio_path = buscar_musica_pixabay(mood or "energico")
            video_path = None
            if audio_path:
                lt = lower_third or (
                    {'texto': titulo_producto, 'precio': precio, 'color': 'white'}
                    if titulo_producto else None
                )
                video_path = generar_video_reel(
                    img_path, audio_path,
                    duracion=int(duracion or 15),
                    mood=mood or "energico",
                    color_grade=color_grade or "none",
                    movimiento=movimiento or None,
                    lower_third=lt,
                    extra_effects=extra_effects or [],
                )
                reel_generado = bool(video_path)
            if video_path and meta_token and ig_user_id:
                publicado = publicar_reel_instagram(video_path, caption, cliente_id)
        else:
            if meta_token and ig_user_id:
                publicado = publicar_en_instagram(imagen_url_final, caption, cliente_id)

        if publicado:
            log(f"✅ {'Reel' if modo == 'reel' else 'Post'} publicado en Instagram ✅", "success")
        else:
            log("⚠️ Generado pero no publicado en Instagram.", "warning")

        # Guardar en historial del dashboard
        entrada = {
            'id': _nuevo_borrador_id(),
            'cliente': cliente['nombre'],
            'cliente_id': cliente_id,
            'tendencia': tendencias[0] if tendencias else '—',
            'caption': caption,
            'prompt_imagen': f"[Imagen propia{' + overlay: ' + overlay['texto'] if overlay and overlay.get('texto') else ''}]",
            'imagen_url': imagen_url_final,
            'publicado': publicado,
            'tipo_publicacion': modo,
            'mood': mood,
            'reel_generado': reel_generado,
            'con_referencia': False,
            'fecha': datetime.now().strftime('%d/%m %H:%M')
        }
        captions_guardados.insert(0, entrada)
        _db_save_caption(entrada)
        socketio.emit('caption', entrada)
        # Error 4 fix: inicializar stats si el cliente_id no existe aún
        if cliente_id not in stats_global:
            stats_global[cliente_id] = {'posts': 0, 'ultimo_ciclo': '—', 'nombre': cliente_id}
        stats_global[cliente_id]['posts'] += 1
        stats_global[cliente_id]['ultimo_ciclo'] = datetime.now().strftime('%d/%m %H:%M')
        _db_save_stats()
        socketio.emit('stats', stats_global)
    except Exception as e:
        log(f"❌ Error en publicar imagen propia: {e}", "error")
    finally:
        with _bot_lock:
            bot_activo = False
        socketio.emit('bot_status', {'activo': False})


@app.route('/api/generar_imagen_propia', methods=['POST'])
@requiere_auth
def api_generar_imagen_propia():
    data = request.get_json() or {}
    imagen_url = (data.get('imagen_url') or '').strip()
    cliente_id = data.get('cliente_id', 'aurakey')
    precio = data.get('precio', 'Consultar por DM')
    modo = data.get('modo', 'post')
    mood = data.get('mood', 'energico')
    overlay = data.get('overlay', None)
    titulo_producto = (data.get('titulo_producto') or '').strip() or None
    color_grade = data.get('color_grade', 'none')
    movimiento = data.get('movimiento') or None
    duracion = int(data.get('duracion', 15))

    if not imagen_url:
        return jsonify({'ok': False, 'msg': '⚠️ No se recibió URL de imagen.'})
    with _bot_lock:
        if bot_activo:
            return jsonify({'ok': False, 'msg': '⚠️ Ya hay un proceso corriendo. Esperá que termine.'})

    hilo = threading.Thread(
        target=generar_borrador_imagen_propia_task,
        args=(imagen_url, cliente_id, precio, modo, mood, overlay, titulo_producto,
              color_grade, movimiento, duracion),
        daemon=True
    )
    hilo.start()
    tipo = "Reel" if modo == "reel" else "Post"
    overlay_info = f" + texto '{overlay['texto']}'" if overlay and overlay.get('texto') else ""
    titulo_info = f" | producto: {titulo_producto}" if titulo_producto else ""
    return jsonify({'ok': True, 'msg': f'🧠 Generando borrador como {tipo}{overlay_info}{titulo_info}...'})


@app.route('/api/publicar_borrador', methods=['POST'])
@requiere_auth
def api_publicar_borrador():
    global bot_activo
    data = request.get_json() or {}
    borrador_id = data.get('id')
    caption_editado = (data.get('caption') or '').strip()
    borrador = _buscar_borrador(borrador_id)

    if not borrador:
        return jsonify({'ok': False, 'msg': '⚠️ No encontré ese borrador.'})
    if borrador.get('publicado'):
        return jsonify({'ok': False, 'msg': '⚠️ Ese borrador ya fue publicado.'})
    with _bot_lock:
        if bot_activo:
            return jsonify({'ok': False, 'msg': '⚠️ Ya hay un proceso corriendo. Esperá que termine.'})
        bot_activo = True
    socketio.emit('bot_status', {'activo': True})

    try:
        cliente_id = borrador.get('cliente_id', 'aurakey')
        caption_final = caption_editado or borrador.get('caption', '')
        modo = borrador.get('tipo_publicacion', 'post')
        publicado = False
        reel_generado = False

        log(f"🚀 Publicando borrador aprobado como {'Reel' if modo == 'reel' else 'Post'}...", "info")
        if modo == 'reel':
            img_path = borrador.get('imagen_path_local')
            if not img_path or not os.path.exists(img_path):
                img_bytes = http_session.get(borrador.get('imagen_url'), timeout=30).content
                os.makedirs('static', exist_ok=True)
                img_path = f"static/reel_aprobado_{int(time.time())}.jpg"
                with open(img_path, 'wb') as f:
                    f.write(img_bytes)
            audio_path = buscar_musica_pixabay(borrador.get('mood') or 'energico')
            video_path = generar_video_reel(
                img_path, audio_path,
                duracion=int(borrador.get('duracion_reel', 15)),
                mood=borrador.get('mood') or 'energico',
                color_grade=borrador.get('color_grade', 'none'),
                movimiento=borrador.get('movimiento') or None,
                lower_third=borrador.get('lower_third') or None,
                usar_watermark=borrador.get('usar_watermark', True),
                extra_effects=borrador.get('extra_effects') or [],
            ) if audio_path else None
            reel_generado = bool(video_path)
            publicado = publicar_reel_instagram(video_path, caption_final, cliente_id) if video_path else False
        else:
            publicado = publicar_post_instagram_url(borrador.get('imagen_url'), caption_final, cliente_id)

        borrador['caption'] = caption_final
        borrador['publicado'] = publicado
        borrador['estado'] = 'publicado' if publicado else 'error_publicacion'
        borrador['reel_generado'] = reel_generado
        borrador['fecha_publicacion'] = datetime.now().strftime('%d/%m %H:%M')
        _db_update_caption(borrador_id, {
            'caption': caption_final,
            'publicado': publicado,
            'estado': borrador['estado'],
            'reel_generado': reel_generado,
            'fecha_publicacion': borrador['fecha_publicacion'],
        })

        if publicado:
            # Error 5 fix: inicializar stats si el cliente_id no existe aún
            if cliente_id not in stats_global:
                stats_global[cliente_id] = {'posts': 0, 'ultimo_ciclo': '—', 'nombre': cliente_id}
            stats_global[cliente_id]['posts'] += 1
            stats_global[cliente_id]['ultimo_ciclo'] = datetime.now().strftime('%d/%m %H:%M')
            _db_save_stats()
            socketio.emit('stats', stats_global)
            return jsonify({'ok': True, 'msg': f"✅ {'Reel' if modo == 'reel' else 'Post'} publicado en Instagram.", 'entrada': borrador})
        return jsonify({'ok': False, 'msg': f"⚠️ No se pudo publicar el {'Reel' if modo == 'reel' else 'Post'}. Revisa los logs.", 'entrada': borrador})
    except Exception as e:
        log(f"❌ Error publicando borrador: {e}", "error")
        return jsonify({'ok': False, 'msg': f'❌ Error publicando borrador: {e}'})
    finally:
        with _bot_lock:
            bot_activo = False
        socketio.emit('bot_status', {'activo': False})


@app.route('/api/publicar_imagen_propia', methods=['POST'])
@requiere_auth
def api_publicar_imagen_propia():
    data = request.get_json() or {}
    imagen_url = (data.get('imagen_url') or '').strip()
    cliente_id = data.get('cliente_id', 'aurakey')
    precio = data.get('precio', 'Consultar por DM')
    modo = data.get('modo', 'post')
    mood = data.get('mood', 'energico')
    overlay = data.get('overlay', None)
    titulo_producto = (data.get('titulo_producto') or '').strip() or None
    color_grade = data.get('color_grade', 'none')
    movimiento = data.get('movimiento') or None
    duracion = int(data.get('duracion', 15))
    lower_third = data.get('lower_third', None)
    extra_effects = data.get('extra_effects') or []

    if not imagen_url:
        return jsonify({'ok': False, 'msg': '⚠️ No se recibió URL de imagen.'})
    with _bot_lock:
        if bot_activo:
            return jsonify({'ok': False, 'msg': '⚠️ Ya hay un ciclo corriendo. Esperá que termine.'})

    hilo = threading.Thread(
        target=publicar_imagen_propia_task,
        args=(imagen_url, cliente_id, precio, modo, mood, overlay, titulo_producto,
              color_grade, movimiento, duracion, lower_third, extra_effects),
        daemon=True
    )
    hilo.start()
    tipo = "Reel" if modo == "reel" else "Post"
    overlay_info = f" + texto '{overlay['texto']}'" if overlay and overlay.get('texto') else ""
    titulo_info  = f" | producto: {titulo_producto}" if titulo_producto else ""
    return jsonify({'ok': True, 'msg': f'✅ Procesando imagen propia como {tipo}{overlay_info}{titulo_info}...'})


@app.route('/api/ciclo', methods=['POST'])
@requiere_auth
def api_ciclo():
    data = request.get_json() or {}
    precio = data.get('precio', 'Consultar por interno')
    busqueda_libre = (data.get('busqueda_libre') or '').strip()
    titulo_producto = (data.get('titulo_producto') or '').strip() or None
    cliente_id = data.get('cliente_id', 'aurakey')
    mood = data.get('mood', 'energico')
    hacer_reel = data.get('hacer_reel', True)
    imagen_referencia_url = data.get('imagen_referencia_url', None)
    style_weight = float(data.get('style_weight', 0.5) or 0.5)
    color_grade = data.get('color_grade', 'none')
    lower_third = data.get('lower_third', None)
    usar_watermark = bool(data.get('usar_watermark', True))
    duracion_reel = int(data.get('duracion_reel', 15))
    movimiento = data.get('movimiento') or None
    extra_effects     = data.get('extra_effects') or []
    hashtags_override = data.get('hashtags_override') or None
    producto_id       = data.get('producto_id') or None
    motor_imagen      = data.get('motor_imagen') or None  # 'flux', 'ideogram' o None (auto)
    if not busqueda_libre:
        return jsonify({'msg': '⚠️ Se requiere búsqueda libre para iniciar un ciclo.'})
    with _bot_lock:
        if bot_activo:
            return jsonify({'msg': '⚠️ Ya hay un ciclo corriendo. Esperá que termine antes de iniciar otro.'})
    hilo = threading.Thread(
        target=ciclo_libre,
        kwargs={
            'busqueda': busqueda_libre, 'precio_manual': precio,
            'cliente_id': cliente_id, 'mood': mood, 'hacer_reel': hacer_reel,
            'imagen_referencia_url': imagen_referencia_url, 'style_weight': style_weight,
            'titulo_producto': titulo_producto, 'color_grade': color_grade,
            'lower_third': lower_third, 'usar_watermark': usar_watermark,
            'duracion_reel': duracion_reel, 'movimiento': movimiento,
            'extra_effects': extra_effects, 'hashtags_override': hashtags_override,
            'producto_id': producto_id, 'motor_imagen': motor_imagen,
        }
    )
    hilo.daemon = True
    hilo.start()
    detalle_log = titulo_producto or busqueda_libre
    return jsonify({'msg': f'Ciclo iniciado para: {detalle_log} (mood:{mood}, grade:{color_grade}, wm:{usar_watermark}, dur:{duracion_reel}s)'})

# ============================================
# SCHEDULER CONFIGURABLE
# ============================================

scheduler_config = {
    "activo": False,
    "intervalo_minutos": 120,
    "busqueda": "",
    "titulo_producto": None,
    "precio": "Consultar por DM",
    "cliente_id": "aurakey",
    "mood": "energico",
    "hacer_reel": True,
    "imagen_referencia_url": None,
    "style_weight": 0.5,
    "proximo_ciclo": None,
    "ciclos_ejecutados": 0,
}

def _ejecutar_ciclo_scheduler():
    if not scheduler_config["activo"]:
        return
    with _bot_lock:
        if bot_activo:
            log("⏰ Scheduler: ciclo anterior aún activo, se omite esta ejecución.", "warning")
            return
    log("⏰ Scheduler: disparando ciclo automático...", "info")
    scheduler_config["ciclos_ejecutados"] += 1
    minutos = scheduler_config["intervalo_minutos"]
    proximo = datetime.now() + timedelta(minutes=minutos)
    scheduler_config["proximo_ciclo"] = proximo.strftime("%d/%m %H:%M")
    socketio.emit("scheduler_status", scheduler_config)
    hilo = threading.Thread(
        target=ciclo_libre,
        kwargs={
            "busqueda": scheduler_config["busqueda"],
            "precio_manual": scheduler_config["precio"],
            "cliente_id": scheduler_config["cliente_id"],
            "mood": scheduler_config["mood"],
            "hacer_reel": scheduler_config["hacer_reel"],
            "imagen_referencia_url": scheduler_config["imagen_referencia_url"],
            "style_weight": scheduler_config["style_weight"],
            "titulo_producto": scheduler_config.get("titulo_producto", None),
        },
        daemon=True
    )
    hilo.start()

def _aplicar_schedule():
    schedule.clear("auto")
    if scheduler_config["activo"] and scheduler_config["busqueda"]:
        minutos = scheduler_config["intervalo_minutos"]
        schedule.every(minutos).minutes.do(_ejecutar_ciclo_scheduler).tag("auto")
        proximo = datetime.now() + timedelta(minutes=minutos)
        scheduler_config["proximo_ciclo"] = proximo.strftime("%d/%m %H:%M")
        label = f"{minutos}m" if minutos < 60 else f"{minutos//60}h"
        log(f"⏰ Scheduler activado — cada {label} | próximo: {scheduler_config['proximo_ciclo']}", "success")
    else:
        scheduler_config["proximo_ciclo"] = None
        log("⏹ Scheduler detenido.", "warning")
    socketio.emit("scheduler_status", scheduler_config)

def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(30)


@app.route("/api/scheduler", methods=["GET"])
@requiere_auth
def api_scheduler_get():
    return jsonify(scheduler_config)

@app.route("/api/estado", methods=["GET"])
@requiere_auth
def api_estado():
    """Estado actual del bot — el dashboard lo consulta para habilitar/deshabilitar botones."""
    with _bot_lock:
        ocupado = bot_activo
    return jsonify({"bot_activo": ocupado})

@app.route("/api/scheduler", methods=["POST"])
@requiere_auth
def api_scheduler_set():
    data = request.get_json() or {}
    scheduler_config["activo"] = bool(data.get("activo", False))
    # Soporte para intervalo_minutos (nuevo) e intervalo_horas (legado)
    if "intervalo_minutos" in data:
        scheduler_config["intervalo_minutos"] = int(data["intervalo_minutos"])
    elif "intervalo_horas" in data:
        scheduler_config["intervalo_minutos"] = int(data["intervalo_horas"]) * 60
    scheduler_config["busqueda"] = (data.get("busqueda") or "").strip()
    scheduler_config["titulo_producto"] = (data.get("titulo_producto") or "").strip() or None
    scheduler_config["precio"] = data.get("precio", "Consultar por DM")
    scheduler_config["cliente_id"] = data.get("cliente_id", "aurakey")
    scheduler_config["mood"] = data.get("mood", "energico")
    scheduler_config["hacer_reel"] = bool(data.get("hacer_reel", True))
    scheduler_config["imagen_referencia_url"] = data.get("imagen_referencia_url", None)
    scheduler_config["style_weight"] = float(data.get("style_weight", 0.5) or 0.5)
    _aplicar_schedule()
    return jsonify({"ok": True, "config": scheduler_config})


# ============================================
# NUEVAS RUTAS PREMIUM
# ============================================

@app.route('/api/upload_logo', methods=['POST'])
@requiere_auth
def api_upload_logo():
    if 'logo' not in request.files:
        return jsonify({'ok': False, 'msg': 'No se envió archivo'}), 400
    archivo = request.files['logo']
    os.makedirs("static/logo", exist_ok=True)
    filepath = LOGO_PATH_DEFAULT
    archivo.save(filepath)
    log(f"🏷️ Logo watermark subido ✅ → {filepath}", "success")
    return jsonify({'ok': True, 'path': filepath, 'msg': f'Logo guardado en {filepath}'})

@app.route('/api/logo_status', methods=['GET'])
@requiere_auth
def api_logo_status():
    exists = os.path.exists(LOGO_PATH_DEFAULT)
    size = os.path.getsize(LOGO_PATH_DEFAULT) // 1024 if exists else 0
    return jsonify({'ok': True, 'existe': exists, 'path': LOGO_PATH_DEFAULT, 'kb': size})

@app.route('/api/color_grades', methods=['GET'])
@requiere_auth
def api_color_grades():
    return jsonify(list(COLOR_GRADES.keys()))

@app.route('/api/profiles', methods=['GET'])
@requiere_auth
def api_profiles_get():
    return jsonify(_db_load_profiles())

@app.route('/api/profiles', methods=['POST'])
@requiere_auth
def api_profiles_post():
    data = request.get_json() or {}
    nombre = (data.get('nombre') or '').strip()
    if not nombre:
        return jsonify({'ok': False, 'msg': 'El perfil necesita un nombre'}), 400
    profile = {
        'id': f"profile_{int(time.time() * 1000)}",
        'nombre': nombre,
        'busqueda': (data.get('busqueda') or '').strip(),
        'titulo_producto': (data.get('titulo_producto') or '').strip(),
        'precio': (data.get('precio') or '').strip(),
        'mood': data.get('mood', 'energico'),
        'cliente_id': data.get('cliente_id', 'aurakey'),
        'fecha': datetime.now().strftime('%d/%m/%Y'),
    }
    _db_save_profile(profile)
    return jsonify({'ok': True, 'profile': profile})

@app.route('/api/profiles/<profile_id>', methods=['DELETE'])
@requiere_auth
def api_profiles_delete(profile_id):
    ok = _db_delete_profile(profile_id)
    return jsonify({'ok': ok})


# ============================================
# CATÁLOGO DE PRODUCTOS — CRUD
# ============================================

@app.route('/api/productos', methods=['GET'])
@requiere_auth
def api_productos_get():
    return jsonify(_db_load_productos())

@app.route('/api/productos', methods=['POST'])
@requiere_auth
def api_productos_post():
    data = request.get_json() or {}
    nombre = (data.get('nombre') or '').strip()
    if not nombre:
        return jsonify({'ok': False, 'msg': 'Nombre requerido'})
    pid = f"prod_{int(time.time())}_{random.randint(100,999)}"
    producto = {
        'id': pid,
        'nombre': nombre,
        'precio': data.get('precio',''),
        'precio_anterior': data.get('precio_anterior',''),
        'duracion': data.get('duracion',''),
        'categoria': data.get('categoria','streaming'),
        'descripcion': data.get('descripcion',''),
        'imagen_ref_url': data.get('imagen_ref_url',''),
        'motion': data.get('motion','zoom_dramatico'),
        'color_grade': data.get('color_grade','cinematico'),
        'extra_effects': data.get('extra_effects',[]),
        'hashtags': data.get('hashtags',[]),
        'lower_third_color': data.get('lower_third_color','white'),
        'mood': data.get('mood','energico'),
        'duracion_reel': int(data.get('duracion_reel',15)),
        'veces_usado': 0,
        'ultimo_uso': '',
        'fecha_creacion': datetime.now().strftime('%d/%m/%Y'),
    }
    _db_save_producto(producto)
    log(f"🗂 Producto guardado: {nombre}", "success")
    return jsonify({'ok': True, 'producto': producto})

@app.route('/api/productos/<pid>', methods=['PUT'])
@requiere_auth
def api_productos_put(pid):
    data = request.get_json() or {}
    _db_update_producto(pid, data)
    return jsonify({'ok': True})

@app.route('/api/productos/<pid>', methods=['DELETE'])
@requiere_auth
def api_productos_delete(pid):
    _db_delete_producto(pid)
    return jsonify({'ok': True})

@app.route('/api/productos/<pid>/prefill', methods=['GET'])
@requiere_auth
def api_productos_prefill(pid):
    """Devuelve todos los settings de un producto para pre-llenar el formulario de ciclo."""
    productos = _db_load_productos()
    p = next((x for x in productos if x['id'] == pid), None)
    if not p:
        return jsonify({'ok': False, 'msg': 'Producto no encontrado'})
    # Construir hashtags rotados para este producto
    hashtags = p.get('hashtags') or _get_hashtags_rotados(p.get('categoria','general'), p.get('veces_usado',0))
    # Construir lower_third con precio anterior si existe
    lower_third = None
    if p.get('nombre'):
        lower_third = {
            'texto': p['nombre'].upper(),
            'precio': p.get('precio',''),
            'precio_anterior': p.get('precio_anterior',''),
            'color': p.get('lower_third_color','white'),
        }
    return jsonify({
        'ok': True,
        'producto': p,
        'lower_third': lower_third,
        'hashtags_override': hashtags,
    })

# ============================================
# PREVIEW RÁPIDO DE DISEÑO (Pillow — sin FFmpeg)
# ============================================

@app.route('/api/tendencias', methods=['GET'])
@requiere_auth
def api_tendencias():
    """Tendencias de Chile. Con ?q=keyword busca términos relacionados; sin él devuelve tendencias del día."""
    q = request.args.get('q', '').strip()

    if q:
        try:
            import urllib.parse, threading
            tendencias = []
            errors = []

            CHROME_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

            def _fetch_suggest():
                try:
                    url = f"https://suggestqueries.google.com/complete/search?output=firefox&q={urllib.parse.quote(q)}&hl=es&gl=CL"
                    r = req.get(url, timeout=8, headers={'User-Agent': CHROME_UA, 'Accept-Language': 'es-CL,es;q=0.9'})
                    suggestions = r.json()[1] if r.ok else []
                    for s in suggestions:
                        tendencias.append({'termino': s, 'trafico': '', 'contexto': '', 'fuente': 'Google Suggest', 'imagen': ''})
                except Exception as e:
                    errors.append(f"suggest:{e}")

            def _fetch_trends():
                try:
                    from motor_tendencias import MotorTendenciasChile
                    motor = MotorTendenciasChile()
                    items = motor.obtener_tendencias_google(limite=20, nicho='general')
                    ql = q.lower()
                    for t in items:
                        termino = t.get('termino', '').lower()
                        contexto = t.get('contexto', '').lower()
                        if ql in termino or ql in contexto:
                            if not any(x['termino'].lower() == t.get('termino', '').lower() for x in tendencias):
                                tendencias.insert(0, t)
                except Exception as e:
                    errors.append(f"trends:{e}")

            t1 = threading.Thread(target=_fetch_trends)
            t2 = threading.Thread(target=_fetch_suggest)
            t1.start(); t2.start()
            t1.join(); t2.join()

            return jsonify({'ok': True, 'q': q, 'tendencias': tendencias[:20], 'total': len(tendencias), 'errors': errors if errors else None})
        except Exception as e:
            return jsonify({'ok': False, 'q': q, 'tendencias': [], 'total': 0, 'error': str(e)})

    nicho = request.args.get('nicho', 'general').lower()
    try:
        from motor_tendencias import MotorTendenciasChile
        motor = MotorTendenciasChile()
        tendencias = motor.obtener_tendencias_google(limite=15, nicho=nicho)
        return jsonify({'ok': True, 'nicho': nicho, 'tendencias': tendencias, 'total': len(tendencias)})
    except Exception as e:
        return jsonify({'ok': False, 'nicho': nicho, 'tendencias': [], 'total': 0, 'error': str(e)})

@app.route('/api/preview', methods=['POST'])
@requiere_auth
def api_preview():
    """Genera una imagen de preview del Lower-Third sobre la imagen de referencia."""
    try:
        data       = request.get_json() or {}
        img_url    = data.get('imagen_url','')
        titulo     = (data.get('titulo') or '').upper()
        precio     = data.get('precio','')
        precio_ant = data.get('precio_anterior','')
        color_map  = {'white':'#ffffff','yellow':'#ffc040','cyan':'#00e5ff','green':'#00e87a','red':'#ff4560'}
        color_hex  = color_map.get(data.get('color','white'),'#ffffff')

        # Descargar imagen
        if img_url.startswith('http'):
            img_bytes = http_session.get(img_url, timeout=15).content
        else:
            return jsonify({'ok': False, 'msg': 'Se necesita URL de imagen'})

        img = Image.open(__import__('io').BytesIO(img_bytes)).convert('RGBA')
        # Escalar a 540x960 para preview rápido
        img = img.resize((540, 960), Image.LANCZOS)
        W, H = img.size
        draw = ImageDraw.Draw(img)

        # Barra inferior
        bar_y = int(H * 0.74)
        draw.rectangle([(0, bar_y), (W, H)], fill=(0,0,0,190))
        draw.rectangle([(0, bar_y), (W, bar_y+2)], fill=(0,229,255,242))

        # Intentar fuente del sistema
        try:
            fnt_big   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 30)
            fnt_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
            fnt_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
        except:
            fnt_big = fnt_title = fnt_small = ImageFont.load_default()

        y_title = bar_y + 18
        if titulo:
            draw.text((15, y_title), titulo[:28], font=fnt_title, fill=color_hex)
        if precio_ant:
            draw.text((15, y_title + 32), precio_ant, font=fnt_small, fill='#888888')
            # Línea de tachado
            bb = draw.textbbox((15, y_title+32), precio_ant, font=fnt_small)
            draw.line([(15, (bb[1]+bb[3])//2), (bb[2], (bb[1]+bb[3])//2)], fill='#ff3333', width=2)
        if precio:
            draw.text((15, y_title + (60 if precio_ant else 32)), precio, font=fnt_big, fill='#ffc040')

        # Corner accents
        draw.rectangle([(10,15),(50,17)], fill='#00e5ff')
        draw.rectangle([(10,15),(12,45)], fill='#00e5ff')
        draw.rectangle([(W-50,15),(W-10,17)], fill='#00e5ff')
        draw.rectangle([(W-12,15),(W-10,45)], fill='#00e5ff')

        # Guardar y devolver como base64
        import io as _io, base64 as _b64
        buf = _io.BytesIO()
        img.convert('RGB').save(buf, 'JPEG', quality=85)
        b64 = _b64.b64encode(buf.getvalue()).decode()
        return jsonify({'ok': True, 'preview_b64': f'data:image/jpeg;base64,{b64}'})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})

@app.route('/api/regenerar_caption', methods=['POST'])
@requiere_auth
def api_regenerar_caption():
    data = request.get_json() or {}
    borrador_id = data.get('id')
    borrador = _buscar_borrador(borrador_id)
    if not borrador:
        return jsonify({'ok': False, 'msg': '⚠️ Borrador no encontrado'})
    with _bot_lock:
        if bot_activo:
            return jsonify({'ok': False, 'msg': '⚠️ Ya hay un proceso corriendo. Esperá que termine.'})

    def _regen():
        global bot_activo
        with _bot_lock:
            bot_activo = True
        socketio.emit('bot_status', {'activo': True})
        try:
            cliente_str = borrador.get('cliente', '')
            nombre_prod = cliente_str.split('—')[1].strip() if '—' in cliente_str else cliente_str
            prod_info = {
                'nombre': borrador.get('cliente_id', 'aurakey'),
                'titulo_producto': nombre_prod,
                'detalle_producto': nombre_prod,
                'keyword_busqueda': nombre_prod.split()[0] if nombre_prod else 'producto',
                'ficha': None,
            }
            log(f"🔄 Regenerando caption para '{nombre_prod}'...", "info")
            ficha = normalizar_producto_info(nombre_prod, None)
            prod_info['ficha'] = ficha
            tendencias = buscar_tendencias_reales_api(prod_info)
            precio = data.get('precio', 'Consultar por DM')
            nuevo_caption = generar_post_estricto(prod_info, tendencias, precio)
            borrador['caption'] = nuevo_caption
            _db_update_caption(borrador_id, {'caption': nuevo_caption})
            socketio.emit('caption_regenerado', {'id': borrador_id, 'caption': nuevo_caption})
            log('✅ Caption regenerado ✅', 'success')
        except Exception as e:
            log(f'❌ Error regenerando caption: {e}', 'error')
        finally:
            with _bot_lock:
                bot_activo = False
            socketio.emit('bot_status', {'activo': False})

    threading.Thread(target=_regen, daemon=True).start()
    return jsonify({'ok': True, 'msg': '🔄 Regenerando caption...'})


@app.route('/api/captions/export', methods=['GET'])
@requiere_auth
def api_captions_export():
    output = io_module.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Cliente', 'Fecha', 'Estado', 'Tipo', 'Caption', 'URL Imagen', 'Tendencia'])
    for c in captions_guardados:
        writer.writerow([
            c.get('id', ''),
            c.get('cliente', ''),
            c.get('fecha', ''),
            'Publicado' if c.get('publicado') else 'Borrador',
            c.get('tipo_publicacion', ''),
            c.get('caption', '').replace('\n', ' '),
            c.get('imagen_url', ''),
            c.get('tendencia', ''),
        ])
    output.seek(0)
    filename = f"captions_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


@app.route('/api/token_check', methods=['GET'])
@requiere_auth
def api_token_check():
    results = {}
    for cid, cliente in CLIENTES.items():
        token = cliente.get('meta_token')
        if not token:
            results[cid] = {'valido': False, 'msg': 'Sin token configurado'}
            continue
        try:
            res = http_session.get(
                "https://graph.facebook.com/debug_token",
                params={"input_token": token, "access_token": token},
                timeout=8
            )
            d = res.json().get('data', {})
            exp = d.get('expires_at', 0)
            if exp == 0:
                results[cid] = {'valido': True, 'tipo': 'never_expires', 'msg': 'Token sin expiración (largo plazo)'}
            else:
                exp_dt = datetime.fromtimestamp(exp)
                dias = (exp_dt - datetime.now()).days
                results[cid] = {
                    'valido': d.get('is_valid', False),
                    'expira': exp_dt.strftime('%d/%m/%Y'),
                    'dias_restantes': dias,
                    'msg': f'Expira en {dias} días' if dias > 0 else '⚠️ TOKEN EXPIRADO',
                }
        except Exception as e:
            results[cid] = {'valido': False, 'msg': str(e)}
    return jsonify(results)


@app.route('/api/insights/<cliente_id>', methods=['GET'])
@requiere_auth
def api_insights(cliente_id):
    cliente = CLIENTES.get(cliente_id)
    if not cliente:
        return jsonify({'error': 'Cliente no encontrado'}), 404
    meta_token = cliente.get('meta_token')
    ig_user_id = cliente.get('ig_user_id')
    if not meta_token or not ig_user_id:
        return jsonify({'error': 'Credenciales no configuradas'}), 400
    try:
        res_info = http_session.get(
            f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}",
            params={"fields": "followers_count,media_count,username", "access_token": meta_token},
            timeout=10
        )
        info = res_info.json()
        res_media = http_session.get(
            f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media",
            params={
                "fields": "id,timestamp,like_count,comments_count,media_type,thumbnail_url,media_url",
                "limit": 10,
                "access_token": meta_token
            },
            timeout=10
        )
        media = res_media.json()
        return jsonify({'ok': True, 'perfil': info, 'media_reciente': media.get('data', [])})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})



@app.route('/api/img_proxy')
def img_proxy():
    """Proxy de imágenes para evitar CORS con URLs de Instagram/CDN externo."""
    url = request.args.get('url', '')
    if not url or not url.startswith('http'):
        return '', 400
    try:
        resp = req.get(url, timeout=10, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; SocialBot/1.0)'
        })
        from flask import make_response
        r = make_response(resp.content)
        r.headers['Content-Type'] = resp.headers.get('Content-Type', 'image/jpeg')
        r.headers['Cache-Control'] = 'public, max-age=3600'
        r.headers['Access-Control-Allow-Origin'] = '*'
        return r
    except Exception as e:
        return str(e), 500


# ============================================================
# WEBHOOK UNIVERSAL — Acceso para IAs y automatizaciones
# ============================================================
WEBHOOK_TOKEN = os.environ.get("WEBHOOK_TOKEN", "bot_social_webhook_2026_ivan")

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    """
    Endpoint universal para control externo por IAs.
    
    GET  /webhook?token=XXX&action=info
    POST /webhook  { "token": "XXX", "action": "...", "params": {} }
    
    Acciones disponibles:
      info         — estado general del sistema
      get_stats    — estadísticas de posts/likes/comentarios
      get_logs     — últimos logs del bot
      get_captions — captions generados
      get_clientes — lista de clientes configurados
      bot_status   — si el bot está activo o no
      run_cycle    — ejecutar un ciclo del bot
      get_products — productos del catálogo
    """
    # Leer token y acción desde GET o POST
    if request.method == 'POST':
        body = request.get_json(silent=True) or {}
        token  = body.get('token', '')
        action = body.get('action', 'info')
        params = body.get('params', {})
    else:
        token  = request.args.get('token', '')
        action = request.args.get('action', 'info')
        # Para GET: todos los query params excepto token y action son params
        params = {k:v for k,v in request.args.items() if k not in ('token','action')}

    # Validar token
    if token != WEBHOOK_TOKEN:
        return jsonify({
            'ok': False,
            'error': 'Token inválido',
            'hint': 'Incluye el token correcto en el campo "token"'
        }), 401

    # ── Acciones disponibles ────────────────────────────────
    try:

        if action == 'info':
            return jsonify({
                'ok': True,
                'sistema': 'Social Bot Manager',
                'version': '2.0',
                'bot_activo': bot_activo,
                'clientes': list(CLIENTES.keys()),
                'total_captions': len(captions_guardados),
                'total_logs': len(logs_global),
                'acciones_disponibles': [
                    'info', 'get_stats', 'get_logs', 'get_captions',
                    'get_clientes', 'bot_status', 'run_cycle', 'get_products'
                ],
                'descripcion': 'Bot de gestión de Instagram con IA — genera captions, imágenes y publica automáticamente'
            })

        elif action == 'get_stats':
            return jsonify({
                'ok': True,
                'stats': stats_global
            })

        elif action == 'get_logs':
            limit = int(params.get('limit', 50))
            return jsonify({
                'ok': True,
                'logs': logs_global[-limit:],
                'total': len(logs_global)
            })

        elif action == 'get_captions':
            limit = int(params.get('limit', 20))
            estado = params.get('estado', None)
            caps = captions_guardados
            if estado:
                caps = [c for c in caps if c.get('estado') == estado]
            return jsonify({
                'ok': True,
                'captions': caps[-limit:],
                'total': len(caps)
            })

        elif action == 'get_clientes':
            clientes_info = {}
            for k, v in CLIENTES.items():
                clientes_info[k] = {
                    'nombre': v.get('nombre', k),
                    'tiene_token': bool(v.get('meta_token')),
                    'tiene_ig_id': bool(v.get('ig_user_id')),
                    'stats': stats_global.get(k, {})
                }
            return jsonify({'ok': True, 'clientes': clientes_info})

        elif action == 'bot_status':
            return jsonify({
                'ok': True,
                'activo': bot_activo,
                'logs_recientes': logs_global[-5:] if logs_global else []
            })

        elif action == 'get_products':
            prods = _db_load_profiles()
            return jsonify({
                'ok': True,
                'productos': prods,
                'total': len(prods)
            })

        elif action == 'add_product':
            nombre = params.get('nombre', '').strip()
            precio = params.get('precio', '').strip()
            keyword = params.get('keyword', nombre).strip()
            detalle = params.get('detalle', nombre).strip()
            if not nombre:
                return jsonify({'ok': False, 'error': 'Debes enviar "nombre" del producto'})
            import uuid
            from datetime import datetime
            profile = {
                'id': str(uuid.uuid4())[:8],
                'nombre': nombre,
                'precio': precio,
                'keyword_busqueda': keyword,
                'detalle_producto': detalle,
                'fecha': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            _db_save_profile(profile)
            log(f"✅ Producto agregado via webhook: {nombre}", "success")
            return jsonify({'ok': True, 'producto': profile, 'mensaje': f'Producto "{nombre}" agregado correctamente'})

        elif action == 'delete_product':
            pid = params.get('id', '')
            if not pid:
                return jsonify({'ok': False, 'error': 'Debes enviar "id" del producto'})
            ok = _db_delete_profile(pid)
            return jsonify({'ok': ok, 'mensaje': f'Producto {pid} eliminado' if ok else 'No encontrado'})

        elif action == 'run_cycle':
            if bot_activo:
                return jsonify({'ok': False, 'error': 'El bot ya está en ejecución'})
            cliente_id = params.get('cliente_id', list(CLIENTES.keys())[0])
            if cliente_id not in CLIENTES:
                return jsonify({'ok': False, 'error': f'Cliente "{cliente_id}" no existe'})
            producto = params.get('producto', '')
            precio   = params.get('precio', '')
            if not producto:
                return jsonify({'ok': False, 'error': 'Debes enviar "producto" en params'})
            # Lanzar ciclo en hilo separado
            def _run():
                from flask import current_app
                with app.app_context():
                    ciclo_completo(cliente_id, producto, precio)
            t = threading.Thread(target=_run, daemon=True)
            t.start()
            return jsonify({
                'ok': True,
                'mensaje': f'Ciclo iniciado para "{producto}" en cliente "{cliente_id}"',
                'tip': 'Consulta action=get_logs para ver el progreso'
            })

        else:
            return jsonify({
                'ok': False,
                'error': f'Acción "{action}" no reconocida',
                'acciones_validas': ['info','get_stats','get_logs','get_captions','get_clientes','bot_status','run_cycle','get_products']
            }), 400

    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

if __name__ == '__main__':
    print("🤖 Social Bot Manager - Activado")
    hilo_scheduler = threading.Thread(target=run_scheduler)
    hilo_scheduler.daemon = True
    hilo_scheduler.start()
    puerto = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=puerto, debug=False)
