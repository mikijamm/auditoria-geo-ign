import io
import unicodedata
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import numpy as np
import geopandas as gpd
import folium

st.set_page_config(page_title="Auditoría Geoespacial IGN", layout="wide")

# ==============================================================================
# FUNCIONES AUXILIARES Y CARGA EN CACHÉ
# ==============================================================================
def normalizar_texto(texto):
    if not isinstance(texto, str) or pd.isna(texto):
        return ""
    texto = texto.strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c))

def autodetectar_columna(df_target, columna_preferida, alternativas):
    if columna_preferida in df_target.columns:
        return columna_preferida
    for alt in alternativas:
        col_match = next((c for c in df_target.columns if c.strip().lower() == alt.lower()), None)
        if col_match:
            return col_match
    return None

@st.cache_resource(show_spinner="Descargando cartografía oficial del IGN (solo la primera vez)...")
def cargar_cartografia_ign():
    url_ign = "https://github.com/rOpenSpain/mapSpain/raw/sianedata/dist/se89_3_admin_muni_a_x.gpkg"
    gdf = gpd.read_file(url_ign, engine="pyogrio")
    cols = list(gdf.columns)

    col_nom = next((c for c in ["rotulo", "name", "nombre", "municipio", "label", "rotulo_muni", "NAME"] if c in cols), cols[0])
    col_cpro = next((c for c in ["cpro", "CPRO", "cod_prov", "codprov", "ine.prov.cd"] if c in cols), None)
    col_prov = next((c for c in ["provincia", "PROVINCIA", "ine.prov.name", "ine.prov.name.x", "prov_name"] if c in cols), None)

    gdf["MUNICIPIO_OFICIAL_IGN"] = gdf[col_nom].astype(str)
    if col_cpro:
        gdf["COD_PROVINCIA_IGN"] = gdf[col_cpro].astype(str).str.zfill(2)
    else:
        col_ine = next((c for c in ["inecode", "cod_ine", "codigo_ine", "id", "LAU_CODE"] if c in cols), None)
        gdf["COD_PROVINCIA_IGN"] = gdf[col_ine].astype(str).str.zfill(5).str[:2] if col_ine else "00"

    gdf["PROVINCIA_OFICIAL_IGN"] = gdf[col_prov].astype(str) if col_prov else gdf["COD_PROVINCIA_IGN"]

    cols_keep = ["MUNICIPIO_OFICIAL_IGN", "PROVINCIA_OFICIAL_IGN", "COD_PROVINCIA_IGN", "geometry"]
    gdf_4326 = gdf[cols_keep].to_crs(epsg=4326)
    gdf_utm = gdf_4326.to_crs(epsg=25830)

    # Diccionario de provincias para el desplegable
    provincias = (
        gdf_4326[["COD_PROVINCIA_IGN", "PROVINCIA_OFICIAL_IGN"]]
        .drop_duplicates()
        .sort_values("PROVINCIA_OFICIAL_IGN")
    )
    return gdf_4326, gdf_utm, provincias

# ==============================================================================
# INTERFAZ PRINCIPAL Y BARRA LATERAL
# ==============================================================================
st.title("🗺️ Auditoría Geoespacial y Validación Municipal (IGN)")
st.write("Sube un catálogo en Excel para validar sus coordenadas frente a los límites oficiales del IGN.")

gdf_espana_4326, gdf_espana_utm, df_provincias = cargar_cartografia_ign()

with st.sidebar:
    st.header("⚙️ Configuración")
    archivo_subido = st.file_uploader("1. Sube el archivo Excel", type=["xlsx", "xls"])

    # Selector de provincia dinámico
    opciones_prov = {
        f"{row['PROVINCIA_OFICIAL_IGN']} ({row['COD_PROVINCIA_IGN']})": (row["COD_PROVINCIA_IGN"], row["PROVINCIA_OFICIAL_IGN"])
        for _, row in df_provincias.iterrows()
    }
    prov_default = next((k for k in opciones_prov if "(10)" in k), list(opciones_prov.keys())[0])
    sel_prov = st.selectbox("2. Provincia a auditar", list(opciones_prov.keys()), index=list(opciones_prov.keys()).index(prov_default))
    CODIGO_PROVINCIA_INE, NOMBRE_PROVINCIA = opciones_prov[sel_prov]

    mostrar_correctos = st.checkbox("Mostrar puntos correctos en el mapa", value=True)

    with st.expander("📏 Ajustar umbrales de distancia (metros)"):
        u_frontera = st.number_input("Fronterizo inmediato", value=100, step=50)
        u_borde = st.number_input("Borde cercano", value=500, step=100)
        u_moderado = st.number_input("Desvío moderado", value=2000, step=500)
        u_notable = st.number_input("Desvío notable", value=6000, step=1000)

# ==============================================================================
# PROCESAMIENTO DEL ARCHIVO SUBIDO
# ==============================================================================
if archivo_subido is not None:
    df = pd.read_excel(archivo_subido)

    # Autodetección de columnas con opción de ajuste manual si fallara alguna
    col_id = autodetectar_columna(df, "ID", ["id", "código", "codigo", "nº", "numero", "clave"])
    col_recurso = autodetectar_columna(df, "Nombre establecimiento", ["recurso", "denominacion", "denominación", "nombre", "activo"])
    col_municipio = autodetectar_columna(df, "Municipio", ["municipio", "poblacion", "población", "localidad"])
    col_lat = autodetectar_columna(df, "Latitud", ["latitud", "lat", "y"])
    col_lon = autodetectar_columna(df, "Longitud", ["longitud", "lon", "lng", "x"])

    with st.sidebar.expander("🗂️ Mapeo de columnas detectadas"):
        cols_excel = list(df.columns)
        col_recurso = st.selectbox("Nombre/Recurso", cols_excel, index=cols_excel.index(col_recurso) if col_recurso in cols_excel else 0)
        col_municipio = st.selectbox("Municipio", cols_excel, index=cols_excel.index(col_municipio) if col_municipio in cols_excel else 0)
        col_lat = st.selectbox("Latitud", cols_excel, index=cols_excel.index(col_lat) if col_lat in cols_excel else 0)
        col_lon = st.selectbox("Longitud", cols_excel, index=cols_excel.index(col_lon) if col_lon in cols_excel else 0)

    # Limpieza de coordenadas
    for c in [col_lat, col_lon]:
        df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", "."), errors="coerce")

    with st.spinner("Realizando cruce espacial y calculando distancias..."):
        mask_validas = df[col_lat].notna() & df[col_lon].notna()
        gdf_pts_utm = gpd.GeoDataFrame(
            df[mask_validas].copy(),
            geometry=gpd.points_from_xy(df.loc[mask_validas, col_lon], df.loc[mask_validas, col_lat]),
            crs="EPSG:4326"
        ).to_crs(epsg=25830)

        gdf_joined = gpd.sjoin(gdf_pts_utm, gdf_espana_utm, how="left", predicate="within")
        dicc_poli = {
            normalizar_texto(nom): geom
            for nom, geom in zip(gdf_espana_utm["MUNICIPIO_OFICIAL_IGN"], gdf_espana_utm["geometry"])
        }

        def evaluar(row):
            mun_exc = normalizar_texto(row.get(col_municipio, ""))
            mun_ign_raw = row.get("MUNICIPIO_OFICIAL_IGN", np.nan)
            prov_ign_raw = row.get("PROVINCIA_OFICIAL_IGN", np.nan)
            cod_prov = str(row.get("COD_PROVINCIA_IGN", "")).zfill(2)
            alerta = f"⚠️ OTRA PROVINCIA ({prov_ign_raw})" if (pd.notna(cod_prov) and cod_prov not in ["", "nan", CODIGO_PROVINCIA_INE]) else "Misma provincia"

            if pd.isna(mun_ign_raw):
                return pd.Series(["FUERA_DE_PROVINCIA_O_SIN_POLIGONO", np.nan, mun_ign_raw, prov_ign_raw, "Sin polígono"])
            if mun_exc == normalizar_texto(mun_ign_raw):
                return pd.Series(["CORRECTO", 0.0, mun_ign_raw, prov_ign_raw, alerta])

            dist_m = np.nan
            if mun_exc in dicc_poli:
                dist_m = round(row["geometry"].distance(dicc_poli[mun_exc]), 1)
            else:
                for nom_of, poli in dicc_poli.items():
                    if mun_exc and (mun_exc in nom_of or nom_of in mun_exc):
                        dist_m = round(row["geometry"].distance(poli), 1)
                        break

            if pd.isna(dist_m): cat = "TOPÓNIMO_NO_LOCALIZADO"
            elif dist_m == 0: cat = "CORRECTO"
            elif dist_m <= u_frontera: cat = "FRONTERIZO_INMEDIATO"
            elif dist_m <= u_borde: cat = "BORDE_CERCANO"
            elif dist_m <= u_moderado: cat = "DESVÍO_MODERADO"
            elif dist_m <= u_notable: cat = "DESVÍO_NOTABLE"
            else: cat = "ERROR_GRAVE"

            return pd.Series([cat, dist_m, mun_ign_raw, prov_ign_raw, alerta])

        df["MUNICIPIO_DETECTADO_IGN"] = None
        df["PROVINCIA_DETECTADA_IGN"] = None
        df["METROS_AL_LIMITE"] = np.nan
        df["ESTADO_AUDITORIA"] = "SIN_COORDENADAS"
        df["ALERTA_INTERPROVINCIAL"] = "Sin coordenadas"

        if not gdf_joined.empty:
            res = gdf_joined.apply(evaluar, axis=1)
            res.columns = ["ESTADO_AUDITORIA", "METROS_AL_LIMITE", "MUNICIPIO_DETECTADO_IGN", "PROVINCIA_DETECTADA_IGN", "ALERTA_INTERPROVINCIAL"]
            for c in res.columns:
                df.loc[gdf_joined.index, c] = res[c]

    # ==========================================================================
    # 1. BREVE INFORME EJECUTIVO
    # ==========================================================================
    st.subheader(f"📊 Informe de Calidad — {NOMBRE_PROVINCIA}")

    sin_coords = df[df["ESTADO_AUDITORIA"] == "SIN_COORDENADAS"]
    correctos = df[df["ESTADO_AUDITORIA"] == "CORRECTO"]
    incidencias = df[~df["ESTADO_AUDITORIA"].isin(["CORRECTO", "SIN_COORDENADAS"])]
    interprov = df[df["ALERTA_INTERPROVINCIAL"].str.contains("OTRA PROVINCIA", na=False)]
    posibles_inv = df[(df[col_lat] < 35) | (df[col_lat] > 44) | (df[col_lon] > 5)]

    validos = len(df) - len(sin_coords)
    pct_ok = (len(correctos) / validos * 100) if validos > 0 else 0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total registros", len(df))
    m2.metric("Correctos", f"{len(correctos)} ({pct_ok:.1f}%)")
    m3.metric("Discrepancias", len(incidencias))
    m4.metric("Sin coordenadas", len(sin_coords))

    if len(interprov) > 0:
        st.warning(f"⚠️ Se han detectado **{len(interprov)}** casos ubicados en provincias colindantes.")
    if len(posibles_inv) > 0:
        st.error(f"🚨 Posible inversión Latitud/Longitud en **{len(posibles_inv)}** registros.")

    col_t1, col_t2 = st.columns([1, 2])
    with col_t1:
        st.write("**Desglose por categoría**")
        st.dataframe(df["ESTADO_AUDITORIA"].value_counts().rename("Registros"), use_container_width=True)
    with col_t2:
        st.write("**Vista previa de discrepancias**")
        if not incidencias.empty:
            cols_ver = [col_recurso, col_municipio, "MUNICIPIO_DETECTADO_IGN", "METROS_AL_LIMITE", "ESTADO_AUDITORIA", "ALERTA_INTERPROVINCIAL"]
            st.dataframe(incidencias[cols_ver], use_container_width=True, height=220)
        else:
            st.success("🎉 ¡Catálogo sin discrepancias!")

    # ==========================================================================
    # 2. MAPA INTERACTIVO CON ANTI-SOLAPAMIENTO Y FILTROS
    # ==========================================================================
    st.subheader("🗺️ Mapa Interactivo de Discrepancias")

    df_mapa = df[df[col_lat].notna() & df[col_lon].notna()].copy()
    if not mostrar_correctos:
        df_mapa = df_mapa[df_mapa["ESTADO_AUDITORIA"] != "CORRECTO"]

    centro_lat = float(df_mapa[col_lat].median()) if not df_mapa.empty else 39.80
    centro_lon = float(df_mapa[col_lon].median()) if not df_mapa.empty else -6.15

    mapa = folium.Map(
        location=[centro_lat, centro_lon],
        zoom_start=9,
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
        attr="Esri & IGN"
    )

    gdf_prov_4326 = gdf_espana_4326[gdf_espana_4326["COD_PROVINCIA_IGN"] == CODIGO_PROVINCIA_INE]
    folium.GeoJson(
        gdf_prov_4326,
        name=f"Términos Municipales {NOMBRE_PROVINCIA} (IGN)",
        style_function=lambda x: {"fillColor": "#2c3e50", "color": "#34495e", "weight": 1.0, "fillOpacity": 0.04},
        tooltip=folium.GeoJsonTooltip(fields=["MUNICIPIO_OFICIAL_IGN"], aliases=["Municipio IGN:"])
    ).add_to(mapa)

    conteos = df["ESTADO_AUDITORIA"].value_counts().to_dict()
    capas = {
        "ERROR_GRAVE": folium.FeatureGroup(name=f"🔴 Error Grave (> {u_notable/1000:.0f} km) [{conteos.get('ERROR_GRAVE', 0)}]"),
        "FUERA_DE_PROVINCIA_O_SIN_POLIGONO": folium.FeatureGroup(name=f"🟣 Fuera / Sin Polígono [{conteos.get('FUERA_DE_PROVINCIA_O_SIN_POLIGONO', 0)}]"),
        "DESVÍO_NOTABLE": folium.FeatureGroup(name=f"🟤 Desvío Notable [{conteos.get('DESVÍO_NOTABLE', 0)}]"),
        "DESVÍO_MODERADO": folium.FeatureGroup(name=f"🟠 Desvío Moderado [{conteos.get('DESVÍO_MODERADO', 0)}]"),
        "BORDE_CERCANO": folium.FeatureGroup(name=f"🟡 Borde Cercano [{conteos.get('BORDE_CERCANO', 0)}]"),
        "FRONTERIZO_INMEDIATO": folium.FeatureGroup(name=f"🟢 Fronterizo Inmediato [{conteos.get('FRONTERIZO_INMEDIATO', 0)}]"),
        "TOPÓNIMO_NO_LOCALIZADO": folium.FeatureGroup(name=f"⚪ Topónimo No Localizado [{conteos.get('TOPÓNIMO_NO_LOCALIZADO', 0)}]"),
        "CORRECTO": folium.FeatureGroup(name=f"🔵 Correcto [{conteos.get('CORRECTO', 0)}]", show=mostrar_correctos),
    }
    estilos = {
        "ERROR_GRAVE": {"color": "#922b21", "fill": "#e74c3c", "r": 8.0},
        "FUERA_DE_PROVINCIA_O_SIN_POLIGONO": {"color": "#5b2c6f", "fill": "#9b59b6", "r": 8.5},
        "DESVÍO_NOTABLE": {"color": "#6e2c00", "fill": "#d35400", "r": 7.0},
        "DESVÍO_MODERADO": {"color": "#b9770e", "fill": "#f39c12", "r": 6.0},
        "BORDE_CERCANO": {"color": "#b7950b", "fill": "#f1c40f", "r": 5.0},
        "FRONTERIZO_INMEDIATO": {"color": "#196f3d", "fill": "#2ecc71", "r": 4.5},
        "TOPÓNIMO_NO_LOCALIZADO": {"color": "#566573", "fill": "#aab7b8", "r": 5.0},
        "CORRECTO": {"color": "#1f618d", "fill": "#3498db", "r": 4.0},
    }
    prioridad = list(estilos.keys())

    for (lat, lon), grupo in df_mapa.groupby([col_lat, col_lon]):
        if not (35 < lat < 45 and -10 < lon < 5):
            continue
        est_prin = next((e for e in prioridad if e in grupo["ESTADO_AUDITORIA"].values), "CORRECTO")
        cfg = estilos[est_prin]
        items_html = "".join([
            f"<div><b>• {r.get(col_recurso, 'Sin nombre')}</b><br>"
            f"<i>Excel:</i> {r.get(col_municipio)} → <i>IGN:</i> <b>{r.get('MUNICIPIO_DETECTADO_IGN')}</b><br>"
            f"<small>Desvío: {r.get('METROS_AL_LIMITE', 'N/D')} m | {r['ESTADO_AUDITORIA']}</small></div><hr style='margin:4px 0'>"
            for _, r in grupo.iterrows()
        ])
        folium.CircleMarker(
            location=[lat, lon],
            radius=cfg["r"] + (2 if len(grupo) > 1 else 0),
            color=cfg["color"], fill=True, fill_color=cfg["fill"], fill_opacity=0.85,
            popup=folium.Popup(items_html, max_width=320),
            tooltip=f"[{len(grupo)}] {grupo.iloc[0].get(col_recurso)} ({est_prin})"
        ).add_to(capas[est_prin])

    for c in capas.values():
        c.add_to(mapa)
    folium.LayerControl(position="topright", collapsed=False).add_to(mapa)

    # Renderizado directo en HTML (evita recargas innecesarias al hacer clic)
    components.html(mapa._repr_html_(), height=600)

    # ==========================================================================
    # 3. EXPORTACIÓN DEL EXCEL DE DISCREPANCIAS
    # ==========================================================================
    st.subheader("💾 Exportar resultados")
    if not incidencias.empty:
        cols_disp = incidencias.columns
        col_desc = next((c for c in cols_disp if "descri" in c.lower()), None)
        col_dir = next((c for c in cols_disp if any(k in c.lower() for k in ["direc", "calle", "via"])), None)
        col_url = next((c for c in cols_disp if any(k in c.lower() for k in ["url", "web", "fuente"])), None)

        df_export = pd.DataFrame({
            "ID": incidencias[col_id] if col_id else "",
            "NOMBRE ACTIVO CORREGIDO": incidencias[col_recurso],
            "DESCRIPCION_CORTA": incidencias[col_desc] if col_desc else "",
            "DIRECCIÓN": incidencias[col_dir] if col_dir else "",
            "LATITUD": incidencias[col_lat],
            "LONGITUD": incidencias[col_lon],
            "URL FUENTE": incidencias[col_url] if col_url else "",
            "Municipio Declarado": incidencias[col_municipio],
            "Municipio Detectado IGN": incidencias["MUNICIPIO_DETECTADO_IGN"],
            "Estado de Auditoría": incidencias["ESTADO_AUDITORIA"],
            "Metros al Límite": incidencias["METROS_AL_LIMITE"],
        })

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df_export.to_excel(writer, index=False)

        st.download_button(
            label=f"📥 Descargar Excel con {len(df_export)} discrepancias",
            data=buffer.getvalue(),
            file_name=f"Discrepancias_Catalogo_{NOMBRE_PROVINCIA}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
else:
    st.info("👈 Sube un archivo Excel en el panel izquierdo para iniciar la auditoría.")
