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
from instagrapi import Client
import requests as req

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sushiloveaurakey2025'
socketio = SocketIO(app, cors_allowed_origins="*")

# ============================================
# CONFIGURACIÓN GLOBAL E INICIALIZACIÓN (RAILWAY)
# ============================================
groq_api_key = os.environ.get("GROQ_API_KEY")

if not groq_api_key:
    raise ValueError("❌ ERROR: La variable de entorno GROQ_API_KEY no está configurada en Railway.")

groq_client = Groq(api_key=groq_api_key)

sesiones = {}
stats_global = {}
logs_global = []
bot_activo = False

# Mapeo interno para identificar qué producto se seleccionó en el Dashboard
PRODUCTOS_INFO = {
    "aurakey_software": {
        "nombre": "Aurakey",
        "detalle_producto": "Licencias de Software Originales (AutoCAD, Adobe Creative Cloud, Windows, Office)",
        "nicho": "licencias de software, AutoCAD, Adobe, tecnología y herramientas de productividad",
        "hashtags": ["#software", "#autocad", "#adobe", "#tecnologia", "#diseño", "#aurakey", "#chile"],
        "tono": "profesional, directo, confiable y vendedor"
    }
    # En el futuro, cuando agregues más <option> en el HTML, solo pones su configuración aquí abajo.
}

# Inicializar estadísticas usando la estructura
for clave, info in PRODUCTOS_INFO.items():
    stats_global[info['nombre']] = {
        'posts': 0,
        'comentarios': 0,
        'likes': 0,
        'interacciones': 0,
        'ultimo_ciclo': 'Nunca'
    }

# ============================================
# FUNCIONES DE IA Y LOGS
# ============================================

def log(msg, tipo='info'):
    entrada = {'msg': msg, 'tipo': tipo, 'hora': datetime.now().strftime('%H:%M:%S')}
    logs_global.append(entrada)
    if len(logs_global) > 100:
        logs_global.pop(0)
    socketio.emit('log', entrada)
    print(f"[{tipo.upper()}] {msg}")

def generar_caption(prod_info, tendencia, precio):
    # Modificamos el prompt para forzar a la IA a integrar el precio manual
    prompt = f"""
    Eres un experto en marketing digital para Instagram enfocado en ventas y alta conversión.
    Marca: {prod_info['nombre']}
    Producto Específico a promocionar hoy: {prod_info['detalle_producto']}
    Nicho: {prod_info['nicho']}
    Tono: {prod_info['tono']}
    Tendencia del día: {tendencia}
    PRECIO DE VENTA: {precio}
    
    Genera un caption muy atractivo para Instagram en español chileno neutro, máximo 150 palabras.
    REGLA OBLIGATORIA: Debes integrar e informar de forma clara, natural y llamativa el PRECIO DE VENTA ({precio}) dentro del texto o como parte de una oferta.
    Incluye emojis estratégicos y un fuerte Call to Action (Llamado a la acción) invitando a comprar o preguntar al DM.
    Solo responde con el caption terminado, sin introducciones ni comentarios adicionales.
    """
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=300,
        temperature=0.8
    )
    return response.choices[0].message.content

def generar_prompt_imagen(prod_info, tendencia, caption):
    prompt = f"""
    Eres un experto en generar prompts para IA de imágenes como Midjourney, Kling o DALL-E.
    
    Marca: {prod_info['nombre']}
    Producto: {prod_info['detalle_producto']}
    Nicho: {prod_info['nicho']}
    Tendencia: {tendencia}
    Caption de Instagram: {caption[:200]}
    
    Genera un prompt en inglés para crear una imagen fotorrealista y comercial para Instagram.
    El prompt debe:
    - Ser en inglés.
    - Describir una escena visual moderna, limpia e ideal para acompañar la promoción de este producto.
    - Incluir estilo: cinematic, 9:16 vertical, high quality, studio lighting, clean background, Instagram aesthetic.
    - Máximo 100 palabras.
    - Solo responde con el prompt en inglés, sin explicaciones.
    """
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200,
        temperature=0.8
    )
    return response.choices[0].message.content

def buscar_tendencias(prod_info):
    prompt = f"""
    Dame 5 tendencias o ganchos de contenido para Instagram relacionados con: {prod_info['nicho']}
    Formato estricto:
    1. tendencia
    2. tendencia
    3. tendencia
    4. tendencia
    5. tendencia
    Solo las tendencias, sin saludos ni explicaciones.
    """
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200,
        temperature=0.9
    )
    tendencias = response.choices[0].message.content.strip().split('\n')
    return [t.split('. ', 1)[1] if '. ' in t else t for t in tendencias if t.strip()]

captions_guardados = []

def ciclo_completo(id_producto="aurakey_software", precio_manual="No especificado"):
    global bot_activo
    bot_activo = True
    socketio.emit('bot_status', {'activo': True})
    log(f'🚀 Iniciando ciclo para el producto: {id_producto}...', 'info')

    # Verificar si el producto existe en nuestra base de datos
    if id_producto not in PRODUCTOS_INFO:
        log(f'❌ Error: El producto "{id_producto}" no está configurado en Python.', 'error')
        return

    prod_info = PRODUCTOS_INFO[id_producto]
    nombre_marca = prod_info['nombre']

    try:
        log(f'🔍 Buscando ángulos de contenido para {nombre_marca}...', 'info')
        tendencias = buscar_tendencias(prod_info)
        tendencia = random.choice(tendencias)
        log(f'📌 Ángulo elegido: {tendencia}', 'info')

        log(f'✍️ Redactando caption comercial con precio: {precio_manual}...', 'info')
        caption = generar_caption(prod_info, tendencia, precio_manual)
        hashtags = ' '.join(prod_info['hashtags'])
        caption_completo = f"{caption}\n\n{hashtags}"

        log(f'🎨 Estructurando prompt fotorrealista para la imagen...', 'info')
        prompt_imagen = generar_prompt_imagen(prod_info, tendencia, caption)

        entrada = {
            'cliente': f"{nombre_marca} ({precio_manual})",
            'tendencia': tendencia,
            'caption': caption_completo,
            'prompt_imagen': prompt_imagen,
            'fecha': datetime.now().strftime('%d/%m %H:%M')
        }
        captions_guardados.insert(0, entrada)
        socketio.emit('caption', entrada)

        stats_global[nombre_marca]['posts'] += 1
        log(f'✅ ¡Post y Prompt generados con éxito para {nombre_marca}! 🚀', 'success')

    except Exception as e:
        log(f'❌ Error ejecutando el ciclo del bot: {e}', 'error')

    socketio.emit('stats', stats_global)

# ============================================
# RUTAS API ALTERADAS
# ============================================

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/api/clientes')
def api_clientes():
    # Retorna la lista formateada para la UI
    lista = [{"nombre": v["nombre"], "nicho": v["detalle_producto"]} for k, v in PRODUCTOS_INFO.items()]
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
    producto = data.get('producto', 'aurakey_software')
    precio = data.get('precio', 'Consultar por interno')

    # Lanzamos el ciclo en un hilo pasando el producto y el precio que pusiste en el celular
    hilo = threading.Thread(target=ciclo_completo, args=(producto, precio))
    hilo.daemon = True
    hilo.start()
    return jsonify({'msg': f'Ciclo iniciado para {producto} a {precio}'})

# ============================================
# SCHEDULER EN HILO SEPARADO
# ============================================
def run_scheduler():
    # El ciclo automático por defecto usa Aurakey sin precio fijo establecido
    schedule.every(3).hours.do(ciclo_completo, id_producto="aurakey_software", precio_manual="Precio Promocional")
    while True:
        schedule.run_pending()
        time.sleep(60)

# ============================================
# INICIO DEL SERVIDOR
# ============================================
if __name__ == '__main__':
    print("🤖 Social Bot Manager - Panel Web")
    print("⏰ Ciclos automáticos activos en segundo plano")

    hilo_scheduler = threading.Thread(target=run_scheduler)
    hilo_scheduler.daemon = True
    hilo_scheduler.start()

    puerto = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=puerto, debug=False, allow_unsafe_werkzeug=True)
