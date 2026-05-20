"""
Sonix.ai bulk transcription scraper.

Pipeline (orquestado por `process_files`):

    0. Pre-filtro local: descarta archivos cuyo {stem}.txt ya existe en OUTPUT_FOLDER.
    1. Login (sesion con cookies en el WebDriver).
    2. Phase 1 - Upload bulk: navega a /upload?folder_id=X UNA vez y envia todos los
       archivos al input multi-file con send_keys("\\n".join(paths)). Espera a que
       aparezcan N indicadores "100% Uploaded".
    3. Phase 2 - Click unico en el boton "TRANSCRIBE IN SPANISH" (chequea
       disabled/aria-disabled antes de hacer click).
    4. Phase 3 - Polling del folder view (/f/{folder_id}) refrescando hasta que el
       badge de cada fila pase de "Transcribing" a "Transcribed". Recolecta el
       recording_id de cada fila (ancestro con UN solo link a /recordings/).
    5. Phase 4 - Disparo BULK de POST /export.json para todos los recordings en
       paralelo (un solo execute_async_script con Promise.all). Luego, por cada
       recording_id, polling con backoff de GET /export?key=... hasta status=
       "completed" + url de S3, GET a S3 y guardado del .txt.

Variables de entorno requeridas (cargar desde .env):
    SONIX_EMAIL, SONIX_PASSWORD, SONIX_FOLDER_ID, SONIX_INPUT_FOLDER, SONIX_OUTPUT_FOLDER

Uso:
    python scriptPerPage/sonix/scraper.py
"""

import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import (
    NoAlertPresentException,
    NoSuchElementException,
    TimeoutException,
    UnexpectedAlertPresentException,
)
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

load_dotenv()

# --- URLs de Sonix -----------------------------------------------------------
LOGIN_URL          = "https://sonix.ai/accounts/sign_in"
UPLOAD_URL         = "https://my.sonix.ai/upload?folder_id={}"
RECORDING_URL      = "https://my.sonix.ai/recordings/{}"
EXPORT_TRIGGER_URL = "https://my.sonix.ai/recordings/{}/export.json"
EXPORT_FETCH_URL   = "https://my.sonix.ai/recordings/{rid}/export?key=exports.{rid}.0.txt"

# --- Configuracion (env) -----------------------------------------------------
EMAIL         = os.getenv("SONIX_EMAIL")
PASSWORD      = os.getenv("SONIX_PASSWORD")
FOLDER_ID     = os.getenv("SONIX_FOLDER_ID", "o9yKNWDo")
INPUT_FOLDER  = os.getenv("SONIX_INPUT_FOLDER", "./input_files")
OUTPUT_FOLDER = os.getenv("SONIX_OUTPUT_FOLDER", "./transcriptions")

# --- Timeouts y polling (segundos) -------------------------------------------
LOGIN_TIMEOUT_S              = 20
UPLOAD_TIMEOUT_S             = 600
TRANSCRIBE_BUTTON_TIMEOUT_S  = 180
TRANSCRIBED_WAIT_TIMEOUT_S   = 1800
TRANSCRIBED_POLL_INTERVAL_S  = 15
EXPORT_GENERATION_TIMEOUT_S  = 600
# Backoff exponencial tipo Fibonacci; tras agotar la lista mantiene 30s.
EXPORT_POLL_DELAYS_S         = [2, 2, 3, 5, 8, 13, 20, 30]
BULK_SCRIPT_TIMEOUT_S        = 120

# --- XPaths reutilizables ----------------------------------------------------
RECORDING_LINK_XPATH = "//a[contains(@href, '/recordings/')]"
# Ancestro de un link de recording que cumple: contiene UN solo link a /recordings/
# Y un badge de estado. Identifica la fila exacta sin abarcar tabla entera o filas vecinas.
ROW_WITH_BADGE_XPATH = (
    "./ancestor::*[count(.//a[contains(@href, '/recordings/')])=1"
    " and (contains(., 'Transcribed') or contains(., 'Transcribing')"
    " or contains(., 'Duplicate') or contains(., 'Failed'))][1]"
)

# --- Tipos de archivo aceptados ----------------------------------------------
MEDIA_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".mp4", ".mov", ".avi",
    ".mkv", ".flac", ".ogg", ".aac", ".wma",
}

# --- Payload del POST /export.json (replica fielmente al frontend) -----------
EXPORT_TXT_PAYLOAD = {
    "fcpxml_titles": False,
    "file_format": "txt",
    "highlights_only": False,
    "include_voice_closing_tag": True,
    "language": "es",
    "limit_captions": True,
    "line_numbers": False,
    "max_characters": 45,
    "max_duration": 10,
    "pastable_format": False,
    "remove_strikethrough": True,
    "speaker_colors": True,
    "speaker_display": "none",
    "speaker_every_subtitle": True,
    "speaker_names": True,
    "subtitle_lines": 2,
    "timestamps": True,
    "timestamps_before_speaker": False,
    "timestamps_detail": False,
    "timestamps_interval": False,
    "timestamps_interval_seconds": 30,
    "translated": False,
    "use_timecode": False,
    "word_by_word_timecodes": False,
}


def _xpath_lower(text_expr: str = ".") -> str:
    """Helper para XPath case-insensitive: translate(text_expr, A-Z, a-z)."""
    return (
        f"translate({text_expr}, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
        f" 'abcdefghijklmnopqrstuvwxyz')"
    )


# --- Driver y login ----------------------------------------------------------

def build_driver(headless: bool = False) -> webdriver.Chrome:
    """Construye un Chrome WebDriver con flags anti-deteccion de automatizacion."""
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    return webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options,
    )


def login(driver: webdriver.Chrome) -> None:
    """Hace login con email/password del .env y espera a que cambie la URL."""
    driver.get(LOGIN_URL)
    wait = WebDriverWait(driver, LOGIN_TIMEOUT_S)

    email_field = wait.until(EC.presence_of_element_located(
        (By.CSS_SELECTOR, "input[type='email'], input[name*='email'], #user_email")
    ))
    email_field.clear()
    email_field.send_keys(EMAIL)

    pw_field = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
    pw_field.clear()
    pw_field.send_keys(PASSWORD)

    driver.find_element(
        By.CSS_SELECTOR, "input[type='submit'], button[type='submit']"
    ).click()

    wait.until(lambda d: "sign_in" not in d.current_url)
    print(f"[Login] Sesion iniciada como {EMAIL}")


# --- Utilidades --------------------------------------------------------------

def _dismiss_alert_if_present(driver: webdriver.Chrome) -> None:
    """Descarta cualquier alert nativo pendiente (ej. confirm() al salir de upload)."""
    try:
        alert = driver.switch_to.alert
        text = alert.text
        alert.dismiss()
        print(f"[Alert] Descartada: {text}")
    except NoAlertPresentException:
        pass


def _href_to_id(href: str) -> str:
    """Extrae el recording_id de un href tipo '/recordings/{id}' (con o sin query)."""
    return href.rstrip("/").split("/recordings/")[-1].split("/")[0].split("?")[0]


# --- Phase 1: Upload bulk ----------------------------------------------------

def upload_all_files(driver: webdriver.Chrome, file_paths: list) -> None:
    """Sube todos los archivos al folder en una sola interaccion con el input multi-file.

    Hace `send_keys("\\n".join(paths))` sobre el `<input type="file" multiple>` y
    espera a que aparezcan N indicadores "100% Uploaded" antes de retornar.
    """
    driver.get(UPLOAD_URL.format(FOLDER_ID))

    file_input = WebDriverWait(driver, 30).until(EC.presence_of_element_located(
        (By.CSS_SELECTOR, "input[type='file']")
    ))
    # El input puede estar oculto por CSS; forzar visibilidad para send_keys.
    driver.execute_script(
        "arguments[0].style.display='block'; arguments[0].style.opacity='1';", file_input
    )

    abs_paths = [str(Path(p).resolve()) for p in file_paths]
    file_input.send_keys("\n".join(abs_paths))
    print(f"[Upload] Enviados {len(abs_paths)} archivo(s) al input multi-file")

    expected = len(file_paths)
    indicator_xpath = f"//*[contains({_xpath_lower()}, '100% uploaded')]"

    WebDriverWait(driver, UPLOAD_TIMEOUT_S).until(
        lambda d: len(d.find_elements(By.XPATH, indicator_xpath)) >= expected
    )
    print(f"[Upload] {expected} archivo(s) al 100%")


# --- Phase 2: Click TRANSCRIBE IN SPANISH ------------------------------------

def click_transcribe_in_spanish_bulk(driver: webdriver.Chrome) -> None:
    """Click en el boton grande 'TRANSCRIBE IN SPANISH' (deshabilitado hasta 100%)."""
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(1)

    candidate_xpath = (
        f"//*[self::button or self::a or @role='button']"
        f"[contains({_xpath_lower()}, 'transcribe in spanish')]"
    )

    def _find_enabled(d):
        for c in d.find_elements(By.XPATH, candidate_xpath):
            if c.get_attribute("disabled"):
                continue
            if (c.get_attribute("aria-disabled") or "").lower() == "true":
                continue
            if "disabled" in (c.get_attribute("class") or "").lower():
                continue
            try:
                if c.is_displayed() and c.is_enabled():
                    return c
            except Exception:
                continue
        return False

    try:
        btn = WebDriverWait(driver, TRANSCRIBE_BUTTON_TIMEOUT_S).until(_find_enabled)
    except TimeoutException:
        debug_path = Path(OUTPUT_FOLDER) / "_debug_upload_page.html"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text(driver.page_source, encoding="utf-8")
        print(f"[Debug] HTML guardado en {debug_path}")
        raise RuntimeError("No se encontro el boton TRANSCRIBE IN SPANISH habilitado")

    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
    time.sleep(0.5)
    try:
        btn.click()
    except Exception:
        driver.execute_script("arguments[0].click();", btn)
    print("[Transcribe] Click en TRANSCRIBE IN SPANISH")


# --- Phase 3: Esperar Transcribed en folder view -----------------------------

def _scan_folder_rows(driver: webdriver.Chrome) -> dict:
    """Escanea todos los links de recordings del folder en UNA pasada.

    Devuelve `{filename_text: [(recording_id, row_text_lower), ...]}` ya con la
    fila resuelta (ancestro mas chico con badge de estado). Una sola pasada
    evita N busquedas XPath por nombre.
    """
    rows_by_name = {}
    for link in driver.find_elements(By.XPATH, RECORDING_LINK_XPATH):
        link_text = (link.text or "").strip()
        if not link_text:
            continue
        rid = _href_to_id(link.get_attribute("href") or "")
        if not rid:
            continue
        row_text = link_text.lower()
        try:
            row = link.find_element(By.XPATH, ROW_WITH_BADGE_XPATH)
            row_text = (row.text or row_text).lower()
        except NoSuchElementException:
            pass
        rows_by_name.setdefault(link_text, []).append((rid, row_text))
    return rows_by_name


def wait_until_all_transcribed(
    driver: webdriver.Chrome,
    file_paths: list,
    timeout: int = TRANSCRIBED_WAIT_TIMEOUT_S,
) -> dict:
    """Polling hasta que cada archivo subido aparezca como Transcribed en el folder.

    Devuelve `{stem: [recording_id, ...]}` con candidatos ordenados: primero
    Transcribed-no-duplicado, luego Transcribed-con-duplicate o solo-duplicate
    (fallback).
    """
    wait = WebDriverWait(driver, 60)
    wait.until(lambda d: "/f/" in d.current_url)
    wait.until(EC.presence_of_element_located((By.XPATH, RECORDING_LINK_XPATH)))

    expected = {Path(fp).name: Path(fp).stem for fp in file_paths}
    found = {}  # stem -> list[rid]
    elapsed = 0

    while elapsed < timeout and len(found) < len(expected):
        rows_by_name = _scan_folder_rows(driver)  # una sola pasada por todo el folder

        for name, stem in expected.items():
            if stem in found:
                continue
            candidates = []      # priority 0: Transcribed sin Duplicate
            dup_candidates = []  # priority 1: Transcribed con Duplicate o solo Duplicate
            for rid, row_text in rows_by_name.get(name, []):
                if "transcribing" in row_text:
                    continue
                is_transcribed = "transcribed" in row_text
                is_duplicate = "duplicate" in row_text
                if is_transcribed and not is_duplicate:
                    candidates.append(rid)
                elif is_transcribed or is_duplicate:
                    dup_candidates.append(rid)
            ordered = candidates + dup_candidates
            if ordered:
                found[stem] = ordered
                print(f"[Ready] {name} -> {len(ordered)} candidato(s): {ordered[:5]}")

        if len(found) >= len(expected):
            break

        print(f"[Wait] {len(found)}/{len(expected)} transcritos ({elapsed}/{timeout}s)")
        time.sleep(TRANSCRIBED_POLL_INTERVAL_S)
        elapsed += TRANSCRIBED_POLL_INTERVAL_S
        try:
            driver.refresh()
        except UnexpectedAlertPresentException:
            _dismiss_alert_if_present(driver)
            driver.refresh()
        wait.until(EC.presence_of_element_located((By.XPATH, RECORDING_LINK_XPATH)))

    for name, stem in expected.items():
        if stem not in found:
            print(f"[Warning] Timeout esperando transcripcion de '{name}'")
    return found


# --- Phase 4: Generar y descargar el .txt ------------------------------------

# JS para disparar fetches POST a /export.json en paralelo (Promise.all).
# Se ejecuta UNA vez con la lista de recording_ids. La pagina actual provee
# las cookies de sesion y el CSRF token via <meta name="csrf-token">.
_BULK_TRIGGER_JS = """
    var done = arguments[2];
    var ids = arguments[0];
    var payload = arguments[1];
    var csrf = (document.querySelector('meta[name=csrf-token]') || {}).content || '';
    var promises = ids.map(function(id){
        return fetch('/recordings/' + id + '/export.json', {
            method: 'POST',
            credentials: 'include',
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRF-Token': csrf
            },
            body: payload
        }).then(function(r){
            return r.text().then(function(t){
                return {id: id, ok: r.ok, status: r.status, body: t.slice(0, 200)};
            });
        }).catch(function(e){
            return {id: id, ok: false, err: String(e)};
        });
    });
    Promise.all(promises).then(done);
"""


def _trigger_exports_bulk(driver: webdriver.Chrome, recording_ids: list) -> dict:
    """POST `/export.json` para N recordings en paralelo via fetch() + Promise.all.

    No navega entre paginas: usa la pagina actual para CSRF + cookies, y dispara
    todos los POST concurrentemente. Sonix genera los exports en S3 en paralelo
    del lado servidor.

    Devuelve `{recording_id: result_dict}` con el resultado de cada POST.
    """
    if not recording_ids:
        return {}
    driver.set_script_timeout(BULK_SCRIPT_TIMEOUT_S)
    results = driver.execute_async_script(
        _BULK_TRIGGER_JS, list(recording_ids), json.dumps(EXPORT_TXT_PAYLOAD)
    )
    by_id = {}
    for r in results or []:
        rid = r.get("id")
        by_id[rid] = r
        if r.get("ok"):
            print(f"[Export] Encolado {rid}")
        else:
            print(f"[Export] FALLO {rid} status={r.get('status')} body={r.get('body')} err={r.get('err')}")
    return by_id


def _fetch_export_file(driver: webdriver.Chrome, recording_id: str) -> str:
    """Polling con backoff exponencial hasta que Sonix devuelva la URL S3 del .txt.

    Mientras genera: `{"status":"processing","url":""}` -> esperar y reintentar.
    Cuando termina: `{"status":"completed","url":"https://sonixai.s3..."}` ->
    GET a esa URL presigned y retornar el texto.
    """
    cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
    headers = {
        "User-Agent": driver.execute_script("return navigator.userAgent;"),
        "Accept": "text/plain,*/*;q=0.8",
        "Referer": f"https://my.sonix.ai/recordings/{recording_id}",
    }
    url = EXPORT_FETCH_URL.format(rid=recording_id)

    deadline = time.time() + EXPORT_GENERATION_TIMEOUT_S
    attempt = 0
    last_payload = None
    while time.time() < deadline:
        resp = requests.get(url, cookies=cookies, headers=headers, timeout=60)
        ctype = (resp.headers.get("Content-Type") or "").lower()

        if "application/json" in ctype:
            try:
                payload = resp.json()
            except Exception:
                payload = {"_raw": resp.text[:200]}
            last_payload = payload
            status = (payload.get("status") or "").lower()
            file_url = payload.get("url") or ""
            print(f"[Fetch] {recording_id} try={attempt + 1} status={status}")
            if status != "processing" and file_url:
                s3 = requests.get(file_url, timeout=60)
                s3.raise_for_status()
                return s3.text
        elif resp.status_code < 400 and resp.text:
            # Caso raro: Sonix sirvio el .txt directo (sin pasar por S3 redirect).
            return resp.text

        sleep_s = EXPORT_POLL_DELAYS_S[min(attempt, len(EXPORT_POLL_DELAYS_S) - 1)]
        time.sleep(sleep_s)
        attempt += 1

    raise RuntimeError(
        f"Timeout esperando export listo para {recording_id}. Ultimo payload: {last_payload}"
    )


def _save_text(text: str, output_name: str) -> Path:
    """Escribe `text` en OUTPUT_FOLDER/{output_name}.txt y devuelve el Path."""
    target = Path(OUTPUT_FOLDER).resolve() / f"{output_name}.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def download_transcription(
    driver: webdriver.Chrome, recording_id: str, output_name: str
) -> Path:
    """Encola export -> polling -> guardado. Devuelve Path del .txt local.

    Util como entrypoint individual (tests, retry de un solo ID). Para
    procesar muchos archivos a la vez, `_phase4_download` dispara los POST en
    paralelo lo cual es mas eficiente.
    """
    _trigger_exports_bulk(driver, [recording_id])
    text = _fetch_export_file(driver, recording_id)
    return _save_text(text, output_name)


# --- Orquestacion ------------------------------------------------------------

def get_input_files() -> list:
    """Devuelve la lista de paths absolutos de archivos media en INPUT_FOLDER."""
    folder = Path(INPUT_FOLDER)
    folder.mkdir(parents=True, exist_ok=True)
    files = [str(f) for f in folder.iterdir() if f.suffix.lower() in MEDIA_EXTENSIONS]
    print(f"[Input] {len(files)} archivo(s) en '{INPUT_FOLDER}'")
    return files


def split_already_transcribed(file_paths: list) -> tuple:
    """Particiona `file_paths` en (a_subir, ya_transcritos_localmente).

    Un archivo se considera ya transcrito si existe `OUTPUT_FOLDER/{stem}.txt`.
    """
    out_folder = Path(OUTPUT_FOLDER)
    out_folder.mkdir(parents=True, exist_ok=True)

    to_upload, already = [], []
    for fp in file_paths:
        txt_path = out_folder / f"{Path(fp).stem}.txt"
        if txt_path.exists():
            already.append((fp, str(txt_path)))
        else:
            to_upload.append(fp)
    return to_upload, already


def _phase1_upload(driver, to_upload):
    print(f"\n{'=' * 55}")
    print(f"[Phase 1] Upload bulk: {len(to_upload)} archivo(s)")
    upload_all_files(driver, to_upload)


def _phase2_click(driver):
    print(f"\n{'=' * 55}")
    print(f"[Phase 2] Click TRANSCRIBE IN SPANISH (1 click, todos)")
    click_transcribe_in_spanish_bulk(driver)


def _phase3_wait(driver, to_upload):
    print(f"\n{'=' * 55}")
    print(f"[Phase 3] Esperar a que cada fila pase a Transcribed")
    return wait_until_all_transcribed(driver, to_upload)


def _phase4_download(driver, to_upload, name_to_ids, results):
    """Disparo paralelo de exports + descarga secuencial con retry."""
    print(f"\n{'=' * 55}")
    print(f"[Phase 4] Descargar ({len(name_to_ids)} listos)")

    # Pre-disparar TODOS los primeros candidatos en paralelo (1 round-trip al server).
    first_ids = [name_to_ids[Path(fp).stem][0]
                 for fp in to_upload
                 if name_to_ids.get(Path(fp).stem)]
    _trigger_exports_bulk(driver, first_ids)

    triggered = set(first_ids)
    for fp in to_upload:
        stem = Path(fp).stem
        candidates = name_to_ids.get(stem) or []
        if not candidates:
            results[stem] = {"status": "error", "error": "timeout esperando Transcribed"}
            continue

        print(f"[Process] {stem}  ({len(candidates)} candidato(s))")
        success = False
        last_err = None
        for rid in candidates:
            if rid not in triggered:
                # Fallback: el primer candidato fallo, encolar este otro on-demand.
                _trigger_exports_bulk(driver, [rid])
                triggered.add(rid)
            try:
                text = _fetch_export_file(driver, rid)
                target = _save_text(text, stem)
                print(f"[Download] {stem} -> {target}")
                results[stem] = {
                    "status": "success",
                    "recording_id": rid,
                    "output_file": str(target),
                    "text": text,
                }
                success = True
                break
            except Exception as e:
                last_err = e
                print(f"[Retry] {stem} id={rid} fallo: {e}")
                continue
        if not success:
            results[stem] = {"status": "error", "error": str(last_err)}


def process_files(file_paths: list, headless: bool = False) -> dict:
    """Pipeline completo. Devuelve `{stem: {status, output_file, text|error, ...}}`.

    Estados posibles por archivo:
      - "skipped": ya tenia .txt en local, no se subio.
      - "success": subido, transcrito y descargado correctamente.
      - "error":   fallo en alguna fase. Motivo en la clave `error`.
    """
    results = {}
    to_upload, already = split_already_transcribed(file_paths)

    for fp, txt_path in already:
        stem = Path(fp).stem
        print(f"[Skip] '{stem}' ya tiene transcripcion en {txt_path}")
        text = Path(txt_path).read_text(encoding="utf-8")
        results[stem] = {"status": "skipped", "output_file": txt_path, "text": text}

    if not to_upload:
        print("[Done] Todos los archivos ya tenian transcripcion local.")
        return results

    driver = build_driver(headless=headless)
    try:
        login(driver)
        _phase1_upload(driver, to_upload)
        _phase2_click(driver)
        name_to_ids = _phase3_wait(driver, to_upload)
        _phase4_download(driver, to_upload, name_to_ids, results)
    finally:
        driver.quit()

    return results


if __name__ == "__main__":
    files = get_input_files()
    if not files:
        print(
            f"No se encontraron archivos de audio/video en '{INPUT_FOLDER}'.\n"
            f"Agrega archivos con extension: {', '.join(sorted(MEDIA_EXTENSIONS))}"
        )
    else:
        process_files(files)
