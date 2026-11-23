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

st.set_page_config(page_title="Auditoría Geoespacial IGN", layout="wide")

st.title("🗺️ Auditoría Geoespacial y Validación Municipal (IGN)")
st.markdown("**Herramienta de Control de Calidad Territorial y Corrección de Coordenadas**")

# --- FUNCIONES AUXILIARES ---
def normalizar_texto(texto):
    if not isinstance(texto, str) or pd.isna(texto):
        return ""
    texto = texto.strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c))

# --- CARGA DE CARTOGRAFÍA IGN ---
@st.cache_data
def cargar_cartografia_ign(cod_provincia_target):
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
    uploaded_file = st.file_uploader("Sube tu archivo Catálogo Excel (.xlsx)", type=["xlsx"])
    NOMBRE_PROVINCIA = st.text_input("Provincia a Auditar", "Cáceres")
    CODIGO_PROVINCIA_INE = st.text_input("Código INE Provincia", "10")

    st.subheader("📏 Umbrales de Distancia (m)")
    UMBRAL_FRONTERIZO = st.slider("Fronterizo inmediato", 10, 500, 100)
    UMBRAL_BORDE = st.slider("Borde cercano", 100, 2000, 500)
    UMBRAL_MODERADO = st.slider("Desvío moderado", 500, 10000, 2000)
    UMBRAL_NOTABLE = st.slider("Desvío notable", 2000, 50000, 6000)

if uploaded_file is not None:
    gdf_espana_4326, gdf_espana_utm = cargar_cartografia_ign(CODIGO_PROVINCIA_INE)

    if gdf_espana_utm is not None:
        df = pd.read_excel(uploaded_file, sheet_name=0)

        # Autodetección robusta de columnas
        def detectar_col(lista_columnas, preferida, alternativas):
            if preferida in lista_columnas:
                return preferida
            for alt in alternativas:
                match = next((c for c in lista_columnas if str(c).strip().lower() == alt.lower()), None)
                if match:
                    return match
            return preferida

        lista_cols = list(df.columns)
        COL_ID = detectar_col(lista_cols, "ID", ["id", "código", "codigo", "nº", "numero", "clave"])
        COL_RECURSO = detectar_col(lista_cols, "Nombre establecimiento", ["recurso", "denominacion", "denominación", "nombre", "activo", "nombre establecimiento", "nombre_establecimiento"])
        COL_MUNICIPIO = detectar_col(lista_cols, "Municipio", ["municipio", "poblacion", "población", "localidad"])
        COL_LATITUD = detectar_col(lista_cols, "Latitud", ["latitud", "lat", "y", "latitude"])
        COL_LONGITUD = detectar_col(lista_cols, "Longitud", ["longitud", "lon", "lng", "x", "longitude"])

        # Validación de existencia real de columnas críticas detectadas
        columnas_faltantes = [c for c in [COL_LATITUD, COL_LONGITUD] if c not in df.columns]
        if columnas_faltantes:
            st.error(f"❌ No se encontraron las columnas de coordenadas en el archivo. Columnas esperadas: Latitud, Longitud. Detectadas ausentes: {columnas_faltantes}. Por favor comprueba tu Excel o renombra las columnas de coordenadas.")
        else:
            # Limpieza e indexación de coordenadas
            for col_coord in [COL_LATITUD, COL_LONGITUD]:
                df[col_coord] = pd.to_numeric(df[col_coord].astype(str).str.replace(",", "."), errors="coerce")

            mask_coords_validas = df[COL_LATITUD].notna() & df[COL_LONGITUD].notna()

            if df[mask_coords_validas].empty:
                st.warning("⚠️ No se encontraron registros con coordenadas numéricas válidas en el archivo.")
            else:
                gdf_puntos_4326 = gpd.GeoDataFrame(
                    df[mask_coords_validas].copy(),
                    geometry=gpd.points_from_xy(
                        df.loc[mask_coords_validas, COL_LONGITUD],
                        df.loc[mask_coords_validas, COL_LATITUD]
                    ),
                    crs="EPSG:4326"
                )
                gdf_puntos_utm = gdf_puntos_4326.to_crs(epsg=25830)

                # Cruce espacial
                gdf_joined = gpd.sjoin(gdf_puntos_utm, gdf_espana_utm, how="left", predicate="within")

                dicc_poligonos_utm = {
                    normalizar_texto(nom): geom
                    for nom, geom in zip(gdf_espana_utm["MUNICIPIO_OFICIAL_IGN"], gdf_espana_utm["geometry"])
                }

                def evaluar_registro(row):
                    mun_excel_raw = row.get(COL_MUNICIPIO, "")
                    mun_ign_raw = row.get("MUNICIPIO_OFICIAL_IGN", np.nan)
                    prov_ign_raw = row.get("PROVINCIA_OFICIAL_IGN", np.nan)
                    cod_prov_ign = str(row.get("COD_PROVINCIA_IGN", "")).zfill(2)
                    punto_utm = row["geometry"]

                    mun_excel_norm = normalizar_texto(mun_excel_raw)
                    mun_ign_norm = normalizar_texto(mun_ign_raw)

                    es_interprovincial = False
                    alerta_provincia = "Misma provincia"
                    if pd.notna(cod_prov_ign) and cod_prov_ign != "" and cod_prov_ign != str(CODIGO_PROVINCIA_INE).zfill(2):
                        es_interprovincial = True
                        alerta_provincia = f"⚠️ OTRA PROVINCIA ({prov_ign_raw})"

                    if pd.isna(mun_ign_raw):
                        return pd.Series(["FUERA_DE_PROVINCIA_O_SIN_POLIGONO", np.nan, mun_ign_raw, prov_ign_raw, "Sin polígono"])

                    if mun_excel_norm == mun_ign_norm:
                        return pd.Series(["CORRECTO", 0.0, mun_ign_raw, prov_ign_raw, alerta_provincia])

                    distancia_m = np.nan
                    if mun_excel_norm in dicc_poligonos_utm:
                        poli_declarado = dicc_poligonos_utm[mun_excel_norm]
                        distancia_m = round(punto_utm.distance(poli_declarado), 1)

                    if pd.isna(distancia_m):
                        categoria = "TOPÓNIMO_NO_LOCALIZADO"
                    elif distancia_m == 0:
                        categoria = "CORRECTO"
                    elif distancia_m <= UMBRAL_FRONTERIZO:
                        categoria = "FRONTERIZO_INMEDIATO"
                    elif distancia_m <= UMBRAL_BORDE:
                        categoria = "BORDE_CERCANO"
                    elif distancia_m <= UMBRAL_MODERADO:
                        categoria = "DESVÍO_MODERADO"
                    elif distancia_m <= UMBRAL_NOTABLE:
                        categoria = "DESVÍO_NOTABLE"
                    else:
                        categoria = "ERROR_GRAVE"

                    return pd.Series([categoria, distancia_m, mun_ign_raw, prov_ign_raw, alerta_provincia])

                if not gdf_joined.empty:
                    res_eval = gdf_joined.apply(evaluar_registro, axis=1)
                    res_eval.columns = ["ESTADO_AUDITORIA", "METROS_AL_LIMITE", "MUNICIPIO_DETECTADO_IGN", "PROVINCIA_DETECTADA_IGN", "ALERTA_INTERPROVINCIAL"]
                    for col in res_eval.columns:
                        gdf_joined[col] = res_eval[col]

                # Volcar resultados a df principal
                for col in ["MUNICIPIO_DETECTADO_IGN", "PROVINCIA_DETECTADA_IGN", "METROS_AL_LIMITE", "ESTADO_AUDITORIA", "ALERTA_INTERPROVINCIAL"]:
                    df[col] = np.nan
                df["ESTADO_AUDITORIA"] = "SIN_COORDENADAS"

                if not gdf_joined.empty:
                    for col in ["MUNICIPIO_DETECTADO_IGN", "PROVINCIA_DETECTADA_IGN", "METROS_AL_LIMITE", "ESTADO_AUDITORIA", "ALERTA_INTERPROVINCIAL"]:
                        df.loc[gdf_joined.index, col] = gdf_joined[col]

                # --- INTERFAZ STREAMLIT ---
                st.success("✅ Auditoría completada con éxito.")

                # Informe Ejecutivo
                st.header("📊 Informe Ejecutivo de Calidad")
                col1, col2, col3 = st.columns(3)
                col1.metric("Total de registros", len(df))
                col2.metric("Coincidencia Exacta", len(df[df["ESTADO_AUDITORIA"]=="CORRECTO"]))
                col3.metric("Incidencias a revisar", len(df[~df["ESTADO_AUDITORIA"].isin(["CORRECTO", "SIN_COORDENADAS"])]))

                # Mostrar tabla de discrepancias
                df_incidencias = df[df["ESTADO_AUDITORIA"] != "CORRECTO"]
                st.subheader("📋 Listado de Incidencias")
                st.dataframe(df_incidencias[[COL_RECURSO, COL_MUNICIPIO, "MUNICIPIO_DETECTADO_IGN", "ESTADO_AUDITORIA", "METROS_AL_LIMITE"]])

                # Descarga de Excel de discrepancias
                towrite = io.BytesIO()
                df_incidencias.to_excel(towrite, index=False, header=True)
                towrite.seek(0)
                st.download_button(label="💾 Descargar Informe de Discrepancias (.xlsx)", data=towrite, file_name="Discrepancias_Auditoria.xlsx", mime="application/vnd.ms-excel")

                # Mapa
                st.header("🗺️ Mapa Interactivo de Auditoría")
                centro_lat = float(df[COL_LATITUD].median())
                centro_lon = float(df[COL_LONGITUD].median())

                mapa = folium.Map(location=[centro_lat, centro_lon], zoom_start=9)

                for idx, row in df[mask_coords_validas].iterrows():
                    popup_txt = f"<b>{row[COL_RECURSO]}</b><br>Excel: {row[COL_MUNICIPIO]}<br>Detectado: {row['MUNICIPIO_DETECTADO_IGN']}<br>Desvío: {row['METROS_AL_LIMITE']}m"
                    folium.Marker(
                        [row[COL_LATITUD], row[COL_LONGITUD]],
                        popup=popup_txt,
                        tooltip=row[COL_RECURSO]
                    ).add_to(mapa)

                st_folium(mapa, width=900, height=500)
else:
    st.info("ℹ️ Sube un archivo Excel en el panel lateral para iniciar la validación territorial.")
