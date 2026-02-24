import pandas as pd
import streamlit as st
from datetime import datetime
from zoneinfo import ZoneInfo
from streamlit_autorefresh import st_autorefresh
from supabase import create_client, Client
import altair as alt

# ---------------- SUPABASE ----------------
SUPABASE_URL = st.secrets["supabase"]["url"]
SUPABASE_KEY = st.secrets["supabase"]["anon_key"]  # publishable key
SUPABASE_TABLE = "programacion_transporte"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

COL_OS_DB = "os"
COL_FECHA_DB = "fecha_programacion"
COL_UPDATED_DB = "updated_at"

# ---------------- STREAMLIT ----------------
st.set_page_config(page_title="Vencimientos de Casos", layout="wide")

st.markdown(
    """
<style>
.block-container { padding-top: 0.6rem !important; padding-bottom: 0.8rem !important; }
h1 { margin-bottom: 0.2rem !important; }
.stCaption { margin-bottom: 0.2rem !important; }
.stColumns { margin-bottom: 0.4rem !important; }

.kpi-card {
    height: 120px;
    border-radius: 14px;
    padding: 16px 18px;
    display: flex;
    flex-direction: column;
    justify-content: center;
}
.kpi-title { font-size: 16px; opacity: 0.9; }
.kpi-value { font-size: 46px; font-weight: 900; line-height: 1.1; }
</style>
""",
    unsafe_allow_html=True,
)

st_autorefresh(interval=1000, key="refresh")
st.title("DashBoard Vencimientos de Casos")

# ---------------- LOAD DATA ----------------
def load_data_from_supabase() -> pd.DataFrame:
    resp = (
        supabase.table(SUPABASE_TABLE)
        .select(f"{COL_OS_DB},{COL_FECHA_DB},{COL_UPDATED_DB}")
        .execute()
    )
    data = resp.data or []
    if not data:
        return pd.DataFrame(columns=[COL_OS_DB, COL_FECHA_DB, COL_UPDATED_DB])
    return pd.DataFrame(data)

df = load_data_from_supabase()

# ---------------- RELOJ + ÚLTIMA LECTURA (SUPABASE) ----------------
now_ui = datetime.now(ZoneInfo("America/Santiago")).replace(tzinfo=None)

last_updated = None
if not df.empty and COL_UPDATED_DB in df.columns:
    tmp = pd.to_datetime(df[COL_UPDATED_DB], errors="coerce")
    if tmp.notna().any():
        last_updated = tmp.max()

c_time1, c_time2 = st.columns(2)
with c_time1:
    st.caption(f"🕒 Hora actual: **{now_ui.strftime('%Y-%m-%d %H:%M:%S')}**")
with c_time2:
    if last_updated is not None and pd.notna(last_updated):
        st.caption(f"🗄️ Última lectura: **{last_updated.strftime('%Y-%m-%d %H:%M:%S')}**")
    else:
        st.caption("🗄️ Última lectura: **—**")

# ---------------- VALIDACIONES ----------------
missing = [c for c in [COL_OS_DB, COL_FECHA_DB] if c not in df.columns]
if missing:
    st.error(f"Faltan columnas en Supabase: {missing}")
    st.stop()

if df.empty:
    st.warning("Supabase respondió OK, pero no hay filas en la tabla todavía.")
    st.stop()

# ---------------- FECHAS (✅ FIX DEFINITIVO) ----------------
# Parse normal (puede venir naive o tz-aware con -03:00)
dt = pd.to_datetime(df[COL_FECHA_DB], errors="coerce")

# Si viene tz-aware (ej: 05:00-03:00), lo pasamos a UTC (08:00+00) y quitamos tz => 08:00
try:
    if getattr(dt.dt, "tz", None) is not None:
        dt = dt.dt.tz_convert("UTC").dt.tz_localize(None)
except Exception:
    pass

df[COL_FECHA_DB] = dt
df["fecha_programacion_display"] = df[COL_FECHA_DB].dt.strftime("%Y-%m-%d %H:%M:%S").astype(str)

# now para cálculos (Chile naive)
now = datetime.now(ZoneInfo("America/Santiago")).replace(tzinfo=None)

def human_diff(target_dt: datetime):
    diff_seconds = int((now - target_dt).total_seconds())

    if diff_seconds >= 0:
        estado = "VENCIDO"
        s = diff_seconds
        h, r = divmod(s, 3600)
        m, s = divmod(r, 60)
        detalle = f"Lleva vencido {h}h {m}m {s}s"
    else:
        s_left = abs(diff_seconds)
        h, r = divmod(s_left, 3600)
        m, s = divmod(r, 60)

        if s_left <= 1800:  # < 30m
            estado = "URGENTE"
            detalle = f"⚠️ Faltan {h}h {m}m {s}s"
        else:
            estado = "POR VENCER"
            detalle = f"Faltan {h}h {m}m {s}s"

    return estado, detalle

estados, detalles = [], []
for dtx in df[COL_FECHA_DB]:
    if pd.isna(dtx):
        estados.append("SIN FECHA")
        detalles.append("—")
    else:
        est, det = human_diff(dtx)
        estados.append(est)
        detalles.append(det)

df["EstadoTiempo"] = estados
df["DetalleTiempo"] = detalles

# ---------------- KPIs ----------------
vencidos = int((df["EstadoTiempo"] == "VENCIDO").sum())
urgentes = int((df["EstadoTiempo"] == "URGENTE").sum())
por_vencer = int((df["EstadoTiempo"] == "POR VENCER").sum())

c1, c2, c3 = st.columns(3)
with c1:
    st.markdown(
        f"""
    <div class="kpi-card" style="background:rgba(255,0,0,0.12); border-left:8px solid red;">
        <div class="kpi-title">Vencidos</div>
        <div class="kpi-value" style="color:red;">{vencidos}</div>
    </div>
    """,
        unsafe_allow_html=True,
    )
with c2:
    st.markdown(
        f"""
    <div class="kpi-card" style="background:rgba(255,165,0,0.18); border-left:8px solid orange;">
        <div class="kpi-title">Urgentes (&lt;30m)</div>
        <div class="kpi-value" style="color:orange;">{urgentes}</div>
    </div>
    """,
        unsafe_allow_html=True,
    )
with c3:
    st.markdown(
        f"""
    <div class="kpi-card" style="background:rgba(255,241,118,0.20); border-left:8px solid #FFF176;">
        <div class="kpi-title">Por vencer</div>
        <div class="kpi-value" style="color:#FFF176;">{por_vencer}</div>
    </div>
    """,
        unsafe_allow_html=True,
    )

st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

# ---------------- GRÁFICO DE ANILLOS POR ESTADO (EN EXPANDER) ----------------

dist_estado = (
    df.groupby("EstadoTiempo")
      .size()
      .reset_index(name="cantidad")
)

# Calcular porcentaje
total = dist_estado["cantidad"].sum()
dist_estado["porcentaje"] = (dist_estado["cantidad"] / total * 100).round(1)

# Colores personalizados
color_scale = alt.Scale(
    domain=["VENCIDO", "URGENTE", "POR VENCER"],
    range=["#FF5252", "#FFA500", "#FFF176"]
)

donut_chart = (
    alt.Chart(dist_estado)
    .mark_arc(innerRadius=60)
    .encode(
        theta="cantidad:Q",
        color=alt.Color(
            "EstadoTiempo:N",
            scale=color_scale,
            legend=alt.Legend(
                title="EstadoTiempo",
                titleFontWeight="bold",
                labelFontWeight="normal"
            )
        ),
        tooltip=[
            alt.Tooltip("EstadoTiempo:N", title="Estado"),
            alt.Tooltip("cantidad:Q", title="Cantidad"),
            alt.Tooltip("porcentaje:Q", title="Porcentaje (%)")
        ]
    )
    .properties(height=420)
)

with st.expander("Gráfico de casos por estado"):
    st.subheader("Distribución de casos por estado")
    st.altair_chart(donut_chart, use_container_width=True)

st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

# ---------------- ORDEN ----------------
order_map = {"VENCIDO": 0, "URGENTE": 1, "POR VENCER": 2, "SIN FECHA": 3}
df["_ord"] = df["EstadoTiempo"].map(order_map).fillna(99)
df = df.sort_values(by=["_ord", COL_FECHA_DB]).drop(columns=["_ord"])

# ---------------- PARPADEO (URGENTE <30m) ----------------
blink_on = (datetime.now(ZoneInfo("America/Santiago")).second % 2 == 0)

# Tabla base
tabla = df[[COL_OS_DB, "fecha_programacion_display", "EstadoTiempo", "DetalleTiempo"]].copy()
tabla = tabla.rename(
    columns={
        COL_OS_DB: "O/S",
        "fecha_programacion_display": "Fecha Programación de servicio",
    }
).reset_index(drop=True)

# ---------------- ICONO DE RIESGO ----------------
def icono_estado(est):
    if est == "VENCIDO":
        return "🔴"
    if est == "URGENTE":
        return "🟠"
    if est == "POR VENCER":
        return "🟡"
    return "⚪"

tabla["Riesgo"] = tabla["EstadoTiempo"].apply(icono_estado)

# Reordenar columnas para que Riesgo vaya primero
tabla = tabla[["Riesgo", "O/S", "Fecha Programación de servicio", "EstadoTiempo", "DetalleTiempo"]]

def style_row(row):
    styles = [""] * len(row)
    idx_estado = row.index.get_loc("EstadoTiempo")
    idx_detalle = row.index.get_loc("DetalleTiempo")
    idx_riesgo = row.index.get_loc("Riesgo")

    if row["EstadoTiempo"] == "VENCIDO":
        styles[idx_estado] = "color:red; font-weight:900;"
        styles[idx_riesgo] = "font-size:20px;"  # icono grande
    elif row["EstadoTiempo"] == "URGENTE":
        if blink_on:
            styles[idx_estado] = "color:orange; font-weight:900;"
            styles[idx_detalle] = "color:orange; font-weight:800;"
            styles[idx_riesgo] = "font-size:20px;"
        else:
            styles[idx_estado] = "color:rgba(255,165,0,0.25); font-weight:900;"
            styles[idx_detalle] = "color:rgba(255,165,0,0.25); font-weight:800;"
            styles[idx_riesgo] = "font-size:20px; opacity:0.25;"
    elif row["EstadoTiempo"] == "POR VENCER":
        styles[idx_estado] = "color:#FFF176; font-weight:900;"
        styles[idx_riesgo] = "font-size:20px;"

    return styles

styled_df = tabla.style.apply(style_row, axis=1)
st.dataframe(styled_df, use_container_width=True, hide_index=True, height=720)
