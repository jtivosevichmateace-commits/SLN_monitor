import os
from pathlib import Path
from datetime import datetime, timedelta, date

import pandas as pd
from playwright.sync_api import sync_playwright
from supabase import create_client

# =========================
# SLN CONFIG
# =========================
SLN_URL = "https://sistemalogistico.dycsa.cl"
SLN_USER = os.getenv("SLN_USER")
SLN_HTTP_USER = os.getenv("SLN_SLN_HTTP_USER")
SLN_HTTP_PASS = os.getenv("SLN_SLN_HTTP_PASS")  # ideal por env

OPTION_TIPO_FECHA = "Fecha Programación de servicio"

# CSV
COL_OS = "O/S"
COL_FECHA = "Fecha Programación de servicio"

# =========================
# SUPABASE CONFIG
# =========================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET")  # service_role recomendado para borrar/insertar
SUPABASE_TABLE = "programacion_transporte"


# ---------------- Helpers ----------------
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
        page.wait_for_timeout(100)

    raise RuntimeError(f"No se encontró '{option_text}' tras scrollear el dropdown.")


# ---------------- SLN Download ----------------
def download_csv_from_sln(download_dir: Path) -> Path:
    limpiar_carpeta(download_dir)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--start-maximized"])
        context = browser.new_context(
            http_credentials={"username": SLN_HTTP_USER, "password": SLN_HTTP_PASS},
            ignore_https_errors=True,
            accept_downloads=True,
        )
        page = context.new_page()

        print("[BOT] Abriendo SLN...")
        page.goto(SLN_URL, timeout=60_000)

        page.get_by_placeholder("Usuario").fill(SLN_USER)
        page.get_by_role("button", name="Continuar").click()
        page.wait_for_load_state("networkidle", timeout=60_000)

        print("[BOT] Navegando a Programación de Transporte...")

        def debug_dump(page, tag="debug"):
            os.makedirs("debug", exist_ok=True)
            print(f"[DEBUG] URL actual: {page.url}")
            page.screenshot(path=f"debug/{tag}.png", full_page=True)
            html = page.content()
            with open(f"debug/{tag}.html", "w", encoding="utf-8") as f:
                f.write(html)
            print(f"[DEBUG] Dump guardado: debug/{tag}.png y debug/{tag}.html")

        # ... justo antes del click:
        page.wait_for_load_state("networkidle", timeout=60_000)
        debug_dump(page, "antes_click_operaciones")
        
        page.get_by_text("Operaciones", exact=True).click()
        page.get_by_text("Programación de Transporte", exact=True).click()
        page.get_by_text("Programación de Transporte", exact=True).wait_for(timeout=60_000)

        hoy = datetime.now()
        fecha_hoy_digits = hoy.strftime("%d%m%Y")

        print("[BOT] Seteando Fecha Desde (hoy)...")
        set_fecha_mask(page.get_by_placeholder("dd-MM-yyyy").nth(0), fecha_hoy_digits)

        print("[BOT] Seteando Fecha Hasta (hoy)...")
        set_fecha_mask(page.get_by_placeholder("dd-MM-yyyy").nth(1), fecha_hoy_digits)

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
        suggested = download.suggested_filename
        ext = os.path.splitext(suggested)[1].lower()

        final_path = download_dir / f"ProgramacionDeTransporte{ext}"
        download.save_as(str(final_path))

        print(f"[BOT] ✅ Archivo guardado como: {final_path}")

        context.close()
        browser.close()

    if ext != ".csv":
        raise RuntimeError(f"Se descargó {ext} y se esperaba .csv. Ajusta el export si cambió.")
    return final_path


# ---------------- Supabase Upload ----------------
def upload_to_supabase(csv_path: Path):
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Faltan SUPABASE_URL o SUPABASE_SECRET en variables de entorno.")

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    df = pd.read_csv(csv_path, sep=";", encoding="utf-8-sig")

    # Validaciones
    for col in (COL_OS, COL_FECHA):
        if col not in df.columns:
            raise RuntimeError(f"Falta columna '{col}'. Columnas: {list(df.columns)}")

    # Parse fecha (SLN viene dd-MM-yyyy o dd-MM-yyyy HH:mm:ss)
    df[COL_FECHA] = pd.to_datetime(df[COL_FECHA], errors="coerce", dayfirst=True)

    # Quedarse con OS + FECHA, limpiar nulos
    df2 = df[[COL_OS, COL_FECHA]].dropna().copy()
    df2[COL_OS] = df2[COL_OS].astype(str).str.strip()


    # 🚩 Importante: como tu columna en Supabase es timestamp WITHOUT time zone,
    # subimos la fecha como "YYYY-MM-DD HH:MM:SS" (sin Z, sin offset)
    df2["fecha_programacion_str"] = df2[COL_FECHA].dt.strftime("%Y-%m-%d %H:%M:%S")

    updated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    rows = [
        {
            "os": r[COL_OS],
            "fecha_programacion": r["fecha_programacion_str"],
            "updated_at": updated_at,
        }
        for _, r in df2.iterrows()
    ]

    print(f"[SUPABASE] Filas listas para insertar: {len(rows)}")
    if not rows:
        print("[SUPABASE] Nada que subir (no había OS/fecha válidos).")
        return

    # -------------------------
    # ✅ MODO DIARIO (recomendado):
    # Borra el día y vuelve a insertar para que siempre cuadre con el CSV del día.
    # -------------------------
    # Tomamos el día desde las fechas que vienen en el archivo (normalmente hoy)
    day0 = df2[COL_FECHA].dt.date.min()
    day1 = day0 + timedelta(days=1)

    start_str = datetime.combine(day0, datetime.min.time()).strftime("%Y-%m-%d %H:%M:%S")
    end_str = datetime.combine(day1, datetime.min.time()).strftime("%Y-%m-%d %H:%M:%S")

    print(f"[SUPABASE] Borrando filas del rango: [{start_str}, {end_str}) ...")
    del_res = (
        supabase
        .table(SUPABASE_TABLE)
        .delete()
        .gte("fecha_programacion", start_str)
        .lt("fecha_programacion", end_str)
        .execute()
    )
    if getattr(del_res, "error", None):
        raise RuntimeError(del_res.error)

    print("[SUPABASE] Insertando filas del día...")
    # Insert en lotes por si el día viene grande
    BATCH = 500
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i+BATCH]
        ins_res = supabase.table(SUPABASE_TABLE).insert(chunk).execute()
        if getattr(ins_res, "error", None):
            raise RuntimeError(ins_res.error)

    print("[SUPABASE] ✅ Subida OK (modo diario: delete+insert)")


def main():
    
    BASE_DIR = Path(__file__).resolve().parent
    download_dir = BASE_DIR / "downloads"


    csv_path = download_csv_from_sln(download_dir)
    upload_to_supabase(csv_path)


if __name__ == "__main__":
    main()





