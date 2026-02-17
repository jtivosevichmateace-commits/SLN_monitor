import os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
from playwright.sync_api import sync_playwright
from supabase import create_client
from datetime import timedelta

# =========================
# Helpers
# =========================
def require_env(name: str) -> str:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        raise RuntimeError(f"Falta variable de entorno: {name}")
    return v.strip()

TZ_CL = ZoneInfo("America/Santiago")

# =========================
# SLN CONFIG
# =========================
SLN_URL = "https://sistemalogistico.dycsa.cl"
SLN_USER = require_env("SLN_USER")
SLN_HTTP_USER = require_env("SLN_HTTP_USER")
SLN_HTTP_PASS = require_env("SLN_HTTP_PASS")

OPTION_TIPO_FECHA = "Fecha Programación de servicio"

# CSV
COL_OS = "O/S"
COL_FECHA = "Fecha Programación de servicio"

# =========================
# SUPABASE CONFIG
# =========================
SUPABASE_URL = require_env("SUPABASE_URL")
SUPABASE_KEY = require_env("SUPABASE_SECRET")  # secret/service role
SUPABASE_TABLE = "programacion_transporte"
SUPABASE_RPC_DELETE = "delete_programacion_by_day"  # RPC en Supabase


def limpiar_carpeta(ruta: Path):
    print("[BOT] Limpiando carpeta de descargas...")
    ruta.mkdir(parents=True, exist_ok=True)
    for archivo in ruta.glob("*"):
        try:
            archivo.unlink()
        except Exception as e:
            print(f"[WARN] No se pudo borrar {archivo}: {e}")


def set_fecha_mask(locator, digits: str):
    locator.click()
    locator.press("Control+A")
    locator.type(digits, delay=60)
    locator.press("Tab")


def select_tipo_fecha_with_scroll(page, option_text: str, max_scrolls: int = 60):
    opt = page.get_by_role("option", name=option_text, exact=False)
    if opt.count() > 0 and opt.first.is_visible():
        opt.first.click()
        return

    listbox = page.locator("[role='listbox']:visible").last

    for _ in range(max_scrolls):
        opt = page.get_by_role("option", name=option_text, exact=False)
        if opt.count() > 0 and opt.first.is_visible():
            opt.first.click()
            return
        listbox.hover()
        page.mouse.wheel(0, 400)
        page.wait_for_timeout(120)

    raise RuntimeError(f"No se encontró '{option_text}' tras scrollear el dropdown.")


def debug_dump(page, tag="debug"):
    os.makedirs("debug", exist_ok=True)
    print(f"[DEBUG] URL actual: {page.url}")
    page.screenshot(path=f"debug/{tag}.png", full_page=True)
    with open(f"debug/{tag}.html", "w", encoding="utf-8") as f:
        f.write(page.content())
    print(f"[DEBUG] Dump guardado: debug/{tag}.png y debug/{tag}.html")


def download_csv_from_sln(download_dir: Path) -> Path:
    limpiar_carpeta(download_dir)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--start-maximized"],
        )

        # ⚠️ importante: fuerza timezone del navegador también
        context = browser.new_context(
            http_credentials={"username": SLN_HTTP_USER, "password": SLN_HTTP_PASS},
            ignore_https_errors=True,
            accept_downloads=True,
            timezone_id="America/Santiago",
            locale="es-CL",
        )

        page = context.new_page()

        print("[BOT] Abriendo SLN...")
        page.goto(SLN_URL, timeout=60_000)

        page.get_by_placeholder("Usuario").fill(SLN_USER)
        page.get_by_role("button", name="Continuar").click()
        page.wait_for_load_state("networkidle", timeout=60_000)

        print("[BOT] Navegando a Programación de Transporte...")
        debug_dump(page, "antes_click_operaciones")

        page.get_by_text("Operaciones", exact=False).first.click(timeout=60_000)
        page.get_by_text("Programación de Transporte", exact=False).first.click(timeout=60_000)
        page.get_by_text("Programación de Transporte", exact=False).first.wait_for(timeout=60_000)

        # HOY CHILE para setear filtro SLN
        hoy_cl = datetime.now(TZ_CL)
        fecha_hoy_digits = hoy_cl.strftime("%d%m%Y")
        print(f"[BOT] Fecha Chile usada: {hoy_cl.strftime('%Y-%m-%d %H:%M:%S')} ({fecha_hoy_digits})")

        print("[BOT] Seteando Fecha Desde (hoy)...")
        set_fecha_mask(page.get_by_placeholder("dd-MM-yyyy").nth(0), fecha_hoy_digits)

        print("[BOT] Seteando Fecha Hasta (hoy)...")
        set_fecha_mask(page.get_by_placeholder("dd-MM-yyyy").nth(1), fecha_hoy_digits)

        # Verifica qué quedó en los inputs
        desde_val = page.get_by_placeholder("dd-MM-yyyy").nth(0).input_value()
        hasta_val = page.get_by_placeholder("dd-MM-yyyy").nth(1).input_value()
        print(f"[BOT] Inputs SLN quedaron: Desde={desde_val} | Hasta={hasta_val}")

        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        print("[BOT] Seleccionando Tipo Fecha...")
        try:
            bloque = page.locator(":has-text('Tipo Fecha:')").first
            combo = bloque.locator("span[role='listbox']").first
            combo.click(force=True)
        except Exception:
            page.locator("[role='listbox']").first.click(force=True)

        select_tipo_fecha_with_scroll(page, OPTION_TIPO_FECHA)

        print("[BOT] Buscando...")
        page.get_by_text("Buscar", exact=True).click()

        print("[BOT] Descargando CSV...")
        with page.expect_download(timeout=120_000) as d:
            try:
                page.locator("button.btn.btn-outline-success").click()
            except Exception:
                page.get_by_text("Exportar a Excel", exact=False).click()

        download = d.value
        ext = os.path.splitext(download.suggested_filename)[1].lower()
        final_path = download_dir / f"ProgramacionDeTransporte{ext}"
        download.save_as(str(final_path))

        print(f"[BOT] ✅ Archivo guardado como: {final_path}")

        context.close()
        browser.close()

    if ext != ".csv":
        raise RuntimeError(f"Se descargó {ext} y se esperaba .csv.")
    return final_path


def upload_to_supabase(csv_path: Path):
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    print(f"[CSV] Leyendo: {csv_path}")
    df = pd.read_csv(csv_path, sep=";", encoding="utf-8-sig")
    print(f"[CSV] Filas CSV: {len(df)}")

    for col in (COL_OS, COL_FECHA):
        if col not in df.columns:
            raise RuntimeError(f"Falta columna '{col}'. Columnas: {list(df.columns)}")

    # Parse fecha SLN
    df[COL_FECHA] = pd.to_datetime(df[COL_FECHA], errors="coerce", dayfirst=True)

    df2 = df[[COL_OS, COL_FECHA]].dropna().copy()
    df2[COL_OS] = df2[COL_OS].astype(str).str.strip()
    print(f"[CSV] Filas válidas (OS+Fecha): {len(df2)}")

    if df2.empty:
        print("[CSV] Nada que subir (sin filas válidas).")
        return

    # ✅ Día objetivo: HOY Chile
    target_day = datetime.now(TZ_CL).date()

    # Día detectado en CSV
    day_csv = df2[COL_FECHA].dt.date.mode().iloc[0]
    print(f"[SUPABASE] Día detectado desde CSV: {day_csv} | Día objetivo (Chile): {target_day}")

    # ✅ Si viene corrido, ajustamos al día objetivo
    if day_csv != target_day:
        shift_days = (target_day - day_csv).days
        print(f"[SUPABASE] ⚠️ CSV viene corrido. Aplicando shift: {shift_days} día(s)")
        df2[COL_FECHA] = df2[COL_FECHA] + pd.to_timedelta(shift_days, unit="D")

    print("[CSV] Rango final fechas:", df2[COL_FECHA].min(), "->", df2[COL_FECHA].max())
    print("[CSV] Día final (mode):", df2[COL_FECHA].dt.date.mode().iloc[0])

    # timestamp WITHOUT tz => string sin offset
    df2["fecha_programacion_str"] = df2[COL_FECHA].dt.strftime("%Y-%m-%d %H:%M:%S")

    # updated_at en Chile
    updated_at = datetime.now(TZ_CL).strftime("%Y-%m-%d %H:%M:%S")

    rows = [
        {"os": r[COL_OS], "fecha_programacion": r["fecha_programacion_str"], "updated_at": updated_at}
        for _, r in df2.iterrows()
    ]

    print(f"[SUPABASE] Filas listas para insertar: {len(rows)}")

    # ✅ BORRAR HOY por rango [00:00, 00:00+1día)
    start_dt_obj = datetime.combine(target_day, datetime.min.time())
    end_dt_obj = start_dt_obj + timedelta(days=1)
    start_dt = start_dt_obj.strftime("%Y-%m-%d %H:%M:%S")
    end_dt = end_dt_obj.strftime("%Y-%m-%d %H:%M:%S")

    print(f"[SUPABASE] Borrando rango HOY: [{start_dt}, {end_dt}) ...")

    del_res = (
        supabase
        .table(SUPABASE_TABLE)
        .delete()
        .gte("fecha_programacion", start_dt)
        .lt("fecha_programacion", end_dt)
        .execute()
    )
    if getattr(del_res, "error", None):
        raise RuntimeError(del_res.error)

    deleted_count = len(del_res.data or [])
    print(f"[SUPABASE] Filas borradas (returning): {len(del_res.data or [])}")

    # Insert por lotes
    print("[SUPABASE] Insertando filas del día...")
    BATCH = 500
    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        ins_res = supabase.table(SUPABASE_TABLE).insert(chunk).execute()
        if getattr(ins_res, "error", None):
            raise RuntimeError(ins_res.error)

    print("[SUPABASE] ✅ Subida OK (delete rango HOY + insert)")



def main():
    print("[BOT] Iniciando uploader SLN -> Supabase...")
    base_dir = Path(__file__).resolve().parent
    download_dir = base_dir / "downloads"

    csv_path = download_csv_from_sln(download_dir)
    upload_to_supabase(csv_path)


if __name__ == "__main__":
    main()


