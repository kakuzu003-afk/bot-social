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
    Eres un experto en crecimiento orgánico de Instagram, copywriting y SEO estratégico en redes sociales.
    Marca: {prod_info['nombre']}
    Producto: {prod_info['detalle_producto']}
    Precio actual de oferta: {precio}
    Términos calientes detectados hoy en la red: {', '.join(tendencias_reales)}
    
    Genera un post comercial para Instagram en español chileno neutro. Sigue estrictamente estas reglas:
    
    1. CAPTION: Redacta un copy persuasivo, vendedor y directo al grano (máximo 130 palabras). Debes incluir el precio de {precio} de forma muy atractiva e integrada en el texto. Agrega emojis modernos.
    
    REGLA OBLIGATORIA DE CONTACTO: Al final del texto, justo antes de los hashtags, incluye exactamente:
    📲 WhatsApp: +56946557876
    
    2. HASHTAGS (REGLA CRÍTICA):
    - Exactamente 5 hashtags
    - DEBEN ser MUY CORTOS: máximo 2 palabras cada uno
    - Ejemplos del estilo correcto: #Office2024 #Software #Productividad #Oferta #Chile
    - PROHIBIDO: hashtags largos como #LicenciaPermanenteOffice o #SoftwareOriginalChile
    - Usa palabras que la gente escribe rápido y busca masivamente
    - Basados en el producto: {prod_info['detalle_producto']} y tendencias: {', '.join(tendencias_reales)}
    - Mezcla: 2 del producto + 2 tendencia + 1 acción corta (#Oferta #Compra #Deal)
    
    Formato estricto de salida:
    [Aquí va el texto de tu caption con emojis...]
    
    📲 WhatsApp: +56946557876
    
    #Hashtag1 #Hashtag2 #Hashtag3 #Hashtag4 #Hashtag5
    
    Ve directo al contenido del post. No metas notas del sistema, saludos ni introducciones.
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
                            "Analyze this reference image for a commercial advertisement. "
                            "Describe in detail: color palette (exact colors and tones), "
                            "visual style (minimalist, 3D, neon, luxury, etc.), "
                            "composition and layout, typography style if any, "
                            "lighting and mood, and any distinctive graphic elements. "
                            "Be specific and technical. Max 80 words. English only."
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
    You are an expert prompt engineer for Ideogram v3 graphic design.
    Product to advertise: "{nombre}"
    
    Write a high-end commercial advertisement prompt.
    
    CRITICAL RULES:
    1. {contexto_estilo}
    2. The output MUST include the text "{nombre}" perfectly spelled.
    3. The typography must be elegant, proportional, and integrated into the design. DO NOT make the text so giant that it ruins the layout. Leave breathing room.
    4. Include beautiful graphic elements related to the product (e.g., sleek shapes, abstract details, or glowing branding icons).
    5. No cheap speed lines, no generic explosion backgrounds. Keep it premium.
    6. Vertical 9:16 format.
    
    OUTPUT RULES:
    - Write ONLY the prompt in English, max 85 words
    - The product name "{nombre}" must appear in quotes in your output
    - No preamble, no notes, no explanations
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

        parametros = {
            "prompt": prompt_imagen,
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
                    parametros["style_reference_images"] = [imagen_ref_bytes]  # ✅ FIX: debe ser array
                    parametros["style_type"] = "Auto"   # Auto: Ideogram detecta el estilo de la referencia
                    parametros["style_weight"] = style_weight  # 0.0 = ignorar ref | 1.0 = copiar al 100%
                    log(f"✅ Referencia de estilo inyectada en Ideogram (style_weight={style_weight}).", "success")
                else:
                    log(f"⚠️ No se pudo descargar la imagen de referencia (HTTP {img_response.status_code}). Generando sin referencia.", "warning")
            except Exception as ref_err:
                log(f"⚠️ Error al cargar referencia: {ref_err}. Continuando sin referencia.", "warning")

        log(f"🖼️ Generando con Ideogram v3 Turbo{'  + referencia de estilo' if imagen_referencia_url else ' de forma estable'}...", "info")
        output = client.run(
            "ideogram-ai/ideogram-v3-turbo",
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

        # Si hay imagen de referencia, Groq la analiza con visión primero
        descripcion_referencia = None
        if imagen_referencia_url:
            descripcion_referencia = analizar_imagen_referencia(imagen_referencia_url)

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
        log(f'❌ Error en ciclo libre: {e}', 'error')
    finally:
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
    log("⏰ Scheduler: disparando ciclo automático...", "info")
    scheduler_config["ciclos_ejecutados"] += 1
    # Calcular próximo ciclo
    from datetime import timedelta
    proximo = datetime.now() + timedelta(hours=scheduler_config["intervalo_horas"])
    scheduler_config["proximo_ciclo"] = proximo.strftime("%d/%m %H:%M")
    socketio.emit("scheduler_status", scheduler_config)
    ciclo_libre(
        busqueda=scheduler_config["busqueda"],
        precio_manual=scheduler_config["precio"],
        cliente_id=scheduler_config["cliente_id"],
        mood=scheduler_config["mood"],
        hacer_reel=scheduler_config["hacer_reel"],
        imagen_referencia_url=scheduler_config["imagen_referencia_url"],
        style_weight=scheduler_config["style_weight"],
    )

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
    socketio.run(app, host='0.0.0.0', port=puerto, debug=False, allow_unsafe_werkzeug=True)
