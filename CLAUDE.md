# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```powershell
# Crear y activar entorno virtual (Python 3.9)
python -m venv .venv
.venv\Scripts\Activate.ps1

# Instalar dependencias
pip install -r requirements.txt
```

Copiar `.env` con las credenciales necesarias (ver variables en `.env.example` o pedirlas al equipo). Las variables requeridas son: `SONIX_EMAIL`, `SONIX_PASSWORD`, `SONIX_FOLDER_ID`, `SONIX_INPUT_FOLDER`, `SONIX_OUTPUT_FOLDER`.

## Comandos frecuentes

```powershell
# Levantar la API de Sonix (transcripción)
uvicorn scriptPerPage.sonix.api:app --reload --port 8000

# Ejecutar el scraper de Sonix directamente
python scriptPerPage/sonix/scraper.py
```

No hay suite de tests automatizados ni linter configurado.

## Tecnologías

- **Selenium + webdriver-manager**: automatización del browser (login, upload, polling de UI, ejecución de `fetch()` desde la página).
- **requests**: GETs autenticados al endpoint de Sonix y al presigned URL de S3.
- **FastAPI + uvicorn**: exposición del pipeline como API REST.
- **python-dotenv**: configuración por variables de entorno.

Concurrencia: `scraper.py` es 100% sync. `api.py` declara endpoints `async` pero despacha `process_files` en thread pool via `run_in_threadpool` para no bloquear el event loop.

## Arquitectura

Toda la lógica vive bajo `scriptPerPage/sonix/`. Pipeline orquestado por `process_files`:

### Pre-filtro local (`split_already_transcribed`)
Particiona los archivos de entrada en (`to_upload`, `already`). Un archivo se considera ya transcrito si existe `OUTPUT_FOLDER/{stem}.txt`. Los `already` se devuelven en `results` con `status="skipped"` sin tocar Sonix.

### Phase 1 — Upload bulk (`upload_all_files`)
Navega UNA sola vez a `/upload?folder_id=X` y envía todos los archivos al input multi-file con `send_keys("\n".join(paths))`. Espera N indicadores "100% Uploaded".

### Phase 2 — Click único (`click_transcribe_in_spanish_bulk`)
Polla hasta que el botón "TRANSCRIBE IN SPANISH" esté habilitado (sin `disabled`, `aria-disabled='true'` ni clase `disabled`) y lo clickea una sola vez.

### Phase 3 — Wait Transcribed (`wait_until_all_transcribed`)
Polling refrescando el folder view hasta que cada fila pase de "Transcribing" a "Transcribed". En cada poll se hace UN solo `_scan_folder_rows(driver)` que recorre todos los `<a href="/recordings/...">` y devuelve `{filename: [(rid, row_text), ...]}`. Para cada fila se sube por el DOM al ancestro más chico con `count(.//a[contains(@href, '/recordings/')])=1` Y que contenga un badge de estado (`Transcribed`/`Transcribing`/`Duplicate`/`Failed`) — así se identifica la fila exacta sin abarcar tabla entera ni filas vecinas. Devuelve `{stem: [recording_id, ...]}` con candidatos ordenados (Transcribed-no-duplicado antes que Duplicate-fallback).

### Phase 4 — Bulk trigger + descarga (`_phase4_download`)
1. **Disparo paralelo**: `_trigger_exports_bulk(driver, [first_rid de cada archivo])` lanza N POSTs a `/recordings/{id}/export.json` en paralelo via `fetch()` + `Promise.all` dentro de un solo `execute_async_script`. Sonix encola las generaciones del `.txt` en S3 concurrentemente del lado servidor.
2. **Polling con backoff** (`_fetch_export_file`): GET a `/recordings/{id}/export?key=exports.{id}.0.txt`. Mientras Sonix responde JSON con `status="processing"` se reintenta con delays `[2, 2, 3, 5, 8, 13, 20, 30]` segundos (luego mantiene 30s). Cuando responde `status="completed"` con `url=https://sonixai.s3...`, se hace GET a esa presigned URL y se guarda el `.txt`.
3. **Retry por candidato**: si el primer `recording_id` falla, encola y polla los siguientes (útil cuando Sonix marcó el upload como Duplicate y necesitamos caer al original).

### API REST (`api.py`)
- `POST /transcribe` — recibe archivos uploaded, los guarda temporal y llama a `process_files()` en thread pool.
- `POST /transcribe/folder` — procesa el contenido de `SONIX_INPUT_FOLDER`.
- `GET /transcriptions/{name}` — descarga un `.txt` ya procesado.
- `GET /health` — healthcheck.

## Patrones clave

**WebDriver anti-detección:** `build_driver()` desactiva las flags de automatización de Chrome (`--disable-blink-features=AutomationControlled`, `excludeSwitches`). Replicar en nuevos scrapers.

**`fetch()` del navegador para requests con sesión:** Sonix valida cookies + CSRF token y discrimina respuestas por headers de navegación (`Sec-Fetch-Mode`). Para el POST a `/export.json` no usar `requests` directamente — usar `driver.execute_async_script` con `fetch(..., {credentials: 'include'})` y leer el CSRF del `<meta name="csrf-token">`. Funciona desde CUALQUIER página de Sonix (el CSRF de Rails es session-wide).

**Bulk trigger con `Promise.all`:** Para encolar N exports en paralelo, una sola llamada a `execute_async_script` con `Promise.all` mapeando los IDs a fetches concurrentes. Evita N navegaciones secuenciales del driver.

**Polling status en lugar de scrape de página:** La transcripción y la generación del export son ambas asíncronas. Para la primera se observa el cambio de badge `Transcribing → Transcribed`. Para la segunda se polla `/export?key=...` con backoff exponencial hasta que el JSON tenga `status="completed"`.

**Identificar fila exacta en tablas:** XPath `ancestor::*[count(.//a[contains(@href, '/recordings/')])=1 and contains(., 'Transcribed')...]` sube al ancestro más chico que tenga un solo link a recording y un badge de estado.

**Resilencia ante uploads duplicados:** `wait_until_all_transcribed` devuelve una LISTA de candidatos por archivo (prefiriendo Transcribed-no-duplicado), y Phase 4 los prueba en orden hasta que uno descargue.

**Constantes centralizadas:** Todos los timeouts/intervalos/XPaths viven en bloques al inicio del módulo (`LOGIN_TIMEOUT_S`, `UPLOAD_TIMEOUT_S`, `RECORDING_LINK_XPATH`, etc.) para que tunearlos no requiera buscar magic numbers en el código.
