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

CONFIG = {
    "clientes": [
        {
            "nombre": "Aurakey",
            "nicho": "licencias de software, AutoCAD, Adobe, tecnología",
            "hashtags": ["#software", "#autocad", "#adobe", "#tecnologia", "#diseño"],
            "tono": "profesional y confiable"
        }
    ]
}

for c in CONFIG['clientes']:
    stats_global[c['nombre']] = {
        'posts': 0,
        'comentarios': 0,
        'likes': 0,
        'interacciones': 0,
        'ultimo_ciclo': 'Nunca'
    }

# ============================================
# FUNCIONES DE IA
# ============================================

def log(msg, tipo='info'):
    entrada = {'msg': msg, 'tipo': tipo, 'hora': datetime.now().strftime('%H:%M:%S')}
    logs_global.append(entrada)
    if len(logs_global) > 100:
        logs_global.pop(0)
    socketio.emit('log', entrada)
    print(f"[{tipo.upper()}] {msg}")

def generar_caption(cliente, tendencia):
    prompt = f"""
    Eres un experto en marketing digital para Instagram.
    Cliente: {cliente['nombre']}
    Nicho: {cliente['nicho']}
    Tono: {cliente['tono']}
    Tendencia: {tendencia}
    
    Genera un caption atractivo para Instagram en español,
    máximo 150 palabras, con emojis y call to action.
    Solo responde con el caption.
    """
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=300,
        temperature=0.8
    )
    return response.choices[0].message.content

def generar_prompt_imagen(cliente, tendencia, caption):
    prompt = f"""
    Eres un experto en generar prompts para IA de imágenes como Midjourney, Kling o DALL-E.
    
    Cliente: {cliente['nombre']}
    Nicho: {cliente['nicho']}
    Tendencia: {tendencia}
    Caption de Instagram: {caption[:200]}
    
    Genera un prompt en inglés para crear una imagen fotorrealista y atractiva para Instagram.
    El prompt debe:
    - Ser en inglés
    - Describir la escena visual ideal para acompañar el caption
    - Incluir estilo: cinematic, 9:16 vertical, high quality, Instagram aesthetic
    - Máximo 100 palabras
    - Solo responde con el prompt, sin explicaciones
    """
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200,
        temperature=0.8
    )
    return response.choices[0].message.content

def buscar_tendencias(cliente):
    prompt = f"""
    Dame 5 tendencias de Instagram para: {cliente['nicho']}
    Formato:
    1. tendencia
    2. tendencia
    3. tendencia
    4. tendencia
    5. tendencia
    Solo las tendencias, sin explicación.
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

def ciclo_completo():
    global bot_activo
    bot_activo = True
    socketio.emit('bot_status', {'activo': True})
    log('🚀 Iniciando ciclo completo...', 'info')

    for cliente in CONFIG['clientes']:
        nombre = cliente['nombre']
        log(f'👤 Procesando {nombre}...', 'info')

        try:
            tendencias = buscar_tendencias(cliente)
            tendencia = random.choice(tendencias)
            log(f'🔍 Tendencia: {tendencia}', 'info')

            caption = generar_caption(cliente, tendencia)
            hashtags = ' '.join(cliente['hashtags'])
            caption_completo = f"{caption}\n\n{hashtags}"

            log(f'🎨 Generando prompt de imagen...', 'info')
            prompt_imagen = generar_prompt_imagen(cliente, tendencia, caption)

            entrada = {
                'cliente': nombre,
                'tendencia': tendencia,
                'caption': caption_completo,
                'prompt_imagen': prompt_imagen,
                'fecha': datetime.now().strftime('%d/%m %H:%M')
            }
            captions_guardados.insert(0, entrada)
            socketio.emit('caption', entrada)

            stats_global[nombre]['posts'] += 1
            log(f'✅ Caption + prompt de imagen generado para {nombre}', 'success')

        except Exception as e:
            log(f'❌ Error en {nombre}: {e}', 'error')

        time.sleep(2)

    socketio.emit('stats', stats_global)
    log('✅ Ciclo completado', 'success')

# ============================================
# RUTAS API
# ============================================

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/api/clientes')
def api_clientes():
    return jsonify(CONFIG['clientes'])

@app.route('/api/stats')
def api_stats():
    return jsonify(stats_global)

@app.route('/api/captions')
def api_captions():
    return jsonify(captions_guardados)

@app.route('/api/ciclo', methods=['POST'])
def api_ciclo():
    hilo = threading.Thread(target=ciclo_completo)
    hilo.daemon = True
    hilo.start()
    return jsonify({'msg': 'Ciclo iniciado correctamente'})

@app.route('/api/caption', methods=['POST'])
def api_caption():
    def generar():
        for cliente in CONFIG['clientes']:
            try:
                tendencias = buscar_tendencias(cliente)
                tendencia = random.choice(tendencias)
                caption = generar_caption(cliente, tendencia)
                hashtags = ' '.join(cliente['hashtags'])
                entrada = {
                    'cliente': cliente['nombre'],
                    'tendencia': tendencia,
                    'caption': f"{caption}\n\n{hashtags}",
                    'fecha': datetime.now().strftime('%d/%m %H:%M')
                }
                captions_guardados.insert(0, entrada)
                socketio.emit('caption', entrada)
                log(f"✅ Caption generado para {cliente['nombre']}", 'success')
            except Exception as e:
                log(f"❌ Error: {e}", 'error')

    hilo = threading.Thread(target=generar)
    hilo.daemon = True
    hilo.start()
    return jsonify({'msg': 'Generando captions...'})

# ============================================
# SCHEDULER EN HILO SEPARADO
# ============================================

def run_scheduler():
    schedule.every(3).hours.do(ciclo_completo)
    while True:
        schedule.run_pending()
        time.sleep(60)

# ============================================
# INICIO
# ============================================

if __name__ == '__main__':
    print("🤖 Social Bot Manager - Panel Web")
    print("⏰ Ciclos automáticos cada 3 horas")

    hilo_scheduler = threading.Thread(target=run_scheduler)
    hilo_scheduler.daemon = True
    hilo_scheduler.start()

    puerto = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=puerto, debug=False, allow_unsafe_werkzeug=True)