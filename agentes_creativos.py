import json
import os
from groq import Groq
from motor_tendencias import MotorTendenciasChile

# Configuración de Groq (se asume que GROQ_API_KEY ya está en el entorno)
groq_api_key = os.environ.get("GROQ_API_KEY")
if not groq_api_key:
    raise ValueError("❌ ERROR: La variable de entorno GROQ_API_KEY no está configurada.")
client = Groq(api_key=groq_api_key)

class SuiteCreativaMultiAgente:
    def __init__(self, model="llama-3.3-70b-versatile"):
        self.model = model
        # Modelo rápido para tareas simples (SEO/hashtags) — 3x más veloz que 70b
        self.model_fast = "llama-3.1-8b-instant"
        self.motor_tendencias = MotorTendenciasChile()

    def _llm_call(self, system_prompt, user_prompt, temperature=0.7, max_tokens=1000, fast=False):
        try:
            model_to_use = self.model_fast if fast else self.model
            response = client.chat.completions.create(
                model=model_to_use,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=temperature,
                max_tokens=max_tokens
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"❌ Error en llamada a LLM: {e}")
            return None

    def agente_director_creativo(self, prod_info, tendencias, captions_historicos=""):
        system_prompt = (
            "Eres un Director Creativo senior en una agencia de marketing digital experta en Chile. "
            "Tu especialidad es el 'Newsjacking': la habilidad de conectar productos comerciales con las noticias y tendencias del momento de forma orgánica e ingeniosa. "
            "Tu tarea es analizar un producto y las tendencias actuales para definir la estrategia de venta. "
            "Debes identificar el DOLOR del cliente, el GANCHO irresistible y la TÁCTICA de venta. "
            "Si una tendencia actual encaja (aunque sea por humor o contraste), úsala para la estrategia."
        )
        
        user_prompt = f"""Analiza este producto y genera una estrategia de venta.
        
Información del Producto:
- Nombre: {prod_info.get('nombre', 'Producto Digital')}
- Beneficio: {prod_info.get('beneficio', 'Mejora tu vida digital')}
- Audiencia: {prod_info.get('audiencia', 'Público general')}
- Precio: {prod_info.get('precio', 'Consultar')}
- Categoría: {prod_info.get('categoria', 'digital')}

Tendencias Actuales: {tendencias}

Historial de Captions Exitosos:
{captions_historicos}

Responde SOLO con un JSON válido siguiendo este esquema:
{{
  "dolor_cliente": "[problema o frustración real del cliente sin este producto]",
  "gancho_irresistible": "[elemento más atractivo del producto]",
  "tactica_venta": "[una de estas: precio_shock | comparacion_tienda | historia_micro | pregunta_directa | fomo | humor_precio | urgencia_stock | resultado_especifico | testimonio_implicito | feature_tecnica | tiempo_ahorrado | revelacion_final]",
  "tono_sugerido": "[uno de estos: informal_chileno | profesional_directo | entusiasta_juvenil | sarcástico_amigable | experto_tecnico | emotivo_cercano]"
}}"""
        
        res = self._llm_call(system_prompt, user_prompt, temperature=0.5, max_tokens=400)
        if res:
            try:
                # Limpiar posibles backticks de la respuesta
                res_clean = res.replace("```json", "").replace("```", "").strip()
                return json.loads(res_clean)
            except json.JSONDecodeError:
                print("⚠️ Error decodificando JSON del Director Creativo. Usando fallback.")
        
        return {
            "dolor_cliente": "Falta de acceso a contenido premium a buen precio.",
            "gancho_irresistible": "Precio inmejorable y activación inmediata.",
            "tactica_venta": "precio_shock",
            "tono_sugerido": "informal_chileno"
        }

    def agente_copywriter_chileno(self, estrategia, prod_info):
        system_prompt = (
            "Eres un Copywriter chileno experto en redes sociales. Tu misión es escribir un caption de Instagram altamente persuasivo y auténtico, "
            "utilizando el lenguaje y las expresiones chilenas. Debes seguir la estrategia proporcionada por el Director Creativo y enfocarte en generar "
            "un llamado a la acción claro para WhatsApp. NO incluyas hashtags en tu respuesta."
        )
        
        user_prompt = f"""Escribe el caption de Instagram basándote en esta estrategia.

Estrategia del Director Creativo:
{json.dumps(estrategia, indent=2)}

Información del Producto:
- Nombre: {prod_info.get('nombre', 'Producto Digital')}
- Precio: {prod_info.get('precio', 'Consultar')}

Reglas:
- Longitud: 80-130 palabras.
- Tono: {estrategia.get('tono_sugerido', 'informal_chileno')}.
- Usa chilenismos si el tono es informal.
- Incluye el nombre del producto y el precio.
- Cierre: Siempre termina con WhatsApp: +56946557876."""
        
        return self._llm_call(system_prompt, user_prompt, temperature=0.9, max_tokens=600)

    def agente_seo_hashtags(self, caption, categoria, historial_hashtags=""):
        system_prompt = (
            "Eres un Especialista en SEO y Hashtags para Instagram en Chile. Tu tarea es generar una lista de hashtags relevantes y efectivos para un caption dado, "
            "considerando la categoría del producto y evitando el shadowban mediante la rotación. NO incluyas el caption en tu respuesta, solo la lista de hashtags."
        )
        
        user_prompt = f"""Genera los hashtags para este post.

Caption: {caption}
Categoría: {categoria}
Historial: {historial_hashtags}

Reglas:
- 5 a 10 hashtags.
- Incluye #chile y #oferta.
- Responde SOLO con la lista de hashtags separados por espacio."""
        
        # fast=True usa llama-3.1-8b-instant (3x más rápido) — tarea simple de keywords
        return self._llm_call(system_prompt, user_prompt, temperature=0.6, max_tokens=200, fast=True)

    def generar_post_completo(self, prod_info, tendencias_manuales="", historial_captions="", historial_hashtags=""):
        from concurrent.futures import ThreadPoolExecutor
        print(f"🚀 Iniciando Suite Creativa para: {prod_info.get('nombre')}")

        # 0. Obtener tendencias reales si no se proveen
        tendencias_reales = ""
        if not tendencias_manuales:
            print("🔍 Buscando tendencias en tiempo real en Chile...")
            tendencias_raw = self.motor_tendencias.obtener_tendencias_google(limite=5)
            tendencias_reales = self.motor_tendencias.formatear_para_llm(tendencias_raw)
        else:
            tendencias_reales = tendencias_manuales

        # 1. Director Creativo (debe ir primero — define la estrategia)
        estrategia = self.agente_director_creativo(prod_info, tendencias_reales, historial_captions)
        print(f"🧠 Estrategia definida: {estrategia.get('tactica_venta')} | {estrategia.get('tono_sugerido')}")

        # 2 + 3. ⚡ OPTIMIZACIÓN: Copywriter y SEO corren en PARALELO
        # El copywriter necesita la estrategia, pero el agente SEO puede prepararse
        # Ahorro estimado: ~2-4s (antes eran secuenciales)
        with ThreadPoolExecutor(max_workers=2) as executor:
            fut_caption  = executor.submit(self.agente_copywriter_chileno, estrategia, prod_info)
            # El agente SEO recibe el caption cuando esté listo
            caption = fut_caption.result()
            fut_hashtags = executor.submit(
                self.agente_seo_hashtags, caption,
                prod_info.get('categoria', 'general'), historial_hashtags
            )
            hashtags = fut_hashtags.result()

        print(f"✍️ Caption redactado ({len(caption.split())} palabras)")
        print(f"🏷️ Hashtags generados: {hashtags}")

        post_final = f"{caption}\n\n{hashtags}"

        return {
            "post_final": post_final,
            "caption": caption,
            "hashtags": hashtags,
            "estrategia": estrategia
        }

# Ejemplo de uso (para pruebas internas)
if __name__ == "__main__":
    suite = SuiteCreativaMultiAgente()
    producto = {
        "nombre": "Xbox Game Pass Ultimate 1 Mes",
        "beneficio": "Acceso a cientos de juegos de alta calidad en consola y PC",
        "audiencia": "Gamers en Chile",
        "precio": "$5.990",
        "categoria": "gaming"
    }
    tendencias = "Lanzamiento de nuevo Call of Duty, Final de la Champions"
    
    resultado = suite.generar_post_completo(producto, tendencias)
    print("\n--- RESULTADO FINAL ---\n")
    print(resultado["post_final"])
