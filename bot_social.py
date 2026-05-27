from flask import Flask, render_template, jsonify, request
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
import openai

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sushiloveaurakey2025'
socketio = SocketIO(app, cors_allowed_origins="*")

# ============================================
# CONFIGURACIÓN GLOBAL E INICIALIZACIÓN
# ============================================
groq_api_key = os.environ.get("GROQ_API_KEY")

if not groq_api_key:
    raise ValueError("❌ ERROR: La variable de entorno GROQ_API_KEY no está configurada en Railway.")

openai_api_key = os.environ.get("OPENAI_API_KEY")

groq_client = Groq(api_key=groq_api_key)

sesiones = {}
stats_global = {}
logs_global = []
bot_activo = False

# ============================================
# CLIENTES — agrega aquí cada cliente nuevo
# Para cada cliente necesitas en Railway:
#   META_ACCESS_TOKEN_<ID>   (ej: META_ACCESS_TOKEN_CLIENTE1)
#   IG_USER_ID_<ID>          (ej: IG_USER_ID_CLIENTE1)
# ============================================
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
# FUNCIONES DE IA, LOGS Y BÚSQUEDA EN VIVO
# ============================================

def log(msg, tipo='info'):
    entrada = {'msg': msg, 'tipo': tipo, 'hora': datetime.now().strftime('%H:%M:%S')}
    logs_global.append(entrada)
    if len(logs_global) > 100:
        logs_global.pop(0)
    socketio.emit('log', entrada)
    print(f"[{tipo.upper()}] {msg}")

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

def generar_post_estricto(prod_info, tendencias_reales, precio):
    prompt = f"""
    Eres un experto en crecimiento orgánico de Instagram, copywriting y SEO estratégico en redes sociales.
    Marca: {prod_info['nombre']}
    Producto: {prod_info['detalle_producto']}
    Precio actual de oferta: {precio}
    Términos calientes detectados hoy en la red: {', '.join(tendencias_reales)}
    
    Genera un post comercial para Instagram en español chileno neutro. Sigue estrictamente estas dos reglas obligatorias:
    
    1. CAPTION: Redacta un copy persuasivo, vendedor y directo al grano (máximo 130 palabras). Debes incluir el precio de {precio} de forma muy atractiva e integrada en el texto. Agrega emojis modernos. 
    
    REGLA OBLIGATORIA DE CONTACTO: Al final del texto, justo antes de los hashtags, debes incluir obligatoriamente un llamado a la acción para comprar que incluya exactamente esta línea:
    📲 WhatsApp: +56946557876
    
    2. 5 HASHTAGS VIRALES (REGLA CRÍTICA): Agrega al final del post exactamente SOLO 5 hashtags separados por un espacio. Tienen que ser etiquetas reales, cortas y orgánicas que la gente de verdad use y busque en Instagram. No te limites a poner un '#' antes de los términos calientes que te pasé. Transfórmalos en conceptos de nicho reales.
    
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
def generar_prompt_imagen(prod_info, caption):
    prompt = f"""
    You are a world-class commercial 3D artist and creative director specializing in high-converting Instagram ads.
    Product to feature: {prod_info['detalle_producto']}
    
    Generate a highly detailed, visually stunning prompt in English for an image generation model. Follow these precise guidelines:
    
    1. VISUAL CONCEPT & SCENERY:
       - Create a grand, premium conceptual scene that represents the essence of the product. 
       - Avoid boring, flat tech setups. Instead, use metaphorical, abstract, or heroic representations. For digital software or services, you can visualize the product's core identity (like glowing modern icons, sleek dark key cards, or abstract data structures) floating as premium 3D crystal or metallic objects in a futuristic, cinematic space.
       - Use dynamic elements like volumetric smoke, floating particles, sharp reflections, and dramatic rim lighting to give it a luxury catalog feel.
    
    2. COLOR & LIGHTING:
       - Use high-contrast color theory. Incorporate cinematic neon accent lights (such as vibrant cyan, deep purple, or electric orange hues) slicing through a moody, dark premium atmosphere. 
    
    3. SHOT SPECIFICATIONS:
       - Composition: Vertical 9:16 framing, macro or close-up heroic shot, strong depth of field with an elegant blurred background.
    
    4. ABSOLUTE PROHIBITIONS (CRITICAL):
       - NO text overlays, NO written words, NO blurry fake logos, NO typos, NO paper documents.
       - NO human faces with distorted features, NO serial numbers.
    
    Style: Photorealistic commercial 3D render, Unreal Engine 5 style, hyper-detailed textures, cinematic lighting, premium dark tech aesthetic.
    
    Max 80 words. Output ONLY the English prompt. No introductions, no notes.
    """
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=250,
        temperature=0.85
    )
    return response.choices[0].message.content

captions_guardados = []

# ============================================
# PIXABAY — BÚSQUEDA DE MÚSICA POR MOOD
# ============================================

MOOD_QUERIES = {
    "energico":   "upbeat electronic",
    "motivador":  "motivational epic",
    "relajado":   "calm ambient",
    "corporativo": "corporate background",
    "misterioso": "dark cinematic",
    "alegre":     "happy pop",
}

def buscar_musica_pixabay(mood="energico"):
    """Busca una pista de música libre en Pixabay según el mood. Retorna ruta local del .mp3."""
    pixabay_key = os.environ.get("PIXABAY_API_KEY")
    if not pixabay_key:
        log("⚠️ PIXABAY_API_KEY no configurada. Saltando música.", "warning")
        return None

    query = MOOD_QUERIES.get(mood, mood)
    log(f"🎵 Buscando música Pixabay — mood: {mood} → query: '{query}'", "info")

    try:
        # Endpoint oficial unificado para peticiones de audio/música sin conflicto de rutas
        url = "https://pixabay.com/api/"
        params = {
            "key": pixabay_key.strip(),
            "q": query,
            "media_type": "music",  # Pasamos el tipo como parámetro para evitar el 404 de la ruta
            "per_page": 5,
        }
        res = req.get(url, params=params, timeout=10)
        
        if res.status_code != 200:
            log(f"⚠️ Pixabay respondió con código de error {res.status_code}. Verifica tu API Key.", "warning")
            return None
            
        data = res.json()
        hits = data.get("hits", [])
        if not hits:
            log(f"⚠️ Sin resultados de música para '{query}'.", "warning")
            return None

        pista = None
        for hit in hits:
            # Pixabay entrega la URL de descarga en 'audio' o en 'previewURL' según la versión de la API
            audio_url = hit.get("audio", {}).get("mp3", "") if isinstance(hit.get("audio"), dict) else hit.get("previewURL", "")
            if not audio_url and "audio" in hit and isinstance(hit["audio"], str):
                audio_url = hit["audio"]
                
            if audio_url:
                pista = {"titulo": hit.get("tags", "pista"), "url": audio_url}
                break

        if not pista:
            log("⚠️ Ninguna pista con URL de audio encontrada.", "warning")
            return None

        log(f"🎶 Pista encontrada: {pista['titulo']}", "success")

        os.makedirs("static", exist_ok=True)
        audio_path = f"static/audio_{int(time.time())}.mp3"
        r_audio = req.get(pista["url"], timeout=30)
        with open(audio_path, "wb") as f:
            f.write(r_audio.content)
        log(f"⬇️ Audio descargado → {audio_path}", "success")
        return audio_path

    except Exception as e:
        log(f"❌ Error buscando música en Pixabay: {e}", "error")
        return None

# ============================================
# FFMPEG — COMBINAR IMAGEN + AUDIO → VIDEO
# ============================================

def generar_video_reel(imagen_path, audio_path, duracion=15):
    """Combina imagen + audio con ffmpeg para generar un Reel MP4 en formato 9:16."""
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
            "-pix_fmt", "yuv420p",
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
        log("❌ ffmpeg no está instalado. Agrega 'ffmpeg' a tu Dockerfile/buildpack.", "error")
        return None
    except Exception as e:
        log(f"❌ Error generando video: {e}", "error")
        return None


# ============================================
# GRAPH API — PUBLICAR COMO REEL EN INSTAGRAM
# ============================================

def subir_video_a_cdn(video_path):
    cloudinary_url = os.environ.get("CLOUDINARY_URL")
    if cloudinary_url:
        try:
            import cloudinary
            import cloudinary.uploader
            cloudinary.config(cloudinary_url=cloudinary_url)
            result = cloudinary.uploader.upload(
                video_path,
                resource_type="video",
                folder="reels"
            )
            url = result.get("secure_url")
            if url:
                log(f"☁️ Video subido a Cloudinary ✅", "success")
                return url
        except Exception as e:
            log(f"⚠️ Error subiendo a Cloudinary: {e}", "warning")

    log("⚠️ Sin CDN de video configurado (CLOUDINARY_URL). El Reel requiere URL pública.", "warning")
    return None


def publicar_reel_instagram(video_path, caption, cliente_id="aurakey"):
    cliente = CLIENTES.get(cliente_id)
    if not cliente:
        log(f"❌ Cliente '{cliente_id}' no encontrado.", "error")
        return False

    meta_token = cliente.get("meta_token")
    ig_user_id = cliente.get("ig_user_id")

    if not meta_token or not ig_user_id:
        log(f"⚠️ Credenciales de Instagram no configuradas para {cliente['nombre']}.", "warning")
        return False

    try:
        video_url = subir_video_a_cdn(video_path)
        if not video_url:
            log("❌ No se pudo obtener URL pública del video. Reel no publicado.", "error")
            return False

        log(f"📤 Creando contenedor Reel en Graph API para {cliente['nombre']}...", "info")
        res = req.post(
            f"https://graph.facebook.com/v19.0/{ig_user_id}/media",
            data={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "access_token": meta_token
            }
        )
        data = res.json()
        container_id = data.get("id")

        if not container_id:
            log(f"❌ Error creando contenedor Reel: {data}", "error")
            return False

        log(f"⏳ Esperando que Meta procese el video (puede tardar ~30s)...", "info")
        max_intentos = 15
        listo = False
        for intento in range(max_intentos):
            time.sleep(6)
            check = req.get(
                f"https://graph.facebook.com/v19.0/{container_id}",
                params={"fields": "status_code", "access_token": meta_token}
            ).json()
            status = check.get("status_code")
            log(f"📡 Estado Reel ({intento+1}/{max_intentos}): {status}", "info")
            if status == "FINISHED":
                listo = True
                break
            elif status == "ERROR":
                log(f"❌ Meta rechazó el video: {check}", "error")
                return False

        if not listo:
            log("❌ Timeout: Meta no procesó el Reel a tiempo.", "error")
            return False

        log(f"🚀 Publicando Reel en Instagram de {cliente['nombre']}...", "info")
        res2 = req.post(
            f"https://graph.facebook.com/v19.0/{ig_user_id}/media_publish",
            data={"creation_id": container_id, "access_token": meta_token}
        )
        data2 = res2.json()

        if data2.get("id"):
            log(f"🎬 Reel publicado en Instagram de {cliente['nombre']}! ID: {data2['id']}", "success")
            return True
        else:
            log(f"❌ Error publicando Reel: {data2}", "error")
            return False

    except Exception as e:
        log(f"❌ Error en Graph API (Reel): {e}", "error")
        return False


# ============================================
# DALL-E 3 — GENERACIÓN DE IMAGEN
# ============================================

def generar_imagen_dalle(prompt_imagen):
    if not openai_api_key:
        log("⚠️ OPENAI_API_KEY no configurada. Saltando generación de imagen.", "warning")
        return None
    try:
        import base64
        client = openai.OpenAI(api_key=openai_api_key)
        response = client.images.generate(
            model="gpt-image-1",
            prompt=prompt_imagen,
            size="1024x1024",
            n=1
        )
        image_data = response.data[0].b64_json
        img_bytes = base64.b64decode(image_data)
        os.makedirs("static", exist_ok=True)
        filename = f"img_{int(time.time())}.png"
        filepath = f"static/{filename}"
        with open(filepath, "wb") as f:
            f.write(img_bytes)
        log(f"🖼️ Imagen generada con gpt-image-1 ✅", "success")
        return filepath
    except Exception as e:
        log(f"❌ Error generando imagen: {e}", "error")
        return None

# ============================================
# GRAPH API — PUBLICAR EN INSTAGRAM
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
        res = req.post(
            "https://api.imgbb.com/1/upload",
            data={"key": imgbb_key, "image": img_b64}
        )
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
        log(f"⚠️ Credenciales de Instagram no configuradas para {cliente['nombre']}. Guardado para publicación manual.", "warning")
        return False
    try:
        imagen_url = subir_imgbb(imagen_path)
        if not imagen_url:
            log("❌ No se pudo obtener URL pública de la imagen.", "error")
            return False

        log(f"📤 Creando contenedor en Graph API para {cliente['nombre']}...", "info")
        res = req.post(
            f"https://graph.facebook.com/v19.0/{ig_user_id}/media",
            data={
                "image_url": imagen_url,
                "caption": caption,
                "access_token": meta_token
            }
        )
        data = res.json()
        container_id = data.get("id")

        if not container_id:
            log(f"❌ Error creando contenedor: {data}", "error")
            return False

        log(f"⏳ Esperando que Meta procese la imagen...", "info")
        max_intentos = 10
        listo = False
        for intento in range(max_intentos):
            time.sleep(4)
            check = req.get(
                f"https://graph.facebook.com/v19.0/{container_id}",
                params={
                    "fields": "status_code",
                    "access_token": meta_token
                }
            ).json()
            status = check.get("status_code")
            log(f"📡 Estado contenedor ({intento+1}/{max_intentos}): {status}", "info")
            if status == "FINISHED":
                listo = True
                break
            elif status == "ERROR":
                log(f"❌ Meta rechazó la imagen: {check}", "error")
                return False

        if not listo:
            log(f"❌ Timeout: Meta no procesó la imagen a tiempo.", "error")
            return False

        log(f"🚀 Publicando en Instagram de {cliente['nombre']}...", "info")
        res2 = req.post(
            f"https://graph.facebook.com/v19.0/{ig_user_id}/media_publish",
            data={
                "creation_id": container_id,
                "access_token": meta_token
            }
        )
        data2 = res2.json()

        if data2.get("id"):
            log(f"✅ Post publicado en Instagram de {cliente['nombre']}! ID: {data2['id']}", "success")
            return True
        else:
            log(f"❌ Error publicando: {data2}", "error")
            return False

    except Exception as e:
        log(f"❌ Error en Graph API: {e}", "error")
        return False


def ciclo_libre(busqueda, precio_manual="No especificado", cliente_id="aurakey", mood="energico", hacer_reel=True):
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
        prompt_imagen = generar_prompt_imagen(prod_info, caption_completo)

        imagen_filepath = None
        imagen_url_publica = None
        publicado_post = False
        publicado_reel = False
        reel_generado = False

        if openai_api_key:
            imagen_filepath = generar_imagen_dalle(prompt_imagen)

        if imagen_filepath:
            imagen_url_publica = subir_imgbb(imagen_filepath)
            publicado_post = publicar_en_instagram(imagen_filepath, caption_completo, cliente_id)

        # ── FLUJO REEL ──────────────────────────────────────────
        if hacer_reel and imagen_filepath:
            audio_path = buscar_musica_pixabay(mood)
            if audio_path:
                video_path = generar_video_reel(imagen_filepath, audio_path, duracion=10)
                if video_path:
                    reel_generado = True
                    publicado_reel = publicar_reel_instagram(video_path, caption_completo, cliente_id)
            else:
                log("⚠️ Sin audio disponible, se omite el Reel.", "warning")
        # ────────────────────────────────────────────────────────

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
            'fecha': datetime.now().strftime('%d/%m %H:%M')
        }
        captions_guardados.insert(0, entrada)
        socketio.emit('caption', entrada)
        stats_global[cliente_id]['posts'] += 1
        stats_global[cliente_id]['ultimo_ciclo'] = datetime.now().strftime('%d/%m %H:%M')
        log(f'✅ Ciclo completo — Post: {"✅" if publicado_post else "—"} | Reel: {"✅" if publicado_reel else ("generado, sin CDN" if reel_generado else "—")}', 'success')

    except Exception as e:
        log(f'❌ Error en ciclo libre: {e}', 'error')

    socketio.emit('stats', stats_global)


# ============================================
# RUTAS API
# ============================================

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/api/clientes')
def api_clientes():
    lista = [{"id": k, "nombre": v["nombre"]} for k, v in CLIENTES.items()]
    return jsonify(lista)

@app.route('/api/stats')
def api_stats():
    return jsonify(stats_global)

@app.route('/api/captions')
def api_captions():
    return jsonify(captions_guardados)

@app.route('/api/ciclo', methods=['POST'])
def api_ciclo():
    data = request.get_json() or {}
    precio = data.get('precio', 'Consultar por interno')
    busqueda_libre = data.get('busqueda_libre', '').strip()
    cliente_id = data.get('cliente_id', 'aurakey')
    mood = data.get('mood', 'energico')
    hacer_reel = data.get('hacer_reel', True)

    if not busqueda_libre:
        return jsonify({'msg': '⚠️ Se requiere búsqueda libre para iniciar un ciclo.'})

    hilo = threading.Thread(target=ciclo_libre, args=(busqueda_libre, precio, cliente_id, mood, hacer_reel))
    hilo.daemon = True
    hilo.start()
    return jsonify({'msg': f'Ciclo iniciado para: {busqueda_libre} (mood: {mood}, reel: {hacer_reel})'})

# ============================================
# SCHEDULER
# ============================================
def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == '__main__':
    print("🤖 Social Bot Manager - Activado")
    
    hilo_scheduler = threading.Thread(target=run_scheduler)
    hilo_scheduler.daemon = True
    hilo_scheduler.start()

    puerto = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=puerto, debug=False, allow_unsafe_werkzeug=True)
