import os
import json
from agentes_creativos import SuiteCreativaMultiAgente

# Simulación de la integración en el bot principal
def demo_integracion():
    # 1. Configurar la suite (se asume que la API KEY está en el entorno para el usuario final)
    # Para esta demo, si no hay API KEY, mostraremos un mensaje informativo.
    if not os.environ.get("GROQ_API_KEY"):
        print("⚠️ Nota: GROQ_API_KEY no detectada en el entorno de ejecución actual.")
        print("Este script está diseñado para integrarse en tu servidor (Railway/Heroku) donde la clave ya está configurada.")
        print("A continuación, se muestra cómo se llamaría a la nueva Suite de Agentes en tu código principal:\n")
        
        codigo_demo = """
# En bot_social.py:
from agentes_creativos import SuiteCreativaMultiAgente

suite = SuiteCreativaMultiAgente()

@app.route('/generar_post', methods=['POST'])
def api_generar_post():
    prod_info = request.json.get('producto')
    tendencias = obtener_tendencias_actuales()
    
    # El nuevo flujo multi-agente reemplaza la función monolítica anterior
    resultado = suite.generar_post_completo(prod_info, tendencias)
    
    return jsonify({
        "caption": resultado["caption"],
        "hashtags": resultado["hashtags"],
        "estrategia": resultado["estrategia"]
    })
        """
        print(codigo_demo)
        return

    # Si hay API KEY, ejecutamos una prueba real
    suite = SuiteCreativaMultiAgente()
    
    producto = {
        "nombre": "Suscripción Disney+ Premium 1 Mes",
        "beneficio": "Todo el contenido de Disney, Pixar, Marvel, Star Wars y National Geographic en 4K",
        "audiencia": "Familias y fans de sagas épicas en Chile",
        "precio": "$4.500",
        "categoria": "streaming"
    }
    
    tendencias = "Estreno de nueva temporada de The Mandalorian, Vacaciones de invierno en Chile"
    
    print("--- INICIANDO GENERACIÓN MULTI-AGENTE ---\n")
    resultado = suite.generar_post_completo(producto, tendencias)
    
    print("\n--- ESTRATEGIA DEL DIRECTOR CREATIVO ---")
    print(json.dumps(resultado["estrategia"], indent=2, ensure_ascii=False))
    
    print("\n--- CAPTION DEL COPYWRITER CHILENO ---")
    print(resultado["caption"])
    
    print("\n--- OPTIMIZACIÓN SEO (HASHTAGS) ---")
    print(resultado["hashtags"])

if __name__ == "__main__":
    demo_integracion()
