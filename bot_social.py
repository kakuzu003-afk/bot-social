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
    # Para agregar un cliente nuevo, copia este bloque y cambia el ID:
    # "cliente1": {
    #     "nombre": "Nombre del cliente",
    #     "meta_token": os.environ.get("META_ACCESS_TOKEN_CLIENTE1"),
    #     "ig_user_id": os.environ.get("IG_USER_ID_CLIENTE1"),
    # },
    # "cliente2": {
    #     "nombre": "Otro cliente",
    #     "meta_token": os.environ.get("META_ACCESS_TOKEN_CLIENTE2"),
    #     "ig_user_id": os.environ.get("IG_USER_ID_CLIENTE2"),
    # },
}

PRODUCTOS_INFO = {
    "aurakey_autocad": {
        "nombre": "Aurakey",
        "detalle_producto": "Licencia Original de AutoCAD (Ideal para arquitectos, ingenieros y diseñadores)",
        "keyword_busqueda": "autocad",
        "nicho": "diseño e ingeniería, planos, arquitectura, software AutoCAD profesional",
        "tono": "profesional, directo, de alto valor, confiable y vendedor"
    },
    "aurakey_adobe": {
        "nombre": "Aurakey",
        "detalle_producto": "Suscripción Original a Adobe Creative Cloud (Acceso a Photoshop, Illustrator, Premiere Pro, etc.)",
        "keyword_busqueda": "adobe photoshop premiere",
        "nicho": "diseño gráfico, edición de video, fotografía, creadores de contenido, herramientas digitales de Adobe",
        "tono": "creativo, enérgico, moderno, disruptivo y vendedor"
    },
    "aurakey_windows": {
        "nombre": "Aurakey",
        "detalle_producto": "Licencia Original de Windows 10 / Windows 11 Pro",
        "keyword_busqueda": "windows 11 pro pro",
        "nicho": "sistemas operativos, optimización de PC, seguridad informática, computación y rendimiento",
        "tono": "técnico pero accesible, confiable, seguro y directo"
    },
    "aurakey_office": {
        "nombre": "Aurakey",
        "detalle_producto": "Licencia Original de Microsoft Office Professional Plus (Word, Excel, PowerPoint)",
        "keyword_busqueda": "microsoft office excel",
        "nicho": "herramientas de oficina, productividad, teletrabajo, estudiantes, organización y eficiencia",
        "tono": "profesional, enfocado en eficiencia, práctico y vendedor"
    }
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
    
    1. CAPTION: Redacta un copy persuasivo, vendedor y directo al grano (máximo 130 palabras). Debes incluir el precio de {precio} de forma muy atractiva e integrada en el texto. Agrega emojis modernos y un llamado a la acción claro invitando a comprar al DM.
    
    2. 5 HASHTAGS VIRALES (REGLA CRÍTICA): Agrega al final del post exactamente SOLO 5 hashtags separados por un espacio. Tienen que ser etiquetas reales, cortas y orgánicas que la gente de verdad use y busque en Instagram. No te limites a poner un '#' antes de los términos calientes que te pasé. Transfórmalos en conceptos de nicho reales.
    
    Ejemplo de lo que NO debes hacer (Prohibido): #MicrosoftOfficeExcelCertification #MicrosoftOfficeExcelDownload #MicrosoftOfficeExcelOnline
    Ejemplo de lo que SÍ debes hacer (Permitido): #excel #productividad #teletrabajo #aurakey #chile
    
    Formato estricto de salida:
    [Aquí va el texto de tu caption con emojis...]
    
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
    Eres un experto en generación de imágenes comerciales para Instagram.
    Producto: {prod_info['detalle_producto']}
    
    Genera un prompt en inglés para una imagen comercial atractiva. Sigue estas reglas estrictas:
    
    PROHIBIDO absolutamente:
    - NO mostrar documentos, contratos ni licencias impresas
    - NO mostrar nombres de personas ficticias ni datos inventados
    - NO mostrar números de serie, fechas de expedición ni formularios
    - NO texto ilegible ni datos falsos de ningún tipo
    
    DEBE mostrar:
    - Interfaz del software en una pantalla moderna o laptop elegante
    - Ambiente oscuro tipo estudio profesional con iluminación dramática
    - Estética tech premium, minimalista y moderna
    - Logo o interfaz real del software si es conocido
    - Composición vertical 9:16 optimizada para Instagram
    
    Estilo: dark cinematic studio, hyper-realistic, premium tech aesthetic, dramatic lighting, 
    ultra high quality commercial photography, no text overlays, no fake documents, no fake data.
    
    Max 80 words. Solo el prompt en inglés, sin explicaciones.
    """
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200,
        temperature=0.7
    )
    return response.choices[0].message.content

captions_guardados = []

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

def publicar_en_instagram(imagen_path, caption, cliente_id="aurakey", musica=""):
    """Publica en el Instagram del cliente especificado."""
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
                "access_token": meta_token,
                **({"audio_name": musica} if musica else {})
            }
        )
        data = res.json()
        container_id = data.get("id")

        if not container_id:
            log(f"❌ Error creando contenedor: {data}", "error")
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


def sugerir_cancion(prod_info, caption):
    """Usa Groq para sugerir la canción más adecuada para el post."""
    prompt = f"""
    Eres un experto en marketing musical para Instagram.
    Producto: {prod_info['detalle_producto']}
    Nicho: {prod_info.get('nicho', '')}
    Caption del post: {caption[:200]}
    
    Sugiere UNA sola canción real y popular que:
    - Esté disponible en el catálogo de Instagram/Meta
    - Sea perfecta para el ambiente del producto
    - Sea reconocible y tenga buen engagement
    
    Responde SOLO con el nombre exacto de la canción y el artista, sin explicaciones.
    Formato: Nombre de la canción - Artista
    Ejemplo: Blinding Lights - The Weeknd
    """
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50,
            temperature=0.6
        )
        cancion = response.choices[0].message.content.strip()
        log(f"🎵 Canción sugerida por IA: {cancion}", "success")
        return cancion
    except Exception as e:
        log(f"⚠️ No se pudo sugerir canción: {e}", "warning")
        return ""

def ciclo_libre(busqueda, precio_manual="No especificado", cliente_id="aurakey", con_musica=False):
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
        publicado = False

        if openai_api_key:
            imagen_filepath = generar_imagen_dalle(prompt_imagen)

        if imagen_filepath:
            imagen_url_publica = subir_imgbb(imagen_filepath)

        cancion = ""
        if con_musica:
            cancion = sugerir_cancion(prod_info, caption_completo)

        if imagen_filepath:
            publicado = publicar_en_instagram(imagen_filepath, caption_completo, cliente_id, cancion)

        entrada = {
            'cliente': f"{nombre_cliente} — {busqueda.upper()}",
            'cancion': cancion,
            'cliente_id': cliente_id,
            'tendencia': gancho_usado,
            'caption': caption_completo,
            'prompt_imagen': prompt_imagen,
            'imagen_url': imagen_url_publica or '',
            'publicado': publicado,
            'fecha': datetime.now().strftime('%d/%m %H:%M')
        }
        captions_guardados.insert(0, entrada)
        socketio.emit('caption', entrada)
        stats_global[cliente_id]['posts'] += 1
        stats_global[cliente_id]['ultimo_ciclo'] = datetime.now().strftime('%d/%m %H:%M')
        log(f'✅ Ciclo libre completo — Publicado: {"Sí ✅" if publicado else "No (manual)"}', 'success')

    except Exception as e:
        log(f'❌ Error en ciclo libre: {e}', 'error')

    socketio.emit('stats', stats_global)

def ciclo_completo(id_producto="aurakey_autocad", precio_manual="No especificado", cliente_id="aurakey", con_musica=False):
    global bot_activo
    bot_activo = True
    socketio.emit('bot_status', {'activo': True})
    
    cliente = CLIENTES.get(cliente_id, CLIENTES["aurakey"])
    nombre_cliente = cliente["nombre"]
    log(f'🚀 Iniciando ciclo para: {id_producto} — Cliente: {nombre_cliente}...', 'info')

    if id_producto not in PRODUCTOS_INFO:
        log(f'❌ Error: El producto "{id_producto}" no existe.', 'error')
        return

    prod_info = PRODUCTOS_INFO[id_producto].copy()
    prod_info['nombre'] = nombre_cliente  # usar el nombre del cliente seleccionado

    try:
        tendencias_reales = buscar_tendencias_reales_api(prod_info)
        gancho_usado = f"Tendencias en vivo: {', '.join(tendencias_reales[:2])}"

        log(f'✍️ Redactando post comercial...', 'info')
        caption_completo = generar_post_estricto(prod_info, tendencias_reales, precio_manual)

        log(f'🎨 Diseñando prompt visual optimizado 9:16...', 'info')
        prompt_imagen = generar_prompt_imagen(prod_info, caption_completo)

        imagen_filepath = None
        imagen_url_publica = None
        publicado = False

        if openai_api_key:
            imagen_filepath = generar_imagen_dalle(prompt_imagen)

        if imagen_filepath:
            imagen_url_publica = subir_imgbb(imagen_filepath)

        cancion = ""
        if con_musica:
            cancion = sugerir_cancion(prod_info, caption_completo)

        if imagen_filepath:
            publicado = publicar_en_instagram(imagen_filepath, caption_completo, cliente_id, cancion)

        entrada = {
            'cliente': f"{nombre_cliente} - {id_producto.split('_')[1].upper()}",
            'cancion': cancion,
            'cliente_id': cliente_id,
            'tendencia': gancho_usado,
            'caption': caption_completo,
            'prompt_imagen': prompt_imagen,
            'imagen_url': imagen_url_publica or '',
            'publicado': publicado,
            'fecha': datetime.now().strftime('%d/%m %H:%M')
        }
        captions_guardados.insert(0, entrada)
        socketio.emit('caption', entrada)

        stats_global[cliente_id]['posts'] += 1
        stats_global[cliente_id]['ultimo_ciclo'] = datetime.now().strftime('%d/%m %H:%M')
        log(f'✅ Ciclo completo — Publicado: {"Sí ✅" if publicado else "No (manual)"}', 'success')

    except Exception as e:
        log(f'❌ Error ejecutando el ciclo: {e}', 'error')

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
    producto = data.get('producto', 'aurakey_autocad')
    precio = data.get('precio', 'Consultar por interno')
    busqueda_libre = data.get('busqueda_libre', '').strip()
    cliente_id = data.get('cliente_id', 'aurakey')
    con_musica = data.get('con_musica', False)

    if busqueda_libre:
        hilo = threading.Thread(target=ciclo_libre, args=(busqueda_libre, precio, cliente_id, con_musica))
    else:
        hilo = threading.Thread(target=ciclo_completo, args=(producto, precio, cliente_id, con_musica))
    hilo.daemon = True
    hilo.start()
    return jsonify({'msg': f'Ciclo iniciado para: {busqueda_libre or producto}'})

# ============================================
# SCHEDULER
# ============================================
def run_scheduler():
    schedule.every(3).hours.do(ciclo_completo, id_producto="aurakey_autocad", precio_manual="Precio Especial", cliente_id="aurakey")
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
