%%writefile app.py
import streamlit as st
import pandas as pd
import numpy as np
import geopandas as gpd
from shapely.geometry import Point
import folium
from streamlit_folium import st_folium
import unicodedata
import os
import io

st.set_page_config(page_title="Auditoría Geosparcial IGN", layout="wide")

st.title("🗺️ Auditoría Geosparcial y Validación Municipal (IGN)")
st.markdown("**Herramienta de Control de Calidad Territorial y Corrección de Coordenadas**")

# --- FUNCIONES AUXILIARES ---
def normalizar_texto(texto):
    if not isinstance(texto, str) or pd.isna(texto):
        return ""
    texto = texto.strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c))

# --- CARGA DE CARTOGRAFÍA IGN (Caché para optimizar) ---
@st.cache_data
def cargar_cartografia_ign():
    URL_IGN_MUNICIPIOS = "https://github.com/rOpenSpain/mapSpain/raw/sianedata/dist/se89_3_admin_muni_a_x.gpkg"
    try:
        gdf_espana = gpd.read_file(URL_IGN_MUNICIPIOS, engine="pyogrio")
        cols_ign = list(gdf_espana.columns)

        cand_nombre = ["rotulo", "name", "nombre", "municipio", "label", "rotulo_muni", "NAME"]
        col_ign_nombre = next((c for c in cand_nombre if c in cols_ign), cols_ign[0])
        gdf_espana["MUNICIPIO_OFICIAL_IGN"] = gdf_espana[col_ign_nombre].astype(str)

        cand_cpro = ["cpro", "CPRO", "cod_prov", "codprov", "cpro_code", "ine.prov.cd"]
        col_ign_cpro = next((c for c in cand_cpro if c in cols_ign), None)
        if col_ign_cpro:
            gdf_espana["COD_PROVINCIA_IGN"] = gdf_espana[col_ign_cpro].astype(str).str.zfill(2)
        else:
            col_ine = next((c for c in ["inecode", "cod_ine", "codigo_ine", "id", "LAU_CODE"] if c in cols_ign), None)
            if col_ine:
                gdf_espana["COD_PROVINCIA_IGN"] = gdf_espana[col_ine].astype(str).str.zfill(5).str[:2]
            else:
                gdf_espana["COD_PROVINCIA_IGN"] = "10"

        cand_prov = ["provincia", "PROVINCIA", "ine.prov.name", "ine.prov.name.x", "cpro_name", "prov_name"]
        col_ign_prov = next((c for c in cand_prov if c in cols_ign), None)
        if col_ign_prov:
            gdf_espana["PROVINCIA_OFICIAL_IGN"] = gdf_espana[col_ign_prov].astype(str)
        else:
            gdf_espana["PROVINCIA_OFICIAL_IGN"] = "Cáceres"

        cols_a_guardar = ["MUNICIPIO_OFICIAL_IGN", "PROVINCIA_OFICIAL_IGN", "COD_PROVINCIA_IGN", "geometry"]
        gdf_espana_4326 = gdf_espana[cols_a_guardar].to_crs(epsg=4326)
        gdf_espana_utm = gdf_espana_4326.to_crs(epsg=25830)
        return gdf_espana_4326, gdf_espana_utm
    except Exception as e:
        st.error(f"Error cargando cartografía del IGN: {e}")
        return None, None

# --- SIDEBAR: Configuración ---
with st.sidebar:
    st.header("⚙️ Configuración")
    
    # Subida de archivo
    uploaded_file = st.file_uploader("Sube tu archivo Catálogo Excel (.xlsx)", type=["xlsx"])
    
    # Selección de provincia
    NOMBRE_PROVINCIA = st.text_input("Provincia a Auditar", "Cáceres")
    CODIGO_PROVINCIA_INE = st.text_input("Código INE Provincia", "10")
    
    # Umbrales
    st.subheader("📏 Umbrales de Distancia (m)")
    UMBRAL_FRONTERIZO = st.slider("Fronterizo inmediato", 10, 500, 100)
    UMBRAL_BORDE = st.slider("Borde cercano", 100, 2000, 500)
    UMBRAL_MODERADO = st.slider("Desvío moderado", 500, 10000, 2000)
    UMBRAL_NOTABLE = st.slider("Desvío notable", 2000, 50000, 6000)

# Carga de cartografía
gdf_espana_4326, gdf_espana_utm = cargar_cartografia_ign()

if uploaded_file is not None and gdf_espana_utm is not None:
    df = pd.read_excel(uploaded_file, sheet_name=0)
    st.success()
