from flask import Flask, render_template, jsonify, request, Response, redirect
from flask_socketio import SocketIO, emit
import json
import os
from datetime import datetime
import threading
import schedule
import time
import random
from groq import Groq
import requests as req
from functools import wraps
from urllib.parse import quote

# Auto-instalar Pillow si no está disponible (necesario para overlay de texto en imágenes)
try:
    from PIL import Image
except ImportError:
    import subprocess, sys
    print("📦 Instalando Pillow...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow", "--quiet"])
    print("✅ Pillow instalado correctamente.")

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY", os.urandom(24))
socketio = SocketIO(app, cors_allowed_origins="*")

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

sesiones = {}
stats_global = {}
logs_global = []
bot_activo = False
_bot_lock = threading.Lock()  # 🔒 Protege bot_activo contra race conditions

GRAPH_API_VERSION = "v21.0"  # Actualizar aquí en futuras migraciones de Meta

# ============================================
# BRAND KIT — AURAKEY
# ============================================
# Esta configuración convierte el bot en una máquina de contenido para Aurakey.
# Todo caption, CTA y estilo comercial puede tomar contexto desde aquí.
AURAKEY_BRAND = {
    "nombre": "Aurakey",
    "nicho": "productos digitales, licencias, software, gaming y suscripciones",
    "tono": "chileno, directo, vendedor, confiable, moderno",
    "whatsapp": "56946557876",
    "colores": ["negro", "cyan", "azul eléctrico"],
    "publico": "personas que buscan cuentas, licencias y productos digitales económicos en Chile",
    "cta": "Escríbenos por WhatsApp para activar tu producto"
}


def normalizar_whatsapp(numero):
    """Devuelve el número en formato +569... para mostrarlo en captions."""
    numero = str(numero or "").strip().replace(" ", "").replace("-", "")
    if not numero:
        return ""
    return numero if numero.startswith("+") else f"+{numero}"


def normalizar_whatsapp_wa(numero):
    """Devuelve solo dígitos para enlaces wa.me: 569..."""
    return "".join(ch for ch in str(numero or "") if ch.isdigit())


def obtener_mensaje_whatsapp_borrador(prod_info):
    """Crea un mensaje corto y universal para que el comprador lo envíe por WhatsApp."""
    ficha = (prod_info or {}).get("ficha") or {}
    brand = (prod_info or {}).get("brand") or obtener_brand_cliente((prod_info or {}).get("cliente_id", "aurakey"))
    nombre = ficha.get("nombre") or (prod_info or {}).get("titulo_producto") or (prod_info or {}).get("detalle_producto") or "un producto"
    mensaje = ficha.get("mensaje_whatsapp") or f"Hola, quiero consultar por {nombre}"
    mensaje = str(mensaje).strip().strip('"')
    if not mensaje.lower().startswith(("hola", "buenas")):
        mensaje = f"Hola, {mensaje[0].lower() + mensaje[1:] if mensaje else f'quiero consultar por {nombre}'}"
    if brand.get("nombre") and brand.get("nombre", "").lower() not in mensaje.lower():
        mensaje = f"{mensaje} en {brand.get('nombre')}"
    return mensaje[:180]


def crear_whatsapp_directo(cliente_id="aurakey", mensaje=""):
    """Construye un enlace directo a WhatsApp sin usar API oficial."""
    brand = obtener_brand_cliente(cliente_id)
    numero = normalizar_whatsapp_wa(brand.get("whatsapp", ""))
    if not numero:
        return ""
    texto = quote(mensaje or f"Hola, quiero consultar por un producto de {brand.get('nombre', 'Aurakey')}")
    return f"https://wa.me/{numero}?text={texto}"


def crear_whatsapp_link(cliente_id="aurakey", borrador_id=None, mensaje=""):
    """Usa tracking propio si PUBLIC_BASE_URL está configurado; si no, cae a wa.me directo."""
    directo = crear_whatsapp_directo(cliente_id, mensaje)
    base_url = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if base_url and borrador_id:
        tracking = f"{base_url}/w/{cliente_id}/{borrador_id}"
        return {"preferido": tracking, "tracking": tracking, "directo": directo}
    return {"preferido": directo, "tracking": "", "directo": directo}


def enriquecer_borrador_whatsapp(entrada, prod_info):
    """Agrega link, mensaje y contador WhatsApp a cada borrador/historial."""
    if not entrada:
        return entrada
    cliente_id = entrada.get("cliente_id", (prod_info or {}).get("cliente_id", "aurakey"))
    mensaje = obtener_mensaje_whatsapp_borrador(prod_info or {})
    links = crear_whatsapp_link(cliente_id, entrada.get("id"), mensaje)
    entrada["whatsapp_mensaje"] = mensaje
    entrada["whatsapp_link"] = links.get("preferido", "")
    entrada["whatsapp_link_directo"] = links.get("directo", "")
    entrada["whatsapp_link_tracking"] = links.get("tracking", "")
    entrada["clicks_whatsapp"] = entrada.get("clicks_whatsapp", 0)
    return entrada


def obtener_brand_cliente(cliente_id="aurakey"):
    """Retorna el brand kit del cliente. Por ahora Aurakey es la marca principal."""
    cliente = CLIENTES.get(cliente_id) if "CLIENTES" in globals() else None
    if cliente and cliente.get("brand"):
        return cliente["brand"]
    return AURAKEY_BRAND


CLIENTES = {
    "aurakey": {
        "nombre": AURAKEY_BRAND["nombre"],
        "meta_token": os.environ.get("META_ACCESS_TOKEN"),
        "ig_user_id": os.environ.get("IG_USER_ID"),
        "whatsapp": AURAKEY_BRAND["whatsapp"],
        "nicho": AURAKEY_BRAND["nicho"],
        "tono": AURAKEY_BRAND["tono"],
        "brand": AURAKEY_BRAND,
    },
}

for clave, cliente in CLIENTES.items():
    stats_global[clave] = {
        'nombre': cliente['nombre'],
        'posts': 0,
        'comentarios': 0,
        'likes': 0,
        'interacciones': 0,
        'clicks_whatsapp': 0,
        'ultimo_ciclo': 'Nunca'
    }

# ============================================
# LOGS
# ============================================

def log(msg, tipo='info'):
    entrada = {'msg': msg, 'tipo': tipo, 'hora': datetime.now().strftime('%H:%M:%S')}
    logs_global.append(entrada)
    if len(logs_global) > 100:
        logs_global.pop(0)
    socketio.emit('log', entrada)
    print(f"[{tipo.upper()}] {msg}")

# ============================================
# TENDENCIAS
# ============================================

def buscar_tendencias_reales_api(prod_info):
    keyword = prod_info["keyword_busqueda"]
    log(f"🌐 Escaneando tendencias globales para '{keyword}'...", "info")
    palabras_clave = []
    try:
        url = f"https://suggestqueries.google.com/complete/search?client=firefox&q={keyword}"
        res = req.get(url, timeout=5)
        if res.status_code == 200:
            datos = res.json()
            if len(datos) > 1 and isinstance(datos[1], list):
                palabras_clave = datos[1][:5]
                log(f"🔥 Datos frescos detectados en vivo: {', '.join(palabras_clave)}", "success")
    except Exception as e:
        log(f"⚠️ Error de conexión en vivo. Usando ganchos dinámicos.", "warning")
    if not palabras_clave:
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
        res = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
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
    Crea una ficha comercial UNIVERSAL a partir del título escrito por el usuario
    y/o la descripción generada por visión IA.

    Objetivo: que el bot pueda vender cualquier producto sin depender de catálogo:
    licencias, software, gaming, cursos, servicios, productos físicos, belleza,
    comida, hogar, moda, eventos, etc.
    """
    contexto = f"Título que escribió el usuario: {titulo_manual}\n" if titulo_manual else ""
    contexto += f"Descripción detectada por visión IA: {descripcion_vision}" if descripcion_vision else ""
    contexto = contexto.strip()

    def ficha_fallback():
        nombre_base = titulo_manual or (descripcion_vision.split(".")[0] if descripcion_vision else "producto")
        nombre_base = (nombre_base or "producto").strip()[:80]
        return {
            "nombre": nombre_base,
            "beneficio": "beneficio principal pendiente de confirmar",
            "audiencia": "personas interesadas en comprar este producto en Chile",
            "categoria": "otro",
            "problema": "quiere resolver una necesidad concreta con una compra simple",
            "objecion": "necesita confianza antes de comprar",
            "angulo_venta": "compra simple, rápida y confiable",
            "urgencia": "consultar disponibilidad hoy",
            "confianza": "atención directa por WhatsApp",
            "tono_producto": "claro, vendedor y confiable",
            "mensaje_whatsapp": f"Hola, quiero consultar por {nombre_base}",
            "hashtags_base": ["#Aurakey", "#Oferta", "#Chile"],
            "elementos_visuales": ["producto destacado", "fondo limpio", "estilo comercial"],
            "claridad": "baja"
        }

    if not contexto:
        return ficha_fallback()

    prompt = f"""A partir de esta información sobre un producto que se va a vender en Instagram Chile:

{contexto}

Crea una FICHA COMERCIAL UNIVERSAL en español. Debe funcionar para CUALQUIER título o imagen: productos digitales, licencias, software, gaming, suscripciones, cursos, servicios, comida, belleza, moda, hogar, electrónica, eventos o productos físicos.

Responde SOLO con JSON válido, sin explicaciones ni backticks, con exactamente estas claves:
{{
  "nombre": "nombre comercial más probable del producto",
  "beneficio": "qué gana el comprador en una línea concreta",
  "audiencia": "a quién va dirigido",
  "categoria": "software | licencia | cuenta_juego | suscripcion | producto_fisico | servicio_digital | curso | alimento | moda | belleza | hogar | evento | otro",
  "problema": "problema o deseo principal que mueve la compra",
  "objecion": "duda principal que podría frenar la compra",
  "angulo_venta": "ángulo de venta más fuerte para este producto",
  "urgencia": "motivo honesto para consultar hoy, sin inventar stock si no aparece",
  "confianza": "elemento de confianza que conviene comunicar",
  "tono_producto": "tono ideal para venderlo: gamer, premium, familiar, técnico, urgente, educativo, etc.",
  "mensaje_whatsapp": "mensaje corto que el comprador podría enviar para consultar",
  "hashtags_base": ["#hashtag1", "#hashtag2", "#hashtag3"],
  "elementos_visuales": ["elemento visual 1", "elemento visual 2", "elemento visual 3"],
  "claridad": "alta | media | baja"
}}

Reglas estrictas:
- Si el título del usuario es más específico que la visión, usa el título.
- Si la imagen es ambigua, NO inventes marca/modelo: usa el título o un nombre genérico honesto.
- No inventes precio, descuento, garantía, stock, duración, despacho ni características técnicas que no aparezcan.
- El nombre debe ser vendible y corto, no una descripción larga.
- El beneficio debe ser concreto, no genérico tipo "mejora tu vida".
- La urgencia debe ser honesta: "consulta disponibilidad", "aprovecha el precio publicado", "pide activación hoy", etc.
- Los hashtags deben ser cortos, sin espacios y útiles para Instagram Chile.
- Todo en español salvo marcas reales en inglés.
- Máximo 18 palabras por campo de texto.
- "claridad" debe ser baja si no se puede identificar bien el producto."""

    try:
        res = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=550,
            temperature=0.0,
        )
        raw = res.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()

        # Si el modelo llega a agregar texto extra, extraemos el primer bloque JSON.
        if not raw.startswith("{"):
            inicio = raw.find("{")
            fin = raw.rfind("}")
            if inicio != -1 and fin != -1 and fin > inicio:
                raw = raw[inicio:fin + 1]

        ficha = json.loads(raw)
        base = ficha_fallback()

        # Normalizar claves y evitar campos vacíos.
        for clave, valor_default in base.items():
            if clave not in ficha or ficha.get(clave) in (None, "", []):
                ficha[clave] = valor_default

        categorias_validas = {
            "software", "licencia", "cuenta_juego", "suscripcion", "producto_fisico",
            "servicio_digital", "curso", "alimento", "moda", "belleza", "hogar", "evento", "otro"
        }
        if ficha.get("categoria") not in categorias_validas:
            ficha["categoria"] = "otro"

        if ficha.get("claridad") not in {"alta", "media", "baja"}:
            ficha["claridad"] = "media"

        # Asegurar listas cortas y limpias.
        for clave_lista in ["hashtags_base", "elementos_visuales"]:
            valor = ficha.get(clave_lista)
            if isinstance(valor, str):
                valor = [v.strip() for v in valor.split(",") if v.strip()]
            elif not isinstance(valor, list):
                valor = base[clave_lista]
            ficha[clave_lista] = [str(v).strip()[:40] for v in valor if str(v).strip()][:5] or base[clave_lista]

        log(
            f"📋 Ficha comercial: {ficha.get('nombre')} | {ficha.get('categoria')} | claridad: {ficha.get('claridad')} | ángulo: {ficha.get('angulo_venta')}",
            "info"
        )
        return ficha
    except Exception as e:
        log(f"⚠️ Error normalizando producto ({e}). Usando ficha universal fallback.", "warning")
        return ficha_fallback()


def generar_post_estricto(prod_info, tendencias_reales, precio):
    tendencias_filtradas = filtrar_tendencias_con_llm(tendencias_reales, prod_info)
    ficha = prod_info.get("ficha") or {}
    brand = prod_info.get("brand") or obtener_brand_cliente(prod_info.get("cliente_id", "aurakey"))

    marca_nombre = brand.get("nombre", "Aurakey")
    marca_nicho = brand.get("nicho", "productos digitales")
    marca_tono = brand.get("tono", "chileno, directo, vendedor, confiable, moderno")
    marca_publico = brand.get("publico", "personas en Chile que buscan productos digitales")
    marca_cta = brand.get("cta", "Escríbenos por WhatsApp")
    marca_colores = ", ".join(brand.get("colores", [])) or "negro, cyan y azul eléctrico"
    marca_whatsapp = normalizar_whatsapp(brand.get("whatsapp", ""))

    nombre   = ficha.get("nombre")   or prod_info.get("titulo_producto") or prod_info.get("detalle_producto", "")
    beneficio = ficha.get("beneficio") or ""
    audiencia = ficha.get("audiencia") or marca_publico
    categoria = ficha.get("categoria") or "otro"
    problema = ficha.get("problema") or "necesita una solución simple y confiable"
    objecion = ficha.get("objecion") or "necesita confianza antes de comprar"
    angulo_venta = ficha.get("angulo_venta") or "compra simple, rápida y confiable"
    urgencia = ficha.get("urgencia") or "consulta disponibilidad hoy"
    confianza = ficha.get("confianza") or "atención directa por WhatsApp"
    tono_producto = ficha.get("tono_producto") or marca_tono
    mensaje_whatsapp = ficha.get("mensaje_whatsapp") or f"Hola, quiero consultar por {nombre}"
    claridad_producto = ficha.get("claridad", "media")
    hashtags_base = ficha.get("hashtags_base") or []

    # Estrategia automática según categoría detectada
    estrategias = {
        "software":        "Ahorro vs precio oficial + activación simple + utilidad inmediata.",
        "licencia":        "Confianza, activación clara y ahorro frente al precio tradicional.",
        "cuenta_juego":    "Emoción, acceso rápido, comunidad gamer y sensación de oportunidad.",
        "suscripcion":     "Valor por tiempo: mucho beneficio por un pago bajo o conveniente.",
        "producto_fisico": "Utilidad práctica, calidad percibida, disponibilidad y relación precio-valor.",
        "servicio_digital":"Resultado concreto, comodidad y ahorro de tiempo.",
        "curso":           "Transformación, habilidad adquirida y avance personal o laboral.",
        "alimento":        "Antojo, sabor, frescura, cercanía y compra impulsiva.",
        "moda":            "Estilo, identidad, ocasión de uso y sensación de verse mejor.",
        "belleza":         "Resultado visible, cuidado personal, confianza y autoestima.",
        "hogar":           "Orden, comodidad, solución práctica y mejora del espacio.",
        "evento":          "Experiencia, fecha cercana, cupos y miedo a quedarse fuera.",
        "otro":            "Beneficio concreto, confianza y acción simple por WhatsApp.",
    }
    estrategia_categoria = estrategias.get(categoria, estrategias["otro"])
    estrategia = f"{estrategia_categoria} Ángulo detectado por IA: {angulo_venta}. Objeción a resolver: {objecion}."

    prompt = f"""Eres el mejor copywriter de ventas digitales de Chile. Escribes para Instagram y cada caption tuyo genera ventas reales porque suena humano, específico y directo — nunca genérico.

MARCA:
- Nombre: {marca_nombre}
- Nicho: {marca_nicho}
- Tono obligatorio: {marca_tono}
- Público principal: {marca_publico}
- Colores de marca: {marca_colores}
- CTA principal: {marca_cta}
- WhatsApp oficial: {marca_whatsapp}

PRODUCTO:
- Nombre: {nombre}
- Qué hace: {beneficio or 'no especificado'}
- Para quién: {audiencia}
- Precio: {precio}
- Tipo: {categoria}
- Claridad de detección: {claridad_producto}
- Problema/deseo del comprador: {problema}
- Objeción principal: {objecion}
- Ángulo de venta: {angulo_venta}
- Urgencia honesta: {urgencia}
- Confianza a comunicar: {confianza}
- Tono ideal del producto: {tono_producto}
- Mensaje WhatsApp sugerido: {mensaje_whatsapp}
- Hashtags base sugeridos: {', '.join(hashtags_base) if hashtags_base else '—'}
- Tendencias útiles hoy: {', '.join(tendencias_filtradas) if tendencias_filtradas else '—'}

ESTRATEGIA: {estrategia}

CÓMO ESCRIBIR EL GANCHO (primera línea):
Escribe algo que SOLO tenga sentido para "{nombre}" — que si alguien lo lee sepa exactamente de qué se trata sin leer el resto. Prohibido frases que sirvan para cualquier producto como "esto cambia tu vida" o "no te lo pierdas". El gancho tiene que mencionar o insinuar el producto de forma concreta.
Ejemplos del estilo correcto:
- Para Duolingo Max: "¿Cuántos meses llevas diciendo que vas a aprender inglés? 🤔"
- Para Xbox Game Pass: "300 juegos. Un precio. Y la mayoría en Chile aún no sabe que existe."
- Para Kaspersky: "Tu PC lleva meses expuesta y probablemente no lo sabés."

REGLAS:
1. Gancho específico como los ejemplos de arriba — nada genérico
2. Beneficio central en 2-3 líneas. Concreto. Qué gana exactamente el que compra.
3. Precio como revelación: "y lo mejor: te sale en {precio} — sí, en serio."
4. CTA directa y urgente, específica al producto. Sin "¡no lo pierdas!"
5. Tono chileno natural, con personalidad, alineado a la marca {marca_nombre}. Puede tener humor si el producto lo permite.
6. Emojis con intención, máximo 6-8 en todo el texto.
7. Largo: 80-120 palabras exactos.
8. La marca debe sentirse confiable: activación clara, compra simple y atención por WhatsApp.
9. Si la claridad de detección es baja, evita afirmar características específicas no confirmadas; vende desde consulta, beneficio general y confianza.
10. No inventes garantía, stock, despacho, duración, descuentos ni características técnicas si no aparecen en la ficha.
11. Resuelve la objeción principal de forma natural dentro del caption.

Justo antes de los hashtags incluye exactamente estas tres líneas:
{marca_cta}
📲 WhatsApp: {marca_whatsapp}
Mensaje sugerido: "{mensaje_whatsapp}"

HASHTAGS (exactamente 5):
- 2 del producto o marca
- 2 de tendencia o audiencia  
- 1 de acción comercial (#Oferta #Deal #Descuento)
- Prohibido hashtags de más de 2 palabras

RESPONDE SOLO CON EL CAPTION, sin explicaciones:"""

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=400,
        temperature=0.9,
    )
    return response.choices[0].message.content


def analizar_imagen_referencia(imagen_referencia_url):
    """Usa Groq Vision para describir el estilo visual de la imagen de referencia."""
    # Validar que la URL sea válida antes de intentar cualquier cosa
    if not imagen_referencia_url or not isinstance(imagen_referencia_url, str) or not imagen_referencia_url.startswith("http"):
        log("⚠️ URL de referencia inválida o vacía. Saltando análisis de visión.", "warning")
        return None
    try:
        import base64
        log("🔍 Groq analizando imagen de referencia...", "info")
        img_response = req.get(imagen_referencia_url, timeout=15)
        if img_response.status_code != 200:
            log(f"⚠️ No se pudo descargar imagen para análisis (HTTP {img_response.status_code}).", "warning")
            return None
        img_b64 = base64.b64encode(img_response.content).decode("utf-8")
        # Detectar tipo de imagen
        content_type = img_response.headers.get("Content-Type", "image/jpeg")
        if "png" in content_type:
            media_type = "image/png"
        elif "webp" in content_type:
            media_type = "image/webp"
        else:
            media_type = "image/jpeg"
        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{img_b64}"}
                    },
                    {
                        "type": "text",
                        "text": (
                            "You are an expert art director analyzing a commercial advertisement image. "
                            "Provide a HIGHLY DETAILED technical analysis covering: "
                            "1) COLORS: exact color palette with hex-like descriptions (deep navy blue, electric cyan, etc.), gradients, and dominant tones. "
                            "2) STYLE: design style (3D render, photorealistic, flat design, neon, luxury minimalist, tech futuristic, etc.). "
                            "3) COMPOSITION: layout structure, element placement, focal point, use of space. "
                            "4) TYPOGRAPHY: font style, weight, size hierarchy, color, placement. "
                            "5) LIGHTING & MOOD: light sources, shadows, atmosphere, emotional tone. "
                            "6) KEY GRAPHIC ELEMENTS: specific shapes, textures, effects, icons, or visual motifs. "
                            "Be extremely specific and technical so a designer can perfectly replicate this style. "
                            "Max 120 words. English only. No generic descriptions."
                        )
                    }
                ]
            }],
            max_tokens=200,
            temperature=0.2,
        )
        descripcion = response.choices[0].message.content.strip()
        log(f"✅ Imagen analizada por Groq Vision: {descripcion[:80]}...", "success")
        return descripcion
    except Exception as e:
        log(f"⚠️ Error en Groq Vision: {e}. Continuando con estilo genérico.", "warning")
        return None


def generar_prompt_imagen(prod_info, caption, con_referencia=False, descripcion_referencia=None):
    # Usar la ficha comercial universal si existe, sino el detalle original
    ficha = prod_info.get("ficha") or {}
    nombre = ficha.get("nombre") or prod_info.get("titulo_producto") or prod_info.get("detalle_producto", "producto")
    categoria = ficha.get("categoria", "otro")
    angulo_venta = ficha.get("angulo_venta", "premium commercial offer")
    elementos_visuales = ficha.get("elementos_visuales") or []
    elementos_visuales_txt = ", ".join(elementos_visuales[:5]) if elementos_visuales else "clean premium commercial elements"

    if con_referencia and descripcion_referencia:
        # Groq vio la imagen real — usamos su análisis para guiar a Ideogram
        contexto_estilo = (
            f"Replicate EXACTLY this visual style from the reference image: {descripcion_referencia}. "
            f"Apply this style to create a premium commercial advertisement for '{nombre}'."
        )
    elif con_referencia:
        # Fallback si la visión falló
        contexto_estilo = f"Create an intense, premium commercial advertisement banner with high-end fluid dynamics, detailed 3D liquid splashes, glowing neon accents, and a vibrant explosive color palette matching '{nombre}'. Highly detailed, cinematic layout."
    else:
        contexto_estilo = f"Create a visual style that perfectly matches the official brand identity of '{nombre}'. If it is corporate software or productivity tools (like Adobe, Microsoft, etc.), use ultra-clean, premium, modern minimalist aesthetics with sleek gradients and 3D icons. If it is gaming or anime, use epic, high-tech, or cinematic styles."

    prompt = f"""
    You are a world-class prompt engineer specialized in Ideogram v3 Balanced — the most advanced AI image generation model for commercial advertising.
    Product to advertise: "{nombre}"
    Product category: "{categoria}"
    Sales angle: "{angulo_venta}"
    Suggested visual elements: "{elementos_visuales_txt}"
    
    Your goal: write a MASTERCLASS-level prompt that pushes Ideogram v3 Balanced to its full potential.
    
    CRITICAL PRODUCT ACCURACY RULES:
    1. {contexto_estilo}
    2. The product "{nombre}" must be the HERO of the image — visually dominant, accurate, and recognizable. Do NOT invent generic product visuals. Base the design on the REAL product identity.
    3. Include REAL product-specific visual elements based on the product category and suggested visual elements. If uncertain, use honest generic commercial visuals instead of inventing logos or technical claims.
    4. Typography: include ONLY the product name "{nombre}" as a single bold headline. NO other text, NO taglines, NO subtext, NO descriptions, NO numbers except the product name.
    5. ABSOLUTE TEXT RULE — THIS IS THE MOST IMPORTANT RULE:
       - ZERO background text patterns, ZERO decorative letters, ZERO texture made of characters
       - ZERO small text blocks anywhere — not at bottom, not at sides, not in background
       - ZERO paragraph text, body copy, or simulated print text
       - ZERO text that looks like a magazine bottom bar or disclaimer
       - The background must be CLEAN graphic elements only: gradients, light effects, geometric shapes, bokeh, particles — NEVER letters or words used as texture
       - If in doubt, use NO text at all — better a clean image than one with fake text
    6. Vertical 9:16 format, premium commercial quality, photorealistic or high-end 3D render style.
    7. LANGUAGE RULE: The ONLY allowed text is the product name in English or Spanish. NOTHING ELSE.
    
    OUTPUT RULES:
    - Write ONLY the Ideogram prompt in English, max 100 words
    - Start directly with the visual description — no preamble
    - END the prompt with this exact phrase: "Absolutely no background text, no decorative text patterns, no small print, no fake paragraph text anywhere in the image. Clean design only."
    - Make it cinematic, detailed, and specific to the real product
    """
    
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=250,
        temperature=0.3,
    )
    return response.choices[0].message.content

captions_guardados = []

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
# FFMPEG — COMBINAR IMAGEN + AUDIO → VIDEO
# ============================================

def generar_video_reel(imagen_path, audio_path, duracion=15):
    import subprocess
    try:
        os.makedirs("static", exist_ok=True)
        video_path = f"static/reel_{int(time.time())}.mp4"
        log(f"🎬 Generando video Reel con ffmpeg ({duracion}s)...", "info")
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", imagen_path,
            "-i", audio_path,
            "-t", str(duracion),
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black",
            "-c:v", "libx264",
            "-tune", "stillimage",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "44100",
            "-ac", "2",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-shortest",
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            log(f"❌ ffmpeg error: {result.stderr[-300:]}", "error")
            return None
        log(f"✅ Video Reel generado → {video_path}", "success")
        return video_path
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
    replicate_token = os.environ.get("REPLICATE_API_TOKEN")
    if not replicate_token:
        log("⚠️ REPLICATE_API_TOKEN no configurada. Saltando generación de imagen.", "warning")
        return None
    try:
        import replicate
        import io
        client = replicate.Client(api_token=replicate_token)

        # Negative prompt agresivo — elimina texto basura, caracteres raros y baja calidad
        negative_prompt = (
            # Texto ilegible y falso
            "illegible text, blurry text, garbled text, scrambled letters, random letters, "
            "fake text, lorem ipsum, gibberish words, nonsense text, decorative fake words, "
            "misspelled words, corrupted text, distorted letters, abstract letterforms, "
            "typographic noise, pseudo-text, simulated text, placeholder text, "
            # Texto pequeño y de fondo
            "small print, fine print, body copy text, paragraph text, running text, "
            "background text, texture text, pattern made of letters, text wallpaper, "
            "footer text, header text, disclaimer text, terms and conditions, legal text, "
            "caption text, subtitle text, watermark text, stamp text, "
            # Caracteres no latinos
            "chinese characters, japanese characters, arabic script, cyrillic text, "
            "korean characters, hindi characters, thai script, hebrew letters, "
            "greek letters used decoratively, runes, symbols as text, "
            # Problemas de diseño
            "cluttered layout, busy background, overlapping elements, messy composition, "
            "stock photo watermark, draft quality, low resolution, pixelated, noisy image, "
            "amateur design, ugly fonts, deformed letters, broken typography"
        )

        parametros = {
            "prompt": prompt_imagen + " NO background text patterns. NO decorative letters as texture. NO small print. NO paragraph text blocks. NO fake words anywhere. Clean graphic design only.",
            "negative_prompt": negative_prompt,
            "resolution": "768x1344",
            "style_type": "Design",
            "magic_prompt_option": "Off",
        }

        # ✅ FIX: Inyectar imagen de referencia real en Ideogram
        if imagen_referencia_url:
            log(f"🖼️ Descargando imagen de referencia de estilo...", "info")
            try:
                img_response = req.get(imagen_referencia_url, timeout=15)
                if img_response.status_code == 200:
                    imagen_ref_bytes = io.BytesIO(img_response.content)
                    parametros["style_reference_images"] = [imagen_ref_bytes]
                    parametros["style_type"] = "Auto"
                    parametros["style_weight"] = style_weight
                    log(f"✅ Referencia de estilo inyectada en Ideogram (style_weight={style_weight}).", "success")
                else:
                    log(f"⚠️ No se pudo descargar la imagen de referencia (HTTP {img_response.status_code}). Generando sin referencia.", "warning")
            except Exception as ref_err:
                log(f"⚠️ Error al cargar referencia: {ref_err}. Continuando sin referencia.", "warning")

        log(f"🖼️ Generando con Ideogram v3 Balanced{'  + referencia de estilo' if imagen_referencia_url else ' — máxima calidad'}...", "info")
        output = client.run(
            "ideogram-ai/ideogram-v3-balanced",
            input=parametros
        )

        image_url = str(output)
        img_bytes = req.get(image_url, timeout=30).content
        os.makedirs("static", exist_ok=True)
        filepath = f"static/img_{int(time.time())}.png"
        with open(filepath, "wb") as f:
            f.write(img_bytes)
        log(f"🖼️ Imagen generada con Ideogram ✅", "success")
        return filepath

    except Exception as e:
        error_str = str(e)
        if "402" in error_str or "Insufficient credit" in error_str:
            log(f"💳 Sin créditos en Replicate. Recarga en: https://replicate.com/account/billing", "warning")
        else:
            log(f"❌ Error generando imagen: {e}", "error")
        return None

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
                log(f"☁️ Video subido a Cloudinary ✅", "success")
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
        res = req.post(
            f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media",
            data={"media_type": "REELS", "video_url": video_url, "caption": caption, "access_token": meta_token}
        )
        container_id = res.json().get("id")
        if not container_id:
            log(f"❌ Error creando contenedor Reel: {res.json()}", "error")
            return False
        log(f"⏳ Esperando que Meta procese el video...", "info")
        listo = False
        for intento in range(15):
            time.sleep(6)
            check = req.get(
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
        res2 = req.post(
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

def aplicar_overlay_texto(imagen_path, texto, posicion='center', glow_color='#00e5ff'):
    """Dibuja texto con banda oscura + glow neón. Visible sobre cualquier fondo."""
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
        import textwrap

        img = Image.open(imagen_path).convert("RGBA")
        w, h = img.size

        # Fuente más grande para que sea bien visible
        font_size = max(52, int(w * 0.10))
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

        # Wrap de texto — líneas más cortas para que quepan mejor
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
        band_pad_v = int(font_size * 0.52)
        band_h = block_h + band_pad_v * 2

        # Banda horizontal de extremo a extremo, más limpia y más transparente
        side_margin = 0
        band_x = side_margin
        band_w = max(0, w - (side_margin * 2))

        # Calcular Y según posición
        padding = int(h * 0.07)
        if posicion == 'top':
            band_y = padding
        elif posicion == 'bottom':
            band_y = h - band_h - padding
        else:  # center
            band_y = (h - band_h) // 2

        block_y = band_y + band_pad_v

        # ── Capa 1: banda oscura semitransparente detrás del texto ──
        band_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        band_draw  = ImageDraw.Draw(band_layer)
        # Banda negra más transparente y sin bordes de color
        band_draw.rectangle(
            [band_x, band_y, band_x + band_w, band_y + band_h],
            fill=(0, 0, 0, 118)
        )
        # Suavizar bordes de la banda ligeramente
        band_layer = band_layer.filter(ImageFilter.GaussianBlur(radius=1))

        # ── Capa 2: glow difuso del texto en color neón ──
        glow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        glow_draw  = ImageDraw.Draw(glow_layer)
        cur_y = block_y
        for i, line in enumerate(lines):
            x = (w - line_widths[i]) // 2
            # Múltiples pasadas de glow con opacidad decreciente
            for radius_extra, alpha in [(0, 180), (0, 140), (0, 100)]:
                glow_draw.text((x, cur_y), line, font=font, fill=(*glow_rgb, alpha))
            cur_y += line_heights[i] + line_spacing
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=font_size // 5))

        # Segunda pasada de glow más amplia
        glow_layer2 = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        glow_draw2  = ImageDraw.Draw(glow_layer2)
        cur_y = block_y
        for i, line in enumerate(lines):
            x = (w - line_widths[i]) // 2
            glow_draw2.text((x, cur_y), line, font=font, fill=(*glow_rgb, 80))
            cur_y += line_heights[i] + line_spacing
        glow_layer2 = glow_layer2.filter(ImageFilter.GaussianBlur(radius=font_size // 2))

        # ── Capa 3: texto blanco nítido con sombra oscura ──
        text_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        text_draw  = ImageDraw.Draw(text_layer)
        cur_y = block_y
        for i, line in enumerate(lines):
            x = (w - line_widths[i]) // 2
            # Sombra desplazada para profundidad
            for dx, dy in [(3, 3), (2, 2), (-1, -1)]:
                text_draw.text((x + dx, cur_y + dy), line, font=font, fill=(0, 0, 0, 180))
            # Texto blanco brillante
            text_draw.text((x, cur_y), line, font=font, fill=(255, 255, 255, 255))
            cur_y += line_heights[i] + line_spacing

        # Combinar: imagen → banda → glow difuso → glow → texto
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
        import base64
        with open(filepath, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        res = req.post("https://api.imgbb.com/1/upload", data={"key": imgbb_key, "image": img_b64})
        url = res.json().get("data", {}).get("url")
        if url:
            log(f"☁️ Imagen subida a ImgBB ✅", "success")
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
        res = req.post(
            f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_user_id}/media",
            data={"image_url": imagen_url, "caption": caption, "access_token": meta_token}
        )
        container_id = res.json().get("id")
        if not container_id:
            log(f"❌ Error creando contenedor: {res.json()}", "error")
            return False
        log(f"⏳ Esperando que Meta procese la imagen...", "info")
        listo = False
        for intento in range(10):
            time.sleep(4)
            check = req.get(
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
            log(f"❌ Timeout: Meta no procesó la imagen.", "error")
            return False
        log(f"🚀 Publicando en Instagram de {cliente['nombre']}...", "info")
        res2 = req.post(
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

def ciclo_libre(busqueda, precio_manual="No especificado", cliente_id="aurakey", mood="energico", hacer_reel=True, imagen_referencia_url=None, style_weight=0.5, titulo_producto=None):
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
    brand = cliente.get("brand", AURAKEY_BRAND)
    prod_info = {
        "nombre": nombre_cliente,
        "cliente_id": cliente_id,
        "brand": brand,
        "titulo_producto": titulo_producto or busqueda,
        "detalle_producto": detalle,
        "keyword_busqueda": (titulo_producto or busqueda).split()[0],
        "nicho": brand.get("nicho", titulo_producto or busqueda),
        "tono": brand.get("tono", "profesional, vendedor, directo y confiable"),
        "whatsapp": brand.get("whatsapp", "")
    }
    try:
        tendencias_reales = buscar_tendencias_reales_api(prod_info)
        gancho_usado = f"Tendencias en vivo: {', '.join(tendencias_reales[:2])}"
        log(f'📋 Normalizando ficha de producto para "{detalle}"...', 'info')
        ficha = normalizar_producto_info(titulo_producto or busqueda, None)
        prod_info['ficha'] = ficha
        prod_info['titulo_producto'] = ficha.get("nombre") or prod_info['titulo_producto']
        log(f'✍️ Redactando post para "{prod_info["titulo_producto"]}"...', 'info')
        caption_completo = generar_post_estricto(prod_info, tendencias_reales, precio_manual)
        log(f'🎨 Generando prompt visual para "{busqueda}"...', 'info')

        # Si hay imagen de referencia válida, Groq la analiza con visión primero
        descripcion_referencia = None
        if imagen_referencia_url and isinstance(imagen_referencia_url, str) and imagen_referencia_url.startswith("http"):
            try:
                descripcion_referencia = analizar_imagen_referencia(imagen_referencia_url)
            except Exception as e_vision:
                log(f"⚠️ Groq Vision falló ({e_vision}). Continuando sin referencia.", "warning")
                descripcion_referencia = None
                imagen_referencia_url = None  # Evitar que se intente usar más adelante

        prompt_imagen = generar_prompt_imagen(
            prod_info,
            caption_completo,
            con_referencia=bool(imagen_referencia_url),
            descripcion_referencia=descripcion_referencia
        )
        
        imagen_filepath = None
        imagen_url_publica = None
        publicado_post = False
        publicado_reel = False
        reel_generado = False
        
        # Generación directa con Ideogram Turbo
        imagen_filepath = generar_imagen_dalle(prompt_imagen, imagen_referencia_url, style_weight=style_weight)

        if imagen_filepath:
            imagen_url_publica = subir_imgbb(imagen_filepath)

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
            'reel_generado': False,
            'con_referencia': bool(imagen_referencia_url),
            'fecha': datetime.now().strftime('%d/%m %H:%M')
        }
        enriquecer_borrador_whatsapp(entrada, prod_info)
        captions_guardados.insert(0, entrada)
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
        log(f"🖼️ Imagen de referencia subida ✅", "success")
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


@app.route('/w/<cliente_id>/<borrador_id>')
def redirigir_whatsapp(cliente_id, borrador_id):
    """Tracking básico: cuenta clics y redirige a WhatsApp sin usar WhatsApp API."""
    cliente = CLIENTES.get(cliente_id)
    if not cliente:
        return Response("Cliente no encontrado.", 404)

    borrador = _buscar_borrador(borrador_id)
    mensaje = (borrador or {}).get('whatsapp_mensaje') or f"Hola, quiero consultar por un producto de {cliente.get('nombre', 'Aurakey')}"
    link_directo = crear_whatsapp_directo(cliente_id, mensaje)
    if not link_directo:
        return Response("WhatsApp no configurado para este cliente.", 400)

    if cliente_id not in stats_global:
        stats_global[cliente_id] = {'nombre': cliente.get('nombre', cliente_id), 'posts': 0, 'comentarios': 0, 'likes': 0, 'interacciones': 0, 'clicks_whatsapp': 0, 'ultimo_ciclo': 'Nunca'}
    stats_global[cliente_id]['clicks_whatsapp'] = stats_global[cliente_id].get('clicks_whatsapp', 0) + 1

    if borrador is not None:
        borrador['clicks_whatsapp'] = borrador.get('clicks_whatsapp', 0) + 1

    socketio.emit('stats', stats_global)
    log(f"📲 Click WhatsApp registrado para {cliente.get('nombre')} — borrador {borrador_id}", "success")
    return redirect(link_directo, code=302)


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
        res = req.post(
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
            check = req.get(
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
        res2 = req.post(
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


def generar_borrador_imagen_propia_task(imagen_url, cliente_id, precio, modo, mood, overlay, titulo_producto=None):
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

        img_bytes = req.get(imagen_url, timeout=30).content
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
                glow_color=overlay.get('glow_color', '#00e5ff')
            )

        imagen_url_final = subir_imgbb(img_path)
        if not imagen_url_final:
            log("⚠️ No se pudo subir imagen procesada. Usando original.", "warning")
            imagen_url_final = imagen_url

        log("🔍 Detectando producto en la imagen...", "info")
        import base64
        img_response = req.get(imagen_url_final, timeout=15)
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
        brand = cliente.get("brand", AURAKEY_BRAND)
        prod_info = {
            'nombre': cliente['nombre'],
            'cliente_id': cliente_id,
            'brand': brand,
            'titulo_producto': ficha.get("nombre") or nombre_final,
            'detalle_producto': detalle_final,
            'keyword_busqueda': (ficha.get("nombre") or nombre_final).split()[0],
            'ficha': ficha,
            'nicho': brand.get("nicho", "productos digitales"),
            'tono': brand.get("tono", "chileno, directo y vendedor"),
            'whatsapp': brand.get("whatsapp", ""),
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
            'reel_generado': False,
            'con_referencia': False,
            'fecha': datetime.now().strftime('%d/%m %H:%M')
        }
        enriquecer_borrador_whatsapp(entrada, prod_info)
        captions_guardados.insert(0, entrada)
        socketio.emit('caption', entrada)
        log("📝 Borrador listo. Revísalo y apruébalo desde el panel.", "success")
        socketio.emit('stats', stats_global)

    except Exception as e:
        log(f"❌ Error generando borrador de imagen propia: {e}", "error")
    finally:
        with _bot_lock:
            bot_activo = False
        socketio.emit('bot_status', {'activo': False})


def publicar_imagen_propia_task(imagen_url, cliente_id, precio, modo, mood, overlay, titulo_producto=None):
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
        img_bytes = req.get(imagen_url, timeout=30).content
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
                glow_color=overlay.get('glow_color', '#00e5ff')
            )

        # 3. Subir imagen procesada (con o sin overlay) a ImgBB
        imagen_url_final = subir_imgbb(img_path)
        if not imagen_url_final:
            log("⚠️ No se pudo subir imagen procesada. Usando original.", "warning")
            imagen_url_final = imagen_url

        # 4. Analizar imagen con Groq Vision para detectar el producto
        log("🔍 Detectando producto en la imagen...", "info")
        import base64
        img_response = req.get(imagen_url_final, timeout=15)
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

        brand = cliente.get("brand", AURAKEY_BRAND)
        prod_info = {
            'nombre': cliente['nombre'],
            'cliente_id': cliente_id,
            'brand': brand,
            'titulo_producto': ficha.get("nombre") or nombre_final,
            'detalle_producto': detalle_final,
            'keyword_busqueda': (ficha.get("nombre") or nombre_final).split()[0],
            'ficha': ficha,
            'nicho': brand.get("nicho", "productos digitales"),
            'tono': brand.get("tono", "chileno, directo y vendedor"),
            'whatsapp': brand.get("whatsapp", ""),
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
                video_path = generar_video_reel(img_path, audio_path)
                reel_generado = bool(video_path)
            if video_path and meta_token and ig_user_id:
                publicado = publicar_reel_instagram(video_path, caption, cliente_id)
        else:
            if meta_token and ig_user_id:
                publicado = publicar_en_instagram(imagen_url_final, caption, cliente_id)

        if publicado:
            log(f"✅ {'Reel' if modo == 'reel' else 'Post'} publicado en Instagram ✅", "success")
        else:
            log(f"⚠️ Generado pero no publicado en Instagram.", "warning")

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
            'estado': 'publicado' if publicado else 'generado',
            'reel_generado': reel_generado,
            'con_referencia': False,
            'fecha': datetime.now().strftime('%d/%m %H:%M')
        }
        enriquecer_borrador_whatsapp(entrada, prod_info)
        captions_guardados.insert(0, entrada)
        socketio.emit('caption', entrada)
        stats_global[cliente_id]['posts'] += 1
        stats_global[cliente_id]['ultimo_ciclo'] = datetime.now().strftime('%d/%m %H:%M')
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
    imagen_url = data.get('imagen_url', '').strip()
    cliente_id = data.get('cliente_id', 'aurakey')
    precio = data.get('precio', 'Consultar por DM')
    modo = data.get('modo', 'post')
    mood = data.get('mood', 'energico')
    overlay = data.get('overlay', None)
    titulo_producto = data.get('titulo_producto', '').strip() or None

    if not imagen_url:
        return jsonify({'ok': False, 'msg': '⚠️ No se recibió URL de imagen.'})
    with _bot_lock:
        if bot_activo:
            return jsonify({'ok': False, 'msg': '⚠️ Ya hay un proceso corriendo. Esperá que termine.'})

    hilo = threading.Thread(
        target=generar_borrador_imagen_propia_task,
        args=(imagen_url, cliente_id, precio, modo, mood, overlay, titulo_producto),
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
    caption_editado = data.get('caption', '').strip()
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
                img_bytes = req.get(borrador.get('imagen_url'), timeout=30).content
                os.makedirs('static', exist_ok=True)
                img_path = f"static/reel_aprobado_{int(time.time())}.jpg"
                with open(img_path, 'wb') as f:
                    f.write(img_bytes)
            audio_path = buscar_musica_pixabay(borrador.get('mood') or 'energico')
            video_path = generar_video_reel(img_path, audio_path) if audio_path else None
            reel_generado = bool(video_path)
            publicado = publicar_reel_instagram(video_path, caption_final, cliente_id) if video_path else False
        else:
            publicado = publicar_post_instagram_url(borrador.get('imagen_url'), caption_final, cliente_id)

        borrador['caption'] = caption_final
        borrador['publicado'] = publicado
        borrador['estado'] = 'publicado' if publicado else 'error_publicacion'
        borrador['reel_generado'] = reel_generado
        borrador['fecha_publicacion'] = datetime.now().strftime('%d/%m %H:%M')

        if publicado:
            stats_global[cliente_id]['posts'] += 1
            stats_global[cliente_id]['ultimo_ciclo'] = datetime.now().strftime('%d/%m %H:%M')
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
    imagen_url = data.get('imagen_url', '').strip()
    cliente_id = data.get('cliente_id', 'aurakey')
    precio = data.get('precio', 'Consultar por DM')
    modo = data.get('modo', 'post')
    mood = data.get('mood', 'energico')
    overlay = data.get('overlay', None)
    titulo_producto = data.get('titulo_producto', '').strip() or None

    if not imagen_url:
        return jsonify({'ok': False, 'msg': '⚠️ No se recibió URL de imagen.'})
    with _bot_lock:
        if bot_activo:
            return jsonify({'ok': False, 'msg': '⚠️ Ya hay un ciclo corriendo. Esperá que termine.'})

    hilo = threading.Thread(
        target=publicar_imagen_propia_task,
        args=(imagen_url, cliente_id, precio, modo, mood, overlay, titulo_producto),
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
    busqueda_libre = data.get('busqueda_libre', '').strip()
    titulo_producto = data.get('titulo_producto', '').strip() or None
    cliente_id = data.get('cliente_id', 'aurakey')
    mood = data.get('mood', 'energico')
    hacer_reel = data.get('hacer_reel', True)
    imagen_referencia_url = data.get('imagen_referencia_url', None)
    style_weight = float(data.get('style_weight', 0.5) or 0.5)
    if not busqueda_libre:
        return jsonify({'msg': '⚠️ Se requiere búsqueda libre para iniciar un ciclo.'})
    with _bot_lock:
        if bot_activo:
            return jsonify({'msg': '⚠️ Ya hay un ciclo corriendo. Esperá que termine antes de iniciar otro.'})
    modo_img = "con referencia" if imagen_referencia_url else "solo texto"
    hilo = threading.Thread(target=ciclo_libre, args=(busqueda_libre, precio, cliente_id, mood, hacer_reel, imagen_referencia_url, style_weight, titulo_producto))
    hilo.daemon = True
    hilo.start()
    detalle_log = titulo_producto or busqueda_libre
    return jsonify({'msg': f'Ciclo iniciado para: {detalle_log} (mood: {mood}, reel: {hacer_reel}, imagen: {modo_img})'})

# ============================================
# SCHEDULER CONFIGURABLE
# ============================================

scheduler_config = {
    "activo": False,
    "intervalo_horas": 2,
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
    # 🔒 Verificar que no haya un ciclo ya corriendo antes de disparar
    with _bot_lock:
        if bot_activo:
            log("⏰ Scheduler: ciclo anterior aún activo, se omite esta ejecución.", "warning")
            return
    log("⏰ Scheduler: disparando ciclo automático...", "info")
    scheduler_config["ciclos_ejecutados"] += 1
    # Calcular próximo ciclo
    from datetime import timedelta
    proximo = datetime.now() + timedelta(hours=scheduler_config["intervalo_horas"])
    scheduler_config["proximo_ciclo"] = proximo.strftime("%d/%m %H:%M")
    socketio.emit("scheduler_status", scheduler_config)
    # 🔧 Lanzar en thread separado — no bloquear el loop del scheduler
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
        horas = scheduler_config["intervalo_horas"]
        schedule.every(horas).hours.do(_ejecutar_ciclo_scheduler).tag("auto")
        from datetime import timedelta
        proximo = datetime.now() + timedelta(hours=horas)
        scheduler_config["proximo_ciclo"] = proximo.strftime("%d/%m %H:%M")
        log(f"⏰ Scheduler activado — cada {horas}h | próximo: {scheduler_config['proximo_ciclo']}", "success")
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
    scheduler_config["intervalo_horas"] = int(data.get("intervalo_horas", 2))
    scheduler_config["busqueda"] = data.get("busqueda", "").strip()
    scheduler_config["titulo_producto"] = data.get("titulo_producto", "").strip() or None
    scheduler_config["precio"] = data.get("precio", "Consultar por DM")
    scheduler_config["cliente_id"] = data.get("cliente_id", "aurakey")
    scheduler_config["mood"] = data.get("mood", "energico")
    scheduler_config["hacer_reel"] = bool(data.get("hacer_reel", True))
    scheduler_config["imagen_referencia_url"] = data.get("imagen_referencia_url", None)
    scheduler_config["style_weight"] = float(data.get("style_weight", 0.5) or 0.5)
    _aplicar_schedule()
    return jsonify({"ok": True, "config": scheduler_config})

if __name__ == '__main__':
    print("🤖 Social Bot Manager - Activado")
    hilo_scheduler = threading.Thread(target=run_scheduler)
    hilo_scheduler.daemon = True
    hilo_scheduler.start()
    puerto = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=puerto, debug=False)
