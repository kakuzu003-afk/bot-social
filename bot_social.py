from flask import Flask, render_template, jsonify, request, Response
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

def generar_post_estricto(prod_info, tendencias_reales, precio):
    prompt = f"""
Eres un copywriter especialista en ventas digitales para el mercado chileno. Llevas 10 años vendiendo software, licencias, cuentas de juegos y servicios digitales en redes sociales. Conoces exactamente qué palabras hacen que la gente chilena detenga el scroll y compre.

CONTEXTO DEL PRODUCTO:
- Producto: {prod_info['detalle_producto']}
- Precio: {precio}
- Tendencias detectadas hoy: {', '.join(tendencias_reales)}

TU TAREA: Escribir un caption de Instagram que venda de verdad. No un texto genérico — uno que suene humano, que conecte con el chileno promedio, y que genere acción inmediata.

ESTRATEGIA SEGÚN EL PRODUCTO:
- Si es software/licencia → énfasis en AHORRO vs precio oficial + beneficio concreto inmediato ("actívalo hoy mismo")
- Si es cuenta de juego → emoción, exclusividad, comunidad gamer ("ya está disponible", "no te quedes sin el tuyo")
- Si es suscripción/servicio → valor por tiempo ("por solo X al mes"), lo que pierden si no lo tienen
- Si hay tendencia relevante → conéctala de forma natural al producto, no forzada

REGLAS DE ESCRITURA:
1. Primera línea = GANCHO que detiene el scroll. Opciones según el producto:
   - Pregunta que duele ("¿Todavía pagando el precio completo de {prod_info['detalle_producto'].split()[0]}?")
   - Dato impactante ("El 80% de la gente en Chile lo consigue por menos de la mitad")
   - Afirmación bold ("Esto cambia cómo trabajas desde hoy")
2. Desarrolla el beneficio CENTRAL en 2-3 líneas máximo. Concreto, no poético. Qué gana exactamente el que compra.
3. El precio {precio} debe aparecer como si fuera una revelación, no solo un número. Ejemplo: "y lo mejor: te sale en {precio} — sí, en serio."
4. Cierre con llamada a la acción directa y urgente. Sin "¡no lo pierdas!" genérico — algo específico al producto.
5. Tono: chileno natural, directo, con personalidad. Nada de "¡Hola a todos!" ni frases corporativas. Puede tener humor sutil si el producto lo permite.
6. Emojis: usarlos con intención, no como decoración. Máximo 6-8 en todo el texto.
7. Largo: 80-120 palabras. Ni más ni menos.

REGLA OBLIGATORIA DE CONTACTO: Justo antes de los hashtags, incluye exactamente esta línea:
📲 WhatsApp: +56946557876

HASHTAGS (exactamente 5, máximo 2 palabras cada uno):
- 2 del producto (ej: #Office365 #Software)
- 2 de tendencia o público (ej: #TrabajoRemoto #Chile)
- 1 de acción comercial (#Oferta #Deal #Descuento)
- PROHIBIDO hashtags de más de 2 palabras juntas

FORMATO DE SALIDA — solo esto, sin comentarios ni explicaciones:
[caption aquí]

📲 WhatsApp: +56946557876

#tag1 #tag2 #tag3 #tag4 #tag5
"""
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=400,
        temperature=0.7
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
    nombre = prod_info['detalle_producto']

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
    
    Your goal: write a MASTERCLASS-level prompt that pushes Ideogram v3 Balanced to its full potential.
    
    CRITICAL PRODUCT ACCURACY RULES:
    1. {contexto_estilo}
    2. The product "{nombre}" must be the HERO of the image — visually dominant, accurate, and recognizable. Do NOT invent generic product visuals. Base the design on the REAL product identity.
    3. Include REAL product-specific visual elements: if it's software, show UI screenshots or icons; if it's antivirus, show shields/protection; if it's CAD software, show 3D blueprints; if it's Office, show document/spreadsheet interfaces. Be specific.
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
    """Dibuja texto con efecto glow neón sobre la imagen. Devuelve la ruta del nuevo archivo."""
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
        import textwrap

        img = Image.open(imagen_path).convert("RGBA")
        w, h = img.size

        # Tamaño de fuente proporcional al ancho de la imagen
        font_size = max(40, int(w * 0.08))
        font = None
        # Intentar cargar fuentes del sistema en orden de preferencia
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
        glow_color = glow_color.lstrip('#')
        glow_rgb = tuple(int(glow_color[i:i+2], 16) for i in (0, 2, 4))

        # Wrap de texto según ancho
        max_chars = max(10, int(w / (font_size * 0.55)))
        lines = textwrap.wrap(texto.upper(), width=max_chars)
        if not lines:
            return imagen_path

        # Medir bloque de texto
        dummy_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        line_heights = []
        line_widths = []
        for line in lines:
            bbox = dummy_draw.textbbox((0, 0), line, font=font)
            line_widths.append(bbox[2] - bbox[0])
            line_heights.append(bbox[3] - bbox[1])
        line_spacing = int(font_size * 0.25)
        block_h = sum(line_heights) + line_spacing * (len(lines) - 1)
        max_line_w = max(line_widths)

        # Calcular Y según posición
        padding = int(h * 0.06)
        if posicion == 'top':
            block_y = padding
        elif posicion == 'bottom':
            block_y = h - block_h - padding
        else:  # center
            block_y = (h - block_h) // 2

        # ── Capa de glow: texto desenfocado en color neón ──
        glow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow_layer)
        cur_y = block_y
        for i, line in enumerate(lines):
            x = (w - line_widths[i]) // 2
            # Dibujar varias capas de glow con distintos radios
            for offset in range(1, 5):
                glow_draw.text((x, cur_y), line, font=font,
                               fill=(*glow_rgb, int(80 - offset * 15)))
            glow_draw.text((x, cur_y), line, font=font,
                           fill=(*glow_rgb, 200))
            cur_y += line_heights[i] + line_spacing

        # Desenfoque para efecto glow suave
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=font_size // 6))

        # Segunda pasada de glow más difuso
        glow_layer2 = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        glow_draw2 = ImageDraw.Draw(glow_layer2)
        cur_y = block_y
        for i, line in enumerate(lines):
            x = (w - line_widths[i]) // 2
            glow_draw2.text((x, cur_y), line, font=font, fill=(*glow_rgb, 100))
            cur_y += line_heights[i] + line_spacing
        glow_layer2 = glow_layer2.filter(ImageFilter.GaussianBlur(radius=font_size // 3))

        # ── Capa de texto blanco nítido encima ──
        text_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        text_draw = ImageDraw.Draw(text_layer)
        cur_y = block_y
        for i, line in enumerate(lines):
            x = (w - line_widths[i]) // 2
            # Sombra sutil para legibilidad
            text_draw.text((x + 2, cur_y + 2), line, font=font, fill=(0, 0, 0, 160))
            # Texto blanco
            text_draw.text((x, cur_y), line, font=font, fill=(255, 255, 255, 255))
            cur_y += line_heights[i] + line_spacing

        # Combinar capas: imagen base → glow difuso → glow → texto
        resultado = img.copy()
        resultado = Image.alpha_composite(resultado, glow_layer2)
        resultado = Image.alpha_composite(resultado, glow_layer)
        resultado = Image.alpha_composite(resultado, text_layer)

        # Guardar como JPEG
        resultado_rgb = resultado.convert("RGB")
        out_path = imagen_path.replace(".jpg", "_overlay.jpg").replace(".png", "_overlay.jpg")
        resultado_rgb.save(out_path, "JPEG", quality=92)
        log(f"✍️ Overlay de texto aplicado ✅ — '{texto}' ({posicion}, glow #{glow_color})", "success")
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

def ciclo_libre(busqueda, precio_manual="No especificado", cliente_id="aurakey", mood="energico", hacer_reel=True, imagen_referencia_url=None, style_weight=0.5):
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
    log(f'🔍 Ciclo libre para "{busqueda}" — Cliente: {nombre_cliente}...', 'info')
    prod_info = {
        "nombre": nombre_cliente,
        "detalle_producto": busqueda,
        "keyword_busqueda": busqueda,
        "nicho": busqueda,
        "tono": "profesional, vendedor, directo y confiable"
    }
    try:
        tendencias_reales = buscar_tendencias_reales_api(prod_info)
        gancho_usado = f"Tendencias en vivo: {', '.join(tendencias_reales[:2])}"
        log(f'✍️ Redactando post para "{busqueda}"...', 'info')
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

        if hacer_reel and imagen_filepath:
            audio_path = buscar_musica_pixabay(mood)
            if audio_path:
                video_path = generar_video_reel(imagen_filepath, audio_path, duracion=15)
                if video_path:
                    reel_generado = True
                    publicado_reel = publicar_reel_instagram(video_path, caption_completo, cliente_id)
            else:
                log("⚠️ Sin audio disponible, se omite el Reel.", "warning")
        elif not hacer_reel and imagen_filepath:
            publicado_post = publicar_en_instagram(imagen_filepath, caption_completo, cliente_id)

        publicado = publicado_post or publicado_reel
        entrada = {
            'cliente': f"{nombre_cliente} — {busqueda.upper()}",
            'cliente_id': cliente_id,
            'tendencia': gancho_usado,
            'caption': caption_completo,
            'prompt_imagen': prompt_imagen,
            'imagen_url': imagen_url_publica or '',
            'publicado': publicado,
            'reel_generado': reel_generado,
            'con_referencia': bool(imagen_referencia_url),
            'fecha': datetime.now().strftime('%d/%m %H:%M')
        }
        captions_guardados.insert(0, entrada)
        socketio.emit('caption', entrada)
        stats_global[cliente_id]['posts'] += 1
        stats_global[cliente_id]['ultimo_ciclo'] = datetime.now().strftime('%d/%m %H:%M')
        log(f'✅ Ciclo completo — Post: {"✅" if publicado_post else "—"} | Reel: {"✅" if publicado_reel else ("generado, sin CDN" if reel_generado else "—")}', 'success')
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


def publicar_imagen_propia_task(imagen_url, cliente_id, precio, modo, mood, overlay):
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

        vision_response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{img_b64}"}},
                {"type": "text", "text": (
                    "You are a product analyst. Identify: "
                    "1) Exact product or service shown. "
                    "2) Category (software, game account, subscription, physical product, etc.). "
                    "3) Any visible brand names or product names. "
                    "Max 60 words. English only."
                )}
            ]}],
            max_tokens=120,
            temperature=0.1,
        )
        descripcion_producto = vision_response.choices[0].message.content.strip()
        log(f"✅ Producto detectado: {descripcion_producto[:80]}...", "success")

        # 5. Generar caption con copywriter experto
        prod_info = {
            'nombre': cliente['nombre'],
            'detalle_producto': descripcion_producto,
            'keyword_busqueda': descripcion_producto.split()[0] if descripcion_producto else 'producto digital'
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
                cdn_url = subir_video_cloudinary(video_path)
                if cdn_url:
                    publicado = publicar_reel_instagram(cdn_url, caption, meta_token, ig_user_id)
        else:
            if meta_token and ig_user_id:
                publicado = publicar_en_instagram(imagen_url_final, caption, cliente_id)

        if publicado:
            log(f"✅ {'Reel' if modo == 'reel' else 'Post'} publicado en Instagram ✅", "success")
        else:
            log(f"⚠️ Generado pero no publicado en Instagram.", "warning")

        # Guardar en historial del dashboard
        entrada = {
            'cliente': cliente['nombre'],
            'cliente_id': cliente_id,
            'tendencia': tendencias[0] if tendencias else '—',
            'caption': caption,
            'prompt_imagen': f"[Imagen propia{' + overlay: ' + overlay['texto'] if overlay and overlay.get('texto') else ''}]",
            'imagen_url': imagen_url_final,
            'publicado': publicado,
            'reel_generado': reel_generado,
            'con_referencia': False,
            'fecha': datetime.now().strftime('%d/%m %H:%M')
        }
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


@app.route('/api/publicar_imagen_propia', methods=['POST'])
@requiere_auth
def api_publicar_imagen_propia():
    data = request.get_json() or {}
    imagen_url = data.get('imagen_url', '').strip()
    cliente_id = data.get('cliente_id', 'aurakey')
    precio = data.get('precio', 'Consultar por DM')
    modo = data.get('modo', 'post')
    mood = data.get('mood', 'energico')
    overlay = data.get('overlay', None)  # {'texto': '...', 'posicion': 'center', 'glow_color': '#00e5ff'}

    if not imagen_url:
        return jsonify({'ok': False, 'msg': '⚠️ No se recibió URL de imagen.'})
    with _bot_lock:
        if bot_activo:
            return jsonify({'ok': False, 'msg': '⚠️ Ya hay un ciclo corriendo. Esperá que termine.'})

    hilo = threading.Thread(
        target=publicar_imagen_propia_task,
        args=(imagen_url, cliente_id, precio, modo, mood, overlay),
        daemon=True
    )
    hilo.start()
    tipo = "Reel" if modo == "reel" else "Post"
    overlay_info = f" + texto '{overlay['texto']}'" if overlay and overlay.get('texto') else ""
    return jsonify({'ok': True, 'msg': f'✅ Procesando imagen propia como {tipo}{overlay_info}...'})


@app.route('/api/ciclo', methods=['POST'])
@requiere_auth
def api_ciclo():
    data = request.get_json() or {}
    precio = data.get('precio', 'Consultar por interno')
    busqueda_libre = data.get('busqueda_libre', '').strip()
    cliente_id = data.get('cliente_id', 'aurakey')
    mood = data.get('mood', 'energico')
    hacer_reel = data.get('hacer_reel', True)
    imagen_referencia_url = data.get('imagen_referencia_url', None)
    style_weight = float(data.get('style_weight', 0.5) or 0.5)
    if not busqueda_libre:
        return jsonify({'msg': '⚠️ Se requiere búsqueda libre para iniciar un ciclo.'})
    # 🔒 Verificar antes de lanzar el thread (doble guardia junto al Lock en ciclo_libre)
    with _bot_lock:
        if bot_activo:
            return jsonify({'msg': '⚠️ Ya hay un ciclo corriendo. Esperá que termine antes de iniciar otro.'})
    modo_img = "con referencia" if imagen_referencia_url else "solo texto"
    hilo = threading.Thread(target=ciclo_libre, args=(busqueda_libre, precio, cliente_id, mood, hacer_reel, imagen_referencia_url, style_weight))
    hilo.daemon = True
    hilo.start()
    return jsonify({'msg': f'Ciclo iniciado para: {busqueda_libre} (mood: {mood}, reel: {hacer_reel}, imagen: {modo_img})'})

# ============================================
# SCHEDULER CONFIGURABLE
# ============================================

scheduler_config = {
    "activo": False,
    "intervalo_horas": 2,
    "busqueda": "",
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
