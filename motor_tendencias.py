import requests
import xml.etree.ElementTree as ET
import json
import os
from datetime import datetime

# Google Trends category IDs (cat param)
NICHO_CATEGORIAS = {
    'general':         0,
    'tecnologia':      5,
    'gaming':          9,
    'deportes':        30,
    'negocios':        7,
    'entretenimiento': 3,
    'internet':        15,
    'noticias':        22,
}

# Both URL formats for fallback
RSS_BASE_NEW   = "https://trends.google.com/trending/rss?geo=CL"
RSS_BASE_DAILY = "https://trends.google.com/trends/trendingsearches/daily/rss?geo=CL"

# XML namespaces used by both RSS formats
NS_NEW   = 'https://trends.google.com/trending/rss'
NS_DAILY = 'https://trends.google.com/trends/trendingsearches/daily/rss'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'es-CL,es;q=0.9',
}


class MotorTendenciasChile:
    """Motor de Viralidad Predictiva para el mercado chileno."""

    def __init__(self):
        self.tendencias_cache = []
        self.ultima_actualizacion = None

    def _parse_items(self, content: bytes) -> list:
        """Parsea el XML del RSS y extrae items enriquecidos."""
        tendencias = []
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return tendencias

        items = root.findall('.//item')
        for item in items:
            titulo_el = item.find('title')
            titulo = titulo_el.text.strip() if titulo_el is not None and titulo_el.text else ''
            if not titulo:
                continue

            # Traffic — try both namespace variants
            trafico = ''
            for ns in (NS_NEW, NS_DAILY):
                el = item.find(f'{{{ns}}}approx_traffic')
                if el is not None and el.text:
                    trafico = el.text.strip()
                    break

            # First news item — title, source, image
            contexto = fuente = imagen = ''
            for ns in (NS_NEW, NS_DAILY):
                ctx_el = item.find(f'.//{{{ns}}}news_item_title')
                if ctx_el is not None and ctx_el.text:
                    contexto = ctx_el.text.strip()
                src_el = item.find(f'.//{{{ns}}}news_item_source')
                if src_el is not None and src_el.text:
                    fuente = src_el.text.strip()
                img_el = item.find(f'.//{{{ns}}}news_item_picture')
                if img_el is not None and img_el.text:
                    imagen = img_el.text.strip()
                if contexto:
                    break

            tendencias.append({
                'termino': titulo,
                'trafico': trafico or 'N/A',
                'contexto': contexto,
                'fuente': fuente,
                'imagen': imagen,
            })
        return tendencias

    def obtener_tendencias_google(self, limite=10, nicho='general'):
        """Extrae tendencias actuales de Chile. nicho filtra por categoría."""
        cat_id = NICHO_CATEGORIAS.get(nicho, 0)

        # Try new URL first, fall back to daily RSS
        urls = [
            f"{RSS_BASE_NEW}&category={cat_id}" if cat_id else RSS_BASE_NEW,
            f"{RSS_BASE_DAILY}&cat={cat_id}",
        ]

        for url in urls:
            try:
                response = requests.get(url, headers=HEADERS, timeout=15)
                if response.status_code != 200:
                    continue
                items = self._parse_items(response.content)
                if items:
                    self.tendencias_cache = items[:limite]
                    self.ultima_actualizacion = datetime.now()
                    return self.tendencias_cache
            except Exception as e:
                print(f"⚠️ Error con URL {url}: {e}")
                continue

        print("❌ No se pudo obtener tendencias de ninguna fuente")
        return []

    def formatear_para_llm(self, tendencias=None):
        """Convierte tendencias a string optimizado para el LLM."""
        if tendencias is None:
            tendencias = self.obtener_tendencias_google()
        if not tendencias:
            return "No hay tendencias claras detectadas en este momento."
        lineas = []
        for t in tendencias:
            lineas.append(f"- {t['termino']} ({t['trafico']} búsquedas): {t['contexto']}")
        return "\n".join(lineas)

    def guardar_tendencias_locales(self, path="tendencias_chile.json"):
        data = {
            "fecha": self.ultima_actualizacion.isoformat() if self.ultima_actualizacion else None,
            "tendencias": self.tendencias_cache
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    motor = MotorTendenciasChile()
    for nicho_test in ['general', 'gaming', 'tecnologia']:
        print(f"\n=== {nicho_test.upper()} ===")
        ts = motor.obtener_tendencias_google(limite=5, nicho=nicho_test)
        for i, t in enumerate(ts, 1):
            print(f"{i}. {t['termino']} | {t['trafico']} | {t['fuente']}")
            if t['contexto']:
                print(f"   {t['contexto']}")
