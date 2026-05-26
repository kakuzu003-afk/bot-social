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
meta_token     = os.environ.get("META_ACCESS_TOKEN")
ig_user_id     = os.environ.get("IG_USER_ID")

groq_client = Groq(api_key=groq_api_key)

sesiones = {}
stats_global = {}
logs_global = []
bot_activo = False

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

for clave, info in PRODUCTOS_INFO.items():
    stats_global[info['nombre']] = {
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
    # Prompt ultra optimizado con ejemplos negativos y positivos para frenar los hashtags robóticos
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
    Eres un experto en Midjourney y DALL-E 3.
    Basándote en este producto: {prod_info['detalle_producto']}.
    Genera un prompt detallado en inglés para crear una imagen comercial fotorrealista para Instagram.
    Estilo: cinematic shot, 9:16 vertical format, hyper-realistic, clean studio lighting, modern tech layout. Max 80 words. No explanations.
    """
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=150,
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
        client = openai.OpenAI(api_key=openai_api_key)
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt_imagen,
            size="1024x1024",
            quality="standard",
            n=1
        )
        url = response.data[0].url
        log(f"🖼️ Imagen generada con DALL-E 3 ✅", "success")
        return url
    except Exception as e:
        log(f"❌ Error generando imagen con DALL-E: {e}", "error")
        return None

# ============================================
# GRAPH API — PUBLICAR EN INSTAGRAM
# ============================================

def publicar_en_instagram(imagen_url, caption):
    if not meta_token or not ig_user_id:
        log("⚠️ META_ACCESS_TOKEN o IG_USER_ID no configurados. Saltando publicación.", "warning")
        return False
    try:
        # Paso 1: crear contenedor de media
        log("📤 Creando contenedor en Graph API...", "info")
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

        # Paso 2: publicar el contenedor
        log("🚀 Publicando en Instagram...", "info")
        res2 = req.post(
            f"https://graph.facebook.com/v19.0/{ig_user_id}/media_publish",
            data={
                "creation_id": container_id,
                "access_token": meta_token
            }
        )
        data2 = res2.json()

        if data2.get("id"):
            log(f"✅ Post publicado en Instagram! ID: {data2['id']}", "success")
            return True
        else:
            log(f"❌ Error publicando: {data2}", "error")
            return False

    except Exception as e:
        log(f"❌ Error en Graph API: {e}", "error")
        return False

def ciclo_completo(id_producto="aurakey_autocad", precio_manual="No especificado"):
    global bot_activo
    bot_activo = True
    socketio.emit('bot_status', {'activo': True})
    log(f'🚀 Iniciando ciclo inteligente para: {id_producto}...', 'info')

    if id_producto not in PRODUCTOS_INFO:
        log(f'❌ Error: El producto "{id_producto}" no existe.', 'error')
        return

    prod_info = PRODUCTOS_INFO[id_producto]
    nombre_marca = prod_info['nombre']

    try:
        tendencias_reales = buscar_tendencias_reales_api(prod_info)
        gancho_usado = f"Tendencias en vivo: {', '.join(tendencias_reales[:2])}"

        log(f'✍️ Redactando post comercial y calculando exactamente 5 hashtags...', 'info')
        caption_completo = generar_post_estricto(prod_info, tendencias_reales, precio_manual)

        log(f'🎨 Diseñando prompt visual optimizado 9:16...', 'info')
        prompt_imagen = generar_prompt_imagen(prod_info, caption_completo)

        # Generar imagen con DALL-E 3
        imagen_url = None
        publicado = False
        if openai_api_key:
            imagen_url = generar_imagen_dalle(prompt_imagen)

        # Publicar en Instagram vía Graph API
        if imagen_url and meta_token and ig_user_id:
            publicado = publicar_en_instagram(imagen_url, caption_completo)
        elif not meta_token or not ig_user_id:
            log("ℹ️ Sin credenciales Meta — caption guardado para publicación manual.", "warning")

        entrada = {
            'cliente': f"{nombre_marca} - {id_producto.split('_')[1].upper()}",
            'tendencia': gancho_usado,
            'caption': caption_completo,
            'prompt_imagen': prompt_imagen,
            'imagen_url': imagen_url or '',
            'publicado': publicado,
            'fecha': datetime.now().strftime('%d/%m %H:%M')
        }
        captions_guardados.insert(0, entrada)
        socketio.emit('caption', entrada)

        stats_global[nombre_marca]['posts'] += 1
        stats_global[nombre_marca]['ultimo_ciclo'] = datetime.now().strftime('%d/%m %H:%M')
        log(f'✅ Ciclo completo para {id_producto} — Publicado: {"Sí ✅" if publicado else "No (manual)"}', 'success')

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
    lista = [{"nombre": "Aurakey"}]
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

    hilo = threading.Thread(target=ciclo_completo, args=(producto, precio))
    hilo.daemon = True
    hilo.start()
    return jsonify({'msg': f'Ciclo en tiempo real iniciado para {producto}'})

# ============================================
# SCHEDULER
# ============================================
def run_scheduler():
    schedule.every(3).hours.do(ciclo_completo, id_producto="aurakey_autocad", precio_manual="Precio Especial")
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == '__main__':
    print("🤖 Social Bot Manager - Activado")
    
    hilo_scheduler = threading.Thread(target=run_scheduler)
    hilo_scheduler.daemon = True
    hilo_scheduler.start()

    puerto = int(os.environ.get("PORT", 5000))
    # Corregido: Usamos socketio.run para que no se caiga la transmisión de logs en vivo hacia la web
    socketio.run(app, host='0.0.0.0', port=puerto, debug=False, allow_unsafe_werkzeug=True)
