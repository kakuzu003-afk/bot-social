import os
import json
import time
import random
import schedule
import requests
from datetime import datetime
from groq import Groq
from instagrapi import Client

# ============================================
# 1. CONFIGURACIÓN E INICIALIZACIÓN (RAILWAY)
# ============================================
groq_api_key = os.environ.get("GROQ_API_KEY")

if not groq_api_key:
    raise ValueError("❌ ERROR: La variable de entorno GROQ_API_KEY no está configurada.")

# Inicializar el cliente de Groq con la clave de la nube
groq_client = Groq(api_key=groq_api_key)

sesiones = {}

# ============================================
# LECTURA DE COOKIES DESDE ENTORNO
# ============================================
def cargar_cookies_desde_env(nombre_cliente):
    """Busca las cookies en las variables de entorno usando el nombre del cliente"""
    # Para 'Aurakey', buscará exactamente la variable 'COOKIES_AURAKEY'
    env_var = f"COOKIES_{nombre_cliente.upper()}"
    cookies_json = os.environ.get(env_var)
    
    if cookies_json:
        try:
            return json.loads(cookies_json)
        except json.JSONDecodeError:
            print(f"❌ ERROR: El formato JSON de las cookies para {nombre_cliente} es inválido.")
            return None
    return None

# ============================================
# FUNCIONES DE IA
# ============================================

def generar_caption(cliente, tendencia):
    prompt = f"""
    Eres un experto en marketing digital para Instagram.
    Cliente: {cliente['nombre']}
    Nicho: {cliente['nicho']}
    Tono: {cliente['tono']}
    Tendencia actual: {tendencia}
    
    Genera un caption atractivo para Instagram que:
    - Sea en español
    - Tenga máximo 150 palabras
    - Incluya emojis relevantes
    - Termine con un call to action
    - Sea natural, no robótico
    - Se relacione con la tendencia: {tendencia}
    
    Solo responde con el caption, sin explicaciones.
    """
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=300,
        temperature=0.8
    )
    return response.choices[0].message.content

def generar_respuesta_comentario(cliente, comentario, usuario):
    prompt = f"""
    Eres el community manager de {cliente['nombre']}.
    Nicho: {cliente['nicho']}
    Tono: {cliente['tono']}
    
    Un usuario llamado @{usuario} comentó: "{comentario}"
    
    Genera una respuesta corta, amigable y natural en español.
    Máximo 2 oraciones. Incluye 1 emoji relevante.
    Solo responde con el texto de la respuesta.
    """
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=100,
        temperature=0.7
    )
    return response.choices[0].message.content

def buscar_tendencias(cliente):
    prompt = f"""
    Dame 5 tendencias actuales de Instagram para el nicho: {cliente['nicho']}
    
    Formato de respuesta (solo esto, sin explicaciones):
    1. tendencia uno
    2. tendencia dos
    3. tendencia tres
    4. tendencia cuatro
    5. tendencia cinco
    """
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200,
        temperature=0.9
    )
    tendencias = response.choices[0].message.content.strip().split('\n')
    return [t.split('. ', 1)[1] if '. ' in t else t for t in tendencias if t.strip()]

def generar_reporte(cliente, stats):
    prompt = f"""
    Genera un reporte semanal breve para el cliente {cliente['nombre']}.
    
    Estadísticas de la semana:
    - Posts publicados: {stats.get('posts', 0)}
    - Comentarios respondidos: {stats.get('comentarios', 0)}
    - Likes dados: {stats.get('likes', 0)}
    - Interacciones totales: {stats.get('interacciones', 0)}
    
    El reporte debe ser en español, profesional y motivador.
    Máximo 10 líneas. Incluye recomendaciones para la próxima semana.
    """
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=400,
        temperature=0.6
    )
    return response.choices[0].message.content

# ============================================
# LOGIN CON COOKIES (ACTUALIZADO PARA RAILWAY)
# ============================================

def iniciar_sesion_cookies(cliente):
    """Inicia sesión usando cookies exportadas desde las variables de entorno de Railway"""
    nombre = cliente['nombre']

    if nombre in sesiones:
        return sesiones[nombre]

    print(f"🔄 [{nombre}] Intentando cargar cookies desde el entorno...")
    cl = Client()
    cl.delay_range = [2, 5]

    try:
        # Cargar cookies desde variable de entorno
        cookies_data = cargar_cookies_desde_env(nombre)

        if not cookies_data:
            print(f"❌ [{nombre}] No se encontraron cookies configuradas en las variables de entorno.")
            return None

        # Convertir formato Cookie-Editor a formato instagrapi
        cookies_dict = {}
        for cookie in cookies_data:
            cookies_dict[cookie['name']] = cookie['value']

        # Cargar cookies usando el jar de requests directamente
        jar = requests.cookies.RequestsCookieJar()
        for name, value in cookies_dict.items():
            jar.set(name, value, domain='.instagram.com', path='/')
        cl.private.cookies = jar

        # Verificar que la sesión funciona
        try:
            user_info = cl.account_info()
            print(f"✅ [{nombre}] Sesión iniciada como @{user_info.username}")
            sesiones[nombre] = cl
            return cl
        except Exception as e:
            print(f"❌ [{nombre}] Cookies inválidas o expiradas en el entorno: {e}")
            return None

    except Exception as e:
        print(f"❌ [{nombre}] Error con cookies: {e}")
        return None

# ============================================
# FUNCIONES DE INSTAGRAM
# ============================================

def responder_comentarios(cliente):
    nombre = cliente['nombre']
    print(f"\n💬 [{nombre}] Revisando comentarios...")

    cl = iniciar_sesion_cookies(cliente)
    if not cl:
        return 0

    comentarios_respondidos = 0

    try:
        user_id = cl.user_id
        medias = cl.user_medias(user_id, amount=5)

        for media in medias:
            comentarios = cl.media_comments(media.id, amount=10)

            for comentario in comentarios:
                if str(comentario.user.pk) == str(user_id):
                    continue
                if len(comentario.text) < 3:
                    continue

                respuesta = generar_respuesta_comentario(
                    cliente,
                    comentario.text,
                    comentario.user.username
                )

                cl.media_comment(media.id, f"@{comentario.user.username} {respuesta}")
                comentarios_respondidos += 1
                print(f"   ↩️ Respondido a @{comentario.user.username}")
                time.sleep(random.uniform(10, 20))

                if comentarios_respondidos >= 5:
                    break

            if comentarios_respondidos >= 5:
                break

    except Exception as e:
        print(f"   ❌ Error: {e}")

    print(f"   ✅ {comentarios_respondidos} comentarios respondidos")
    return comentarios_respondidos

def dar_likes_nicho(cliente):
    nombre = cliente['nombre']
    print(f"\n❤️ [{nombre}] Dando likes en el nicho...")

    cl = iniciar_sesion_cookies(cliente)
    if not cl:
        return 0

    likes_dados = 0
    hashtag = random.choice(cliente['hashtags']).replace('#', '')

    try:
        medias = cl.hashtag_medias_recent(hashtag, amount=10)

        for media in medias:
            cl.media_like(media.id)
            likes_dados += 1
            print(f"   ❤️ Like en publicación de @{media.user.username}")
            time.sleep(random.uniform(5, 15))

            if likes_dados >= 5:
                break

    except Exception as e:
        print(f"   ❌ Error: {e}")

    print(f"   ✅ {likes_dados} likes dados")
    return likes_dados

def generar_publicacion(cliente):
    nombre = cliente['nombre']
    print(f"\n📸 [{nombre}] Generando caption...")

    try:
        tendencias = buscar_tendencias(cliente)
        tendencia = random.choice(tendencias) if tendencias else cliente['nicho']
        print(f"   🔍 Tendencia: {tendencia}")

        caption = generar_caption(cliente, tendencia)
        hashtags_str = ' '.join(cliente['hashtags'])
        caption_completo = f"{caption}\n\n{hashtags_str}"

        log_publicacion(nombre, caption_completo, tendencia)
        print(f"   ✅ Caption guardado en publicaciones_pendientes.txt")
        return True

    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False

def log_publicacion(nombre, caption, tendencia):
    with open('publicaciones_pendientes.txt', 'a', encoding='utf-8') as f:
        f.write(f"\n{'='*50}\n")
        f.write(f"Cliente: {nombre}\n")
        f.write(f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}\n")
        f.write(f"Tendencia: {tendencia}\n")
        f.write(f"Caption:\n{caption}\n")

# ============================================
# ESTADÍSTICAS Y CONFIGURACIÓN LOCAL
# ============================================

# AQUÍ QUEDA TU CONFIGURACIÓN FIJA INYECTADA DIRECTAMENTE
CONFIG = {
    "clientes": [
        {
            "nombre": "Aurakey",
            "nicho": "Venta de licencias digitales, software, juegos y membresías de streaming",
            "tono": "Profesional, confiable, tecnológico y directo",
            "hashtags": ["#Aurakey", "#LicenciasDigitales", "#StreamingChile", "#SoftwareOriginal", "#GamerChile", "#Productividad"]
        }
    ]
}

stats_semana = {}

def inicializar_stats():
    for cliente in CONFIG['clientes']:
        stats_semana[cliente['nombre']] = {
            'posts': 0,
            'comentarios': 0,
            'likes': 0,
            'interacciones': 0
        }

def reporte_semanal():
    print("\n📊 GENERANDO REPORTES SEMANALES...")
    for cliente in CONFIG['clientes']:
        nombre = cliente['nombre']
        stats = stats_semana.get(nombre, {})
        reporte = generar_reporte(cliente, stats)

        print(f"\n{'='*50}")
        print(f"📊 REPORTE — {nombre}")
        print('='*50)
        print(reporte)

        with open(f"reporte_{nombre}_{datetime.now().strftime('%Y%m%d')}.txt", 'w', encoding='utf-8') as f:
            f.write(reporte)

        stats_semana[nombre] = {'posts': 0, 'comentarios': 0, 'likes': 0, 'interacciones': 0}

# ============================================
# CICLO PRINCIPAL
# ============================================

def ciclo_completo():
    print(f"\n🚀 [{datetime.now().strftime('%H:%M')}] Iniciando ciclo...")

    for cliente in CONFIG['clientes']:
        nombre = cliente['nombre']
        print(f"\n👤 Procesando cliente: {nombre}")

        generar_publicacion(cliente)
        stats_semana[nombre]['posts'] += 1
        time.sleep(random.uniform(5, 10))

        comentarios = responder_comentarios(cliente)
        stats_semana[nombre]['comentarios'] += comentarios
        time.sleep(random.uniform(5, 10))

        likes = dar_likes_nicho(cliente)
        stats_semana[nombre]['likes'] += likes
        stats_semana[nombre]['interacciones'] += likes + comentarios
        time.sleep(random.uniform(10, 20))

    print(f"\n✅ Ciclo completado")

# ============================================
# INICIO
# ============================================

print("🤖 Bot Social Manager iniciando...")
print(f"📋 Clientes: {len(CONFIG['clientes'])}")

inicializar_stats()

for c in CONFIG['clientes']:
    print(f"    ✅ {c['nombre']}")

print("\n⏰ Programando tareas cada 3 horas...")
schedule.every(3).hours.do(ciclo_completo)
schedule.every().monday.at("08:00").do(reporte_semanal)

print("✅ Bot activo. Presiona Ctrl+C para detener.")
print("📁 Captions guardados en: publicaciones_pendientes.txt\n")

ciclo_completo()

while True:
    schedule.run_pending()
    time.sleep(60)