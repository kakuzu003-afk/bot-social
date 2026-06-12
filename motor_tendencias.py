import requests
import xml.etree.ElementTree as ET
import json
import os
from datetime import datetime

class MotorTendenciasChile:
    """
    Motor de Viralidad Predictiva especializado en el mercado chileno.
    Extrae tendencias de Google Trends y otras fuentes para alimentar a los agentes creativos.
    """
    
    RSS_URL = "https://trends.google.com/trending/rss?geo=CL"
    
    def __init__(self):
        self.tendencias_cache = []
        self.ultima_actualizacion = None

    def obtener_tendencias_google(self, limite=10):
        """
        Extrae las tendencias de búsqueda actuales en Chile desde el feed RSS de Google Trends.
        """
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            response = requests.get(self.RSS_URL, headers=headers, timeout=15)
            if response.status_code != 200:
                print(f"⚠️ Error al acceder a Google Trends (HTTP {response.status_code})")
                return []

            root = ET.fromstring(response.content)
            items = root.findall('.//item')
            
            tendencias = []
            for item in items[:limite]:
                titulo = item.find('title').text
                trafico = item.find('{https://trends.google.com/trending/rss}approx_traffic')
                trafico_texto = trafico.text if trafico is not None else "N/A"
                
                # Obtener descripción o noticias relacionadas
                noticia = item.find('.//ht:news_item_title', {'ht': 'https://trends.google.com/trending/rss'})
                noticia_texto = noticia.text if noticia is not None else ""
                
                tendencias.append({
                    "termino": titulo,
                    "trafico": trafico_texto,
                    "contexto": noticia_texto
                })
            
            self.tendencias_cache = tendencias
            self.ultima_actualizacion = datetime.now()
            return tendencias
            
        except Exception as e:
            print(f"❌ Error en el scraper de tendencias: {e}")
            return []

    def formatear_para_llm(self, tendencias=None):
        """
        Convierte la lista de tendencias en un string optimizado para el prompt del Director Creativo.
        """
        if tendencias is None:
            tendencias = self.obtener_tendencias_google()
            
        if not tendencias:
            return "No hay tendencias claras detectadas en este momento."
            
        lineas = []
        for t in tendencias:
            lineas.append(f"- {t['termino']} ({t['trafico']} búsquedas): {t['contexto']}")
            
        return "\n".join(lineas)

    def guardar_tendencias_locales(self, path="tendencias_chile.json"):
        """
        Guarda las tendencias en un archivo JSON local para persistencia o auditoría.
        """
        data = {
            "fecha": self.ultima_actualizacion.isoformat() if self.ultima_actualizacion else None,
            "tendencias": self.tendencias_cache
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

# Prueba rápida del módulo
if __name__ == "__main__":
    motor = MotorTendenciasChile()
    print("🔍 Extrayendo tendencias actuales de Chile...")
    tendencias = motor.obtener_tendencias_google(limite=5)
    for i, t in enumerate(tendencias, 1):
        print(f"{i}. {t['termino']} | Tráfico: {t['trafico']}")
        if t['contexto']:
            print(f"   Contexto: {t['contexto']}")
    
    print("\n--- FORMATO PARA LLM ---")
    print(motor.formatear_para_llm(tendencias))
