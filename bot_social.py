from flask import Flask, render_template_string, jsonify, request
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
# CARGAR CONFIGURACIÓN
# ============================================
with open('config.json', 'r', encoding='utf-8') as f:
    CONFIG = json.load(f)

groq_client = Groq(api_key=CONFIG['groq_api_key'])
sesiones = {}
stats_global = {}
logs_global = []
bot_activo = False

for c in CONFIG['clientes']:
    stats_global[c['nombre']] = {
        'posts': 0,
        'comentarios': 0,
        'likes': 0,
        'interacciones': 0,
        'ultimo_ciclo': 'Nunca'
    }

# ============================================
# HTML DEL PANEL
# ============================================

HTML = '''
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Social Bot Manager</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f0f13; color: #e0e0e0; min-height: 100vh; }
        
        .header { background: linear-gradient(135deg, #1a1a2e, #16213e); padding: 20px 30px; border-bottom: 1px solid #2a2a3e; display: flex; align-items: center; justify-content: space-between; }
        .header h1 { font-size: 22px; font-weight: 600; color: #fff; }
        .header h1 span { color: #7c6aff; }
        .status-dot { width: 10px; height: 10px; border-radius: 50%; background: #22c55e; display: inline-block; margin-right: 8px; animation: pulse 2s infinite; }
        .status-dot.off { background: #ef4444; animation: none; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
        
        .container { padding: 24px 30px; max-width: 1200px; margin: 0 auto; }
        
        .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 24px; }
        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
        
        .card { background: #1a1a2e; border: 1px solid #2a2a3e; border-radius: 12px; padding: 20px; }
        .card-title { font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 8px; }
        .card-value { font-size: 28px; font-weight: 700; color: #fff; }
        .card-sub { font-size: 12px; color: #666; margin-top: 4px; }
        
        .stat-card { border-left: 3px solid #7c6aff; }
        .stat-card.green { border-left-color: #22c55e; }
        .stat-card.orange { border-left-color: #f59e0b; }
        .stat-card.pink { border-left-color: #ec4899; }
        
        .section-title { font-size: 14px; font-weight: 600; color: #fff; margin-bottom: 14px; }
        
        .btn { padding: 10px 20px; border-radius: 8px; border: none; font-size: 13px; font-weight: 500; cursor: pointer; transition: all 0.2s; }
        .btn-primary { background: #7c6aff; color: #fff; }
        .btn-primary:hover { background: #6b59ee; }
        .btn-danger { background: #ef4444; color: #fff; }
        .btn-danger:hover { background: #dc2626; }
        .btn-success { background: #22c55e; color: #fff; }
        .btn-success:hover { background: #16a34a; }
        .btn-sm { padding: 6px 14px; font-size: 12px; }
        
        .controls { display: flex; gap: 10px; align-items: center; margin-bottom: 24px; flex-wrap: wrap; }
        
        .log-box { background: #0a0a0f; border: 1px solid #2a2a3e; border-radius: 8px; padding: 16px; height: 200px; overflow-y: auto; font-family: monospace; font-size: 12px; }
        .log-line { padding: 2px 0; color: #aaa; }
        .log-line.success { color: #22c55e; }
        .log-line.error { color: #ef4444; }
        .log-line.info { color: #7c6aff; }
        .log-line.warning { color: #f59e0b; }
        
        .caption-list { display: flex; flex-direction: column; gap: 12px; max-height: 400px; overflow-y: auto; }
        .caption-card { background: #0f0f1a; border: 1px solid #2a2a3e; border-radius: 8px; padding: 14px; }
        .caption-meta { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
        .caption-cliente { font-size: 11px; font-weight: 600; color: #7c6aff; text-transform: uppercase; }
        .caption-fecha { font-size: 11px; color: #666; }
        .caption-text { font-size: 13px; color: #ccc; line-height: 1.5; white-space: pre-wrap; }
        .caption-actions { display: flex; gap: 8px; margin-top: 10px; }
        
        .badge { display: inline-block; padding: 2px 8px; border-radius: 99px; font-size: 11px; }
        .badge-purple { background: rgba(124,106,255,0.2); color: #7c6aff; }
        .badge-green { background: rgba(34,197,94,0.2); color: #22c55e; }
        
        .client-row { display: flex; align-items: center; justify-content: space-between; padding: 12px 0; border-bottom: 1px solid #2a2a3e; }
        .client-row:last-child { border-bottom: none; }
        .client-name { font-size: 14px; font-weight: 500; }
        .client-handle { font-size: 12px; color: #666; }

        textarea { background: #0f0f1a; border: 1px solid #2a2a3e; border-radius: 8px; color: #e0e0e0; padding: 10px; font-size: 13px; width: 100%; resize: vertical; font-family: inherit; }
        textarea:focus { outline: none; border-color: #7c6aff; }

        .empty-state { text-align: center; padding: 40px; color: #555; font-size: 13px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🤖 Social Bot <span>Manager</span></h1>
        <div style="display:flex;align-items:center;gap:16px;">
            <span><span class="status-dot" id="statusDot"></span><span id="statusText" style="font-size:13px;">Activo</span></span>
            <span style="font-size:12px;color:#666;" id="reloj"></span>
        </div>
    </div>

    <div class="container">

        <!-- Stats -->
        <div class="grid-3" id="statsGrid">
            <div class="card stat-card">
                <div class="card-title">Posts generados</div>
                <div class="card-value" id="statPosts">0</div>
                <div class="card-sub">Esta semana</div>
            </div>
            <div class="card stat-card green">
                <div class="card-title">Comentarios respondidos</div>
                <div class="card-value" id="statComentarios">0</div>
                <div class="card-sub">Esta semana</div>
            </div>
            <div class="card stat-card orange">
                <div class="card-title">Likes dados</div>
                <div class="card-value" id="statLikes">0</div>
                <div class="card-sub">Esta semana</div>
            </div>
        </div>

        <!-- Controles -->
        <div class="controls">
            <button class="btn btn-success" onclick="ejecutarCiclo()">▶ Ejecutar ciclo ahora</button>
            <button class="btn btn-primary" onclick="generarCaption()">✍️ Generar caption</button>
            <button class="btn btn-danger btn-sm" onclick="limpiarLogs()">🗑 Limpiar logs</button>
        </div>

        <div class="grid-2">
            <!-- Logs -->
            <div class="card">
                <div class="section-title">📋 Logs en tiempo real</div>
                <div class="log-box" id="logBox">
                    <div class="log-line info">Panel iniciado correctamente ✅</div>
                </div>
            </div>

            <!-- Clientes -->
            <div class="card">
                <div class="section-title">👥 Clientes activos</div>
                <div id="clientesList"></div>
            </div>
        </div>

        <!-- Captions generados -->
        <div class="card">
            <div class="section-title">✍️ Captions generados <span class="badge badge-purple" id="captionCount">0</span></div>
            <div class="caption-list" id="captionList">
                <div class="empty-state">No hay captions generados aún.<br>Ejecuta un ciclo para comenzar.</div>
            </div>
        </div>

    </div>

    <script>
        const socket = io();
        let captions = [];

        // Reloj
        setInterval(() => {
            document.getElementById('reloj').textContent = new Date().toLocaleTimeString('es-CL');
        }, 1000);

        // Socket eventos
        socket.on('log', (data) => agregarLog(data.msg, data.tipo));
        socket.on('stats', (data) => actualizarStats(data));
        socket.on('caption', (data) => agregarCaption(data));
        socket.on('bot_status', (data) => {
            const dot = document.getElementById('statusDot');
            const txt = document.getElementById('statusText');
            if (data.activo) {
                dot.classList.remove('off');
                txt.textContent = 'Activo';
            } else {
                dot.classList.add('off');
                txt.textContent = 'Detenido';
            }
        });

        function agregarLog(msg, tipo = 'info') {
            const box = document.getElementById('logBox');
            const line = document.createElement('div');
            line.className = `log-line ${tipo}`;
            const hora = new Date().toLocaleTimeString('es-CL');
            line.textContent = `[${hora}] ${msg}`;
            box.appendChild(line);
            box.scrollTop = box.scrollHeight;
        }

        function actualizarStats(data) {
            let posts = 0, comentarios = 0, likes = 0;
            for (const cliente in data) {
                posts += data[cliente].posts || 0;
                comentarios += data[cliente].comentarios || 0;
                likes += data[cliente].likes || 0;
            }
            document.getElementById('statPosts').textContent = posts;
            document.getElementById('statComentarios').textContent = comentarios;
            document.getElementById('statLikes').textContent = likes;
        }

        function agregarCaption(data) {
            captions.unshift(data);
            renderCaptions();
        }

        function renderCaptions() {
            const list = document.getElementById('captionList');
            document.getElementById('captionCount').textContent = captions.length;
            if (captions.length === 0) {
                list.innerHTML = '<div class="empty-state">No hay captions generados aún.</div>';
                return;
            }
            list.innerHTML = captions.map((c, i) => `
                <div class="caption-card">
                    <div class="caption-meta">
                        <span class="caption-cliente">${c.cliente}</span>
                        <span class="caption-fecha">${c.fecha}</span>
                    </div>
                    <div style="font-size:11px;color:#f59e0b;margin-bottom:6px;">🔍 ${c.tendencia}</div>
                    <div style="font-size:11px;color:#888;margin-bottom:4px;">📝 Caption:</div>
                    <textarea rows="4" id="caption_${i}">${c.caption}</textarea>
                    <div style="font-size:11px;color:#888;margin:8px 0 4px;">🎨 Prompt para imagen (Kling / Midjourney / DALL-E):</div>
                    <textarea rows="3" id="prompt_${i}" style="border-color:#7c6aff33;">${c.prompt_imagen || 'No generado aún'}</textarea>
                    <div class="caption-actions">
                        <button class="btn btn-primary btn-sm" onclick="copiarCaption(${i})">📋 Copiar caption</button>
                        <button class="btn btn-sm" style="background:#7c6aff;color:#fff;" onclick="copiarPrompt(${i})">🎨 Copiar prompt imagen</button>
                    </div>
                </div>
            `).join('');
        }

        function copiarCaption(i) {
            const ta = document.getElementById(`caption_${i}`);
            navigator.clipboard.writeText(ta.value);
            agregarLog('Caption copiado al portapapeles ✅', 'success');
        }

        function copiarPrompt(i) {
            const ta = document.getElementById(`prompt_${i}`);
            navigator.clipboard.writeText(ta.value);
            agregarLog('Prompt de imagen copiado ✅ Pégalo en Kling, Midjourney o DALL-E', 'success');
        }

        function limpiarLogs() {
            document.getElementById('logBox').innerHTML = '';
        }

        function ejecutarCiclo() {
            agregarLog('Ejecutando ciclo manualmente...', 'info');
            fetch('/api/ciclo', { method: 'POST' })
                .then(r => r.json())
                .then(d => agregarLog(d.msg, 'success'));
        }

        function generarCaption() {
            agregarLog('Generando caption...', 'info');
            fetch('/api/caption', { method: 'POST' })
                .then(r => r.json())
                .then(d => agregarLog(d.msg, 'success'));
        }

        // Cargar clientes
        fetch('/api/clientes')
            .then(r => r.json())
            .then(data => {
                const list = document.getElementById('clientesList');
                list.innerHTML = data.map(c => `
                    <div class="client-row">
                        <div>
                            <div class="client-name">${c.nombre}</div>
                            <div class="client-handle">${c.nicho.substring(0, 40)}...</div>
                        </div>
                        <span class="badge badge-green">Activo</span>
                    </div>
                `).join('');
            });

        // Cargar stats iniciales
        fetch('/api/stats')
            .then(r => r.json())
            .then(data => actualizarStats(data));

        // Cargar captions guardados
        fetch('/api/captions')
            .then(r => r.json())
            .then(data => {
                captions = data;
                renderCaptions();
            });
    </script>
</body>
</html>
'''

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
    return render_template_string(HTML)

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
    print("🌐 Abre tu navegador en: http://localhost:5000")
    print("⏰ Ciclos automáticos cada 3 horas")
    print("Presiona Ctrl+C para detener\n")

    hilo_scheduler = threading.Thread(target=run_scheduler)
    hilo_scheduler.daemon = True
    hilo_scheduler.start()

    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
