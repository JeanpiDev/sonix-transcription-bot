"""
Transcripción de audio/video vía Sonix.ai (scraper Selenium en bloque).

Pipeline (orquestado por `transcribe`):

    0. Cache local: salta archivos cuyo {cache_key}.txt ya existe en
       `sonix_cache_dir` (se reusa el texto, no se llama a Sonix).
    1. Login (sesión con cookies en el WebDriver).
    2. Phase 1 — Upload bulk: navega a /upload?folder_id=X UNA vez y envía todos
       los archivos al input multi-file. Espera N indicadores "100% Uploaded".
    3. Phase 2 — Click único en "TRANSCRIBE IN SPANISH".
    4. Phase 3 — Sondeo del folder view hasta que cada fila pase de
       "Transcribing" a "Transcribed". Recolecta el recording_id de cada fila.
    5. Phase 4 — Disparo BULK de POST /export.json en paralelo (Promise.all),
       sondeo con backoff de GET /export?key=... hasta status="completed" + url
       de S3, GET a S3 y guardado del .txt en cache.

Es 100% síncrono y bloqueante: invocarlo desde código async vía
`asyncio.to_thread` o `run_in_threadpool`, serializado para no abrir varios
Chrome contra la misma cuenta de Sonix a la vez.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path

import requests
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

logger = logging.getLogger("sonix")

# --- URLs de Sonix -----------------------------------------------------------
LOGIN_URL          = "https://sonix.ai/accounts/sign_in"
UPLOAD_URL         = "https://my.sonix.ai/upload?folder_id={}"
FOLDER_VIEW_URL    = "https://my.sonix.ai/f/{}"
EXPORT_FETCH_URL   = "https://my.sonix.ai/recordings/{rid}/export?key=exports.{rid}.0.txt"

# --- Timeouts y sondeo (segundos) --------------------------------------------
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

def build_driver(headless: bool = True) -> webdriver.Chrome:
    """Construye un Chrome/Chromium WebDriver con flags anti-detección.

    En contenedor usa el Chromium/Chromedriver del sistema vía las env
    `CHROME_BIN`/`CHROMEDRIVER_PATH` (las fija el Dockerfile). En dev local
    (sin esas env) cae a `webdriver-manager`, que descarga el ChromeDriver
    compatible con el Chrome instalado.
    """
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])

    chrome_bin = os.getenv("CHROME_BIN")
    if chrome_bin:
        options.binary_location = chrome_bin

    driver_path = os.getenv("CHROMEDRIVER_PATH")
    if driver_path:
        service = Service(driver_path)
    else:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())

    return webdriver.Chrome(service=service, options=options)


def login(driver: webdriver.Chrome, email: str, password: str) -> None:
    """Hace login con email/password y espera a que cambie la URL."""
    driver.get(LOGIN_URL)
    wait = WebDriverWait(driver, LOGIN_TIMEOUT_S)

    email_field = wait.until(EC.presence_of_element_located(
        (By.CSS_SELECTOR, "input[type='email'], input[name*='email'], #user_email")
    ))
    email_field.clear()
    email_field.send_keys(email)

    pw_field = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
    pw_field.clear()
    pw_field.send_keys(password)

    driver.find_element(
        By.CSS_SELECTOR, "input[type='submit'], button[type='submit']"
    ).click()

    wait.until(lambda d: "sign_in" not in d.current_url)
    logger.info("[Login] Sesión iniciada como %s", email)


# --- Utilidades --------------------------------------------------------------

def _dismiss_alert_if_present(driver: webdriver.Chrome) -> None:
    """Descarta cualquier alert nativo pendiente (ej. confirm() al salir de upload)."""
    try:
        alert = driver.switch_to.alert
        text = alert.text
        alert.dismiss()
        logger.info("[Alert] Descartada: %s", text)
    except NoAlertPresentException:
        pass


def _href_to_id(href: str) -> str:
    """Extrae el recording_id de un href tipo '/recordings/{id}' (con o sin query)."""
    return href.rstrip("/").split("/recordings/")[-1].split("/")[0].split("?")[0]


# --- Phase 1: Upload bulk ----------------------------------------------------

def upload_all_files(driver: webdriver.Chrome, folder_id: str, file_paths: list) -> None:
    """Sube todos los archivos al folder en una sola interacción con el input multi-file."""
    driver.get(UPLOAD_URL.format(folder_id))

    file_input = WebDriverWait(driver, 30).until(EC.presence_of_element_located(
        (By.CSS_SELECTOR, "input[type='file']")
    ))
    # El input puede estar oculto por CSS; forzar visibilidad para send_keys.
    driver.execute_script(
        "arguments[0].style.display='block'; arguments[0].style.opacity='1';", file_input
    )

    abs_paths = [str(Path(p).resolve()) for p in file_paths]
    file_input.send_keys("\n".join(abs_paths))
    logger.info("[Upload] Enviados %d archivo(s) al input multi-file", len(abs_paths))

    expected = len(file_paths)
    indicator_xpath = f"//*[contains({_xpath_lower()}, '100% uploaded')]"

    WebDriverWait(driver, UPLOAD_TIMEOUT_S).until(
        lambda d: len(d.find_elements(By.XPATH, indicator_xpath)) >= expected
    )
    logger.info("[Upload] %d archivo(s) al 100%%", expected)


# --- Phase 2: Click TRANSCRIBE IN SPANISH ------------------------------------

def click_transcribe_in_spanish_bulk(driver: webdriver.Chrome) -> None:
    """Click en el botón grande 'TRANSCRIBE IN SPANISH' (deshabilitado hasta 100%)."""
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
        logger.error("[Transcribe] No se encontró el botón TRANSCRIBE IN SPANISH habilitado")
        raise RuntimeError("TRANSCRIPTION_ERROR")

    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
    time.sleep(0.5)
    try:
        btn.click()
    except Exception:
        driver.execute_script("arguments[0].click();", btn)
    logger.info("[Transcribe] Click en TRANSCRIBE IN SPANISH")


# --- Phase 3: Esperar Transcribed en folder view -----------------------------

def _scan_folder_rows(driver: webdriver.Chrome, wanted_names: set | None = None) -> dict:
    """Escanea los links de recordings del folder en UNA pasada.

    Devuelve `{filename_text: [(recording_id, row_text_lower), ...]}` ya con la
    fila resuelta (ancestro más chico con badge de estado).

    `wanted_names` acota el escaneo a los archivos de esta sesión: los links
    cuyo texto no esté en el set se saltan ANTES de la costosa resolución del
    ancestro. Así el costo por sondeo es proporcional a los archivos subidos, no
    al tamaño total del folder (que crece sin límite). Si es None, escanea todo.
    """
    rows_by_name = {}
    for link in driver.find_elements(By.XPATH, RECORDING_LINK_XPATH):
        link_text = (link.text or "").strip()
        if not link_text:
            continue
        if wanted_names is not None and link_text not in wanted_names:
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
    """Sondeo hasta que cada archivo subido aparezca como Transcribed en el folder.

    Devuelve `{stem: [recording_id, ...]}` con candidatos ordenados: primero
    Transcribed-no-duplicado, luego Transcribed-con-duplicate o solo-duplicate.
    """
    wait = WebDriverWait(driver, 60)
    wait.until(lambda d: "/f/" in d.current_url)
    wait.until(EC.presence_of_element_located((By.XPATH, RECORDING_LINK_XPATH)))

    expected = {Path(fp).name: Path(fp).stem for fp in file_paths}
    wanted_names = set(expected.keys())
    found = {}  # stem -> list[rid]
    elapsed = 0

    while elapsed < timeout and len(found) < len(expected):
        # Acotado a los archivos de esta sesión (no a todo el folder).
        rows_by_name = _scan_folder_rows(driver, wanted_names)

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
                logger.info("[Ready] %s -> %d candidato(s): %s", name, len(ordered), ordered[:5])

        if len(found) >= len(expected):
            break

        logger.info("[Wait] %d/%d transcritos (%d/%ds)", len(found), len(expected), elapsed, timeout)
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
            logger.warning("[Warning] Timeout esperando transcripción de '%s'", name)
    return found


# --- Phase 4: Generar y descargar el .txt ------------------------------------

# JS para disparar fetches POST a /export.json en paralelo (Promise.all).
# La página actual provee las cookies de sesión y el CSRF token.
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
    """POST `/export.json` para N recordings en paralelo via fetch() + Promise.all."""
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
            logger.info("[Export] Encolado %s", rid)
        else:
            logger.warning(
                "[Export] FALLO %s status=%s body=%s err=%s",
                rid, r.get("status"), r.get("body"), r.get("err"),
            )
    return by_id


def _fetch_export_file(driver: webdriver.Chrome, recording_id: str) -> str:
    """Sondeo con backoff exponencial hasta que Sonix devuelva la URL S3 del .txt."""
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
            logger.info("[Fetch] %s try=%d status=%s", recording_id, attempt + 1, status)
            if status != "processing" and file_url:
                s3 = requests.get(file_url, timeout=60)
                s3.raise_for_status()
                return s3.text
        elif resp.status_code < 400 and resp.text:
            # Caso raro: Sonix sirvió el .txt directo (sin pasar por S3 redirect).
            return resp.text

        sleep_s = EXPORT_POLL_DELAYS_S[min(attempt, len(EXPORT_POLL_DELAYS_S) - 1)]
        time.sleep(sleep_s)
        attempt += 1

    raise RuntimeError(
        f"Timeout esperando export listo para {recording_id}. Último payload: {last_payload}"
    )


# --- Cache local (fase 0) ----------------------------------------------------

def content_cache_key(media_path: str, original_name: str) -> str:
    """Clave de cache estable y segura ante colisiones: `{stem}__{hash8}`.

    Combina el nombre original (legible en la carpeta) con los primeros 8
    hex del SHA-256 del contenido. Así dos archivos distintos con el mismo
    nombre NO comparten transcripción, y la cache se reusa solo si el
    contenido es idéntico.
    """
    h = hashlib.sha256()
    with open(media_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return f"{Path(original_name).stem}__{h.hexdigest()[:8]}"


def _cache_path(cache_dir: str, cache_key: str) -> Path:
    return Path(cache_dir).resolve() / f"{cache_key}.txt"


def _split_cached(items: list, cache_dir: str) -> tuple:
    """Particiona `items` [(path, cache_key)] en (to_upload, cached).

    Un archivo se salta si ya existe `cache_dir/{cache_key}.txt`.
    `cached` es una lista de (cache_key, texto) ya leídos de disco.
    """
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    to_upload, cached = [], []
    for path, cache_key in items:
        txt_path = _cache_path(cache_dir, cache_key)
        if txt_path.exists():
            cached.append((cache_key, txt_path.read_text(encoding="utf-8")))
        else:
            to_upload.append((path, cache_key))
    return to_upload, cached


def _save_cache(cache_dir: str, cache_key: str, text: str) -> None:
    target = _cache_path(cache_dir, cache_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


# --- Orquestación ------------------------------------------------------------

def transcribe(files: list, settings) -> dict:
    """Transcribe `files` vía Sonix y devuelve `{cache_key: texto}`.

    `files`: lista de `(media_path, cache_key)`. `cache_key` (ver
    `content_cache_key`) es la clave estable para la cache.

    Es síncrono y bloqueante. Invocar vía `run_in_threadpool` / `asyncio.to_thread`
    y serializado. Lanza `RuntimeError` ante fallos irrecuperables.
    """
    if not files:
        return {}

    if not (settings.sonix_email and settings.sonix_password and settings.sonix_folder_id):
        logger.error("[Sonix] Credenciales incompletas (SONIX_EMAIL/PASSWORD/FOLDER_ID)")
        raise RuntimeError("TRANSCRIPTION_CONFIG_ERROR")

    cache_dir = settings.sonix_cache_dir
    to_upload, cached = _split_cached(files, cache_dir)

    results = {}
    for cache_key, text in cached:
        logger.info("[Skip] '%s' ya tenía transcripción en cache", cache_key)
        results[cache_key] = text

    if not to_upload:
        logger.info("[Done] Todas las transcripciones estaban en cache.")
        return results

    paths = [p for p, _ in to_upload]
    stem_to_key = {Path(p).stem: k for p, k in to_upload}

    driver = build_driver(headless=settings.sonix_headless)
    try:
        login(driver, settings.sonix_email, settings.sonix_password)

        logger.info("[Phase 1] Upload bulk: %d archivo(s)", len(paths))
        upload_all_files(driver, settings.sonix_folder_id, paths)

        logger.info("[Phase 2] Click TRANSCRIBE IN SPANISH (1 click, todos)")
        click_transcribe_in_spanish_bulk(driver)

        logger.info("[Phase 3] Esperar a que cada fila pase a Transcribed")
        stem_to_ids = wait_until_all_transcribed(driver, paths)

        logger.info("[Phase 4] Descargar (%d listos)", len(stem_to_ids))
        _download_all(driver, paths, stem_to_ids, stem_to_key, cache_dir, results)
    except RuntimeError:
        raise
    except Exception as exc:
        logger.error("[Sonix] Error inesperado: %s", exc)
        raise RuntimeError("TRANSCRIPTION_ERROR") from exc
    finally:
        driver.quit()

    return results


def _download_all(driver, paths, stem_to_ids, stem_to_key, cache_dir, results) -> None:
    """Disparo paralelo de exports + descarga con retry por candidato. Guarda en cache."""
    # Pre-disparar TODOS los primeros candidatos en paralelo (1 round-trip al server).
    first_ids = [stem_to_ids[Path(p).stem][0]
                 for p in paths
                 if stem_to_ids.get(Path(p).stem)]
    _trigger_exports_bulk(driver, first_ids)

    triggered = set(first_ids)
    for p in paths:
        stem = Path(p).stem
        cache_key = stem_to_key[stem]
        candidates = stem_to_ids.get(stem) or []
        if not candidates:
            logger.error("[Process] '%s' sin candidatos (timeout en Transcribed)", cache_key)
            raise RuntimeError("TRANSCRIPTION_TIMEOUT")

        logger.info("[Process] %s (%d candidato(s))", cache_key, len(candidates))
        text = None
        for rid in candidates:
            if rid not in triggered:
                _trigger_exports_bulk(driver, [rid])  # fallback on-demand
                triggered.add(rid)
            try:
                text = _fetch_export_file(driver, rid)
                logger.info("[Download] %s <- recording %s", cache_key, rid)
                break
            except Exception as exc:
                logger.warning("[Retry] %s id=%s falló: %s", cache_key, rid, exc)
                continue

        if text is None:
            logger.error("[Process] '%s' no se pudo descargar de ningún candidato", cache_key)
            raise RuntimeError("TRANSCRIPTION_ERROR")

        _save_cache(cache_dir, cache_key, text)
        results[cache_key] = text


# --- Limpieza del folder: borrar recordings ya "Transcribed" -----------------

# Borra cada recording vía DELETE /recordings/{id} (patrón Rails UJS, igual que
# el link data-method="delete" del frontend de Sonix). Se usa redirect:'manual'
# porque Sonix responde 302 → folder; siguiendo el redirect con Accept:json daría
# un 404 engañoso. El borrado real se confirma reescaneando el folder.
_BULK_DELETE_JS = """
    var done = arguments[arguments.length - 1];
    var ids = arguments[0];
    var csrf = document.querySelector("meta[name=csrf-token]").content;
    var promises = ids.map(function(id){
        return fetch('/recordings/' + id, {
            method: 'DELETE',
            headers: {'X-CSRF-Token': csrf, 'Accept': 'application/json'},
            credentials: 'same-origin',
            redirect: 'manual'
        }).then(function(r){
            return {id: id, status: r.status, type: r.type};
        }).catch(function(e){
            return {id: id, err: String(e)};
        });
    });
    Promise.all(promises).then(done);
"""


def _collect_transcribed_ids(driver: webdriver.Chrome) -> list:
    """Recoge los recording_id del folder cuyo estado es 'Transcribed'.

    Excluye los que estén 'Transcribing' (aún en proceso). Un recording marcado
    'Duplicate' que también es 'Transcribed' se incluye (ya está transcrito).
    """
    ids = []
    seen = set()
    for entries in _scan_folder_rows(driver, None).values():
        for rid, row_text in entries:
            if rid in seen:
                continue
            if "transcribing" in row_text:
                continue
            if "transcribed" in row_text:
                ids.append(rid)
                seen.add(rid)
    return ids


def delete_transcribed_in_folder(settings) -> dict:
    """Login → abre el folder de Sonix → borra todos los recordings 'Transcribed'.

    Síncrono y bloqueante (abre Chrome); invocar vía `run_in_threadpool` y
    serializado para no chocar con transcripciones. Devuelve
    `{"found": N, "deleted": N, "failed": N, "failed_ids": [...]}`.
    """
    if not (settings.sonix_email and settings.sonix_password and settings.sonix_folder_id):
        logger.error("[Cleanup] Credenciales/folder de Sonix incompletos")
        raise RuntimeError("TRANSCRIPTION_CONFIG_ERROR")

    driver = build_driver(headless=settings.sonix_headless)
    try:
        login(driver, settings.sonix_email, settings.sonix_password)
        driver.get(FOLDER_VIEW_URL.format(settings.sonix_folder_id))
        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, RECORDING_LINK_XPATH))
            )
        except TimeoutException:
            logger.info("[Cleanup] Folder vacío o sin recordings")
            return {"found": 0, "deleted": 0, "failed": 0, "failed_ids": []}
        time.sleep(2)

        ids = _collect_transcribed_ids(driver)
        logger.info("[Cleanup] %d recording(s) en estado Transcribed", len(ids))
        if not ids:
            return {"found": 0, "deleted": 0, "failed": 0, "failed_ids": []}

        driver.set_script_timeout(BULK_SCRIPT_TIMEOUT_S)
        results = driver.execute_async_script(_BULK_DELETE_JS, ids)
        for r in results or []:
            logger.info("[Cleanup] DELETE %s -> status=%s type=%s err=%s",
                        r.get("id"), r.get("status"), r.get("type"), r.get("err"))

        # Confirmar reescaneando: lo que ya no aparece se considera borrado.
        time.sleep(2)
        driver.get(FOLDER_VIEW_URL.format(settings.sonix_folder_id))
        time.sleep(2)
        remaining = {rid for entries in _scan_folder_rows(driver, None).values()
                     for rid, _ in entries}
        deleted = [i for i in ids if i not in remaining]
        failed = [i for i in ids if i in remaining]
        logger.info("[Cleanup] Borrados %d/%d (fallidos: %s)",
                    len(deleted), len(ids), failed)
        return {
            "found": len(ids),
            "deleted": len(deleted),
            "failed": len(failed),
            "failed_ids": failed,
        }
    finally:
        driver.quit()
