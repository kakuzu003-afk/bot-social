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

PRODUCTOS_INFO = {
    "aurakey_autocad": {
        "nombre": "Aurakey",
        "detalle_producto": "Licencia Original de AutoCAD (Ideal para arquitectos, ingenieros y diseñadores)",
        "keyword_busqueda": "autocad architecture design engineering",
        "nicho": "diseño e ingeniería, planos, arquitectura, software AutoCAD profesional",
        "tono": "profesional, directo, de alto valor, confiable y vendedor"
    },
    "aurakey_adobe": {
        "nombre": "Aurakey",
        "detalle_producto": "Suscripción Original a Adobe Creative Cloud (Acceso a Photoshop, Illustrator, Premiere Pro, etc.)",
        "keyword_busqueda": "adobe photoshop premiere video editing graphic design",
        "nicho": "diseño gráfico, edición de video, fotografía, creadores de contenido, herramientas digitales de Adobe",
        "tono": "creativo, enérgico, moderno, disruptivo y vendedor"
    },
    "aurakey_windows": {
        "nombre": "Aurakey",
        "detalle_producto": "Licencia Original de Windows 10 / Windows 11 Pro",
        "keyword_busqueda": "windows 11 pro pc gaming computer optimization",
        "nicho": "sistemas operativos, optimización de PC, seguridad informática, computación y rendimiento",
        "tono": "técnico pero accesible, confiable, seguro y directo"
    },
    "aurakey_office": {
        "nombre": "Aurakey",
        "detalle_producto": "Licencia Original de Microsoft Office Professional Plus (Word, Excel, PowerPoint)",
        "keyword_busqueda": "microsoft office excel productivity remote work",
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

def buscar_hashtags_y_tendencias_reales(prod_info):
    """
    Esta función se conecta en tiempo real a internet para buscar qué tópicos
    y palabras clave están calientes en este segundo sobre el producto elegido.
    """
    keyword = prod_info["keyword_busqueda"]
    log(f"🌐 Escaneando la red en busca de tendencias para: '{keyword}'...", "info")
    
    contexto_internet = ""
    try:
        # Hacemos una consulta en vivo usando un motor de búsqueda público y rápido
        url_busqueda = f"https://html.duckduckgo.com/html/?q={keyword}+trends+2026"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        res = req.get(url_busqueda, headers=headers, timeout=8)
        
        if res.status_code == 200 and "No results" not in res.text:
            # Extraemos texto de los primeros resultados para que la IA vea qué es tendencia hoy
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(res.text, "html.parser")
            resultados = [a.get_text() for a in soup.find_all("a", class_="result__snippet")[:4]]
            contexto_internet = " ".join(resultados)
            log("🔥 Datos de tendencias de última hora recopilados con éxito.", "success")
        else:
            log("⚠️ No se pudieron extraer datos frescos directos, usando base de ganchos rápidos.", "warning")
    except Exception as e:
        log(f"⚠️ Error en scraping de tendencias ({e}). Usando ganchos dinámicos de respaldo.", "warning")

    # Le pasamos los datos reales extraídos a Groq para que nos dé los hashtags del momento exacto
    prompt = f"""
    Analiza este reporte de tendencias actuales de internet sobre el nicho del producto:
    ---
    {contexto_internet if contexto_internet else 'Herramientas digitales, automatización, trabajo remoto eficiente y optimización de flujos creativos en 2026.'}
    ---
    
    En base a esos datos reales y al nicho '{prod_info['nicho']}', genera una lista de 5 ganchos o temáticas del momento que estén siendo ultra virales en Instagram hoy en día, junto con una sugerencia de 8 hashtags que estén rompiendo el algoritmo en este segundo para este producto específico.
    
    Responde estrictamente en este formato:
    GANCHOS: [Escribe aquí las 5 ideas separadas por comas]
    HASHTAGS_DEL_MOMENTO: [Escribe aquí los 8 hashtags sugeridos de alta tendencia]
    """
    
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=250,
        temperature=0.7
    )
    
    contenido = response.choices[0].message.content.strip()
    return contenido

def generar_caption_y_hashtags_pro(prod_info, info_tendencias, precio):
    prompt = f"""
    Eres un estratega experto de Instagram especializado en viralidad y embudos de ventas.
    Marca: {prod_info['nombre']}
    Producto a reventar en ventas hoy: {prod_info['detalle_producto']}
    Tono de la cuenta: {prod_info['tono']}
    PRECIO DE OFERTA HOY: {precio}
    
    INFORMACIÓN DE ÚLTIMA HORA (Extraída en tiempo real de internet):
    {info_tendencias}
    
    Tu misión es redactar el post perfecto para Instagram usando los datos reales de arriba:
    
    1. CAPTION: Redacta un copy demoledor y persuasivo en español chileno neutro (máximo 140 palabras). Integra el precio ({precio}) de una forma tan atractiva que parezca una oportunidad imperdible de último minuto. Usa emojis modernos y termina con un Call to Action (CTA) claro empujando al usuario a enviar un DM para comprar.
    
    2. HASHTAGS DEL MOMENTO: Pon un bloque de hashtags mezclando los extraídos en la búsqueda en tiempo real junto con ganchos virales de emprendimiento, productividad y geolocalización (#chile, #santiago). Tienen que ser los mejores para posicionar el post en la sección "Explorar" HOY.
    
    No agregues introducciones, textos de relleno ni notas del sistema. Entrega directo el post listo.
    """
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=450,
        temperature=0.8
    )
    return response.choices[0].message.content

def generar_prompt_imagen(prod_info, caption):
    prompt = f"""
    Eres un director de arte digital experto en prompts para Midjourney, Kling y DALL-E 3.
    Analiza este caption de ventas que acabamos de crear:
    ---
    {caption[:200]}
    ---
    Genera un prompt hiper detallado en inglés para crear una imagen fotorrealista y de altísimo impacto comercial para Instagram.
    Estilo obligatorio: cinematic style, 9:16 vertical format, hyper-realistic texture, professional studio clean lighting, modern tech setup aesthetic, trendy Instagram visual layout. Max 90 words. No explanations.
    """
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200,
        temperature=0.75
    )
    return response.choices[0].message.content

captions_guardados = []

def ciclo_completo(id_producto="aurakey_autocad", precio_manual="No especificado"):
    global bot_activo
    bot_activo = True
    socketio.emit('bot_status', {'activo': True})
    log(f'🚀 Iniciando ciclo dinámico en vivo para: {id_producto}...', 'info')

    if id_producto not in PRODUCTOS_INFO:
        log(f'❌ Error: El producto "{id_producto}" no está configurado.', 'error')
        return

    prod_info = PRODUCTOS_INFO[id_producto]
    nombre_marca = prod_info['nombre']

    try:
        # PASO 1: Buscar las tendencias reales haciendo Web Scraping instantáneo
        info_tendencias = buscar_hashtags_y_tendencias_reales(prod_info)
        
        log(f'✍️ Redactando post con hashtags calientes rastreados para el precio de {precio_manual}...', 'info')
        # PASO 2: Inyectar esos datos frescos en el copy final
        caption_completo = generar_caption_y_hashtags_pro(prod_info, info_tendencias, precio_manual)

        log(f'🎨 Diseñando prompt visual optimizado para la tendencia capturada...', 'info')
        prompt_imagen = generar_prompt_imagen(prod_info, caption_completo)

        entrada = {
            'cliente': f"{nombre_marca} - {id_producto.split('_')[1].upper()}",
            'tendencia': "Tendencias rastreadas en vivo 🌐🔥",
            'caption': caption_completo,
            'prompt_imagen': prompt_imagen,
            'fecha': datetime.now().strftime('%d/%m %H:%M')
        }
        captions_guardados.insert(0, entrada)
        socketio.emit('caption', entrada)

        stats_global[nombre_marca]['posts'] += 1
        log(f'✅ ¡Post con Tendencias y Hashtags en Tiempo Real generado para {id_producto}! 🔥', 'success')

    except Exception as e:
        log(f'❌ Error ejecutando el ciclo dinámico: {e}', 'error')

    socketio.emit('stats', stats_global)

# ============================================
# RUTAS API
# ============================================

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/api/clientes')
def api_clientes():
    # Dejamos un único cliente activo real en la interfaz visual
    lista = [{
        "nombre": "Aurakey", 
    }]
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
    schedule.every(3).hours.do(ciclo_completo, id_producto="aurakey_autocad", precio_manual="Precio Promo")
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == '__main__':
    print("🤖 Social Bot Manager - Modo Tendencias en Vivo Activado")
    
    hilo_scheduler = threading.Thread(target=run_scheduler)
    hilo_scheduler.daemon = True
    hilo_scheduler.start()

    puerto = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=puerto, debug=False, allow_unsafe_werkzeug=True)
