# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Qué es

Microservicio de transcripción audio/video → texto que automatiza Sonix.ai con Selenium. API REST de un solo propósito: subir archivos y obtener el texto. Consumido por agentes/IA o scripts; corre **headless** y se despliega con **Docker**.

## Setup

```powershell
# Local (Python 3.12)
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

```bash
# Docker (recomendado — la imagen trae Chromium + chromedriver)
docker compose up --build
```

Copiar `.env.example` a `.env`. Variables: `SONIX_EMAIL`, `SONIX_PASSWORD`, `SONIX_FOLDER_ID` (obligatorias); `SONIX_HEADLESS` (default `true`), `SONIX_CACHE_DIR` (default `transcriptions`).

No hay suite de tests ni linter configurado.

## Tecnologías

- **Selenium + webdriver-manager**: automatización del browser (login, upload, sondeo de UI, ejecución de `fetch()` desde la página). En Docker usa el Chromium del sistema vía `CHROME_BIN`/`CHROMEDRIVER_PATH`; en local cae a `webdriver-manager`.
- **requests**: GETs autenticados al endpoint de Sonix y a la presigned URL de S3.
- **FastAPI + uvicorn**: expone el pipeline como API REST.
- **pydantic-settings**: configuración por variables de entorno (`app/config.py`).

Concurrencia: `sonix_transcriber.py` es 100% sync y bloqueante. `main.py` despacha el pipeline en thread pool vía `run_in_threadpool` para no bloquear el event loop.

## Arquitectura

```
app/
  config.py             # Settings (pydantic-settings, lee .env vía lru_cache)
  sonix_transcriber.py  # pipeline (4 fases) + delete_transcribed_in_folder
  main.py               # FastAPI: /transcribe, /folder/cleanup, /health
```

### API (`app/main.py`)
- `POST /transcribe` — guarda los uploads a un tempdir, calcula `content_cache_key` por archivo, llama a `transcribe(items, settings)` en thread pool y devuelve `{nombre_original: texto}`. Borra el tempdir en `finally`.
- `POST /folder/cleanup` — `delete_transcribed_in_folder` para que el folder de Sonix no crezca sin límite. No toca la cache local.
- `GET /health` — healthcheck + flag `sonix_configured`.

### Pipeline (`app/sonix_transcriber.py`), orquestado por `transcribe(files, settings)`

`files` es `[(media_path, cache_key)]`; devuelve `{cache_key: texto}`.

#### Fase 0 — Cache (`_split_cached`)
Salta archivos cuyo `{cache_key}.txt` ya existe en `sonix_cache_dir`. `content_cache_key(path, nombre)` → `"{stem}__{sha256[:8]}"`: nombre legible + hash corto del contenido, así dos archivos distintos con el mismo nombre no comparten transcripción y la cache se reusa solo si el contenido es idéntico.

#### Fase 1 — Upload bulk (`upload_all_files`)
Navega UNA vez a `/upload?folder_id=X` y envía todos los archivos al input multi-file con `send_keys("\n".join(paths))`. Espera N indicadores "100% Uploaded".

#### Fase 2 — Click único (`click_transcribe_in_spanish_bulk`)
Sondea hasta que el botón "TRANSCRIBE IN SPANISH" esté habilitado (sin `disabled`, `aria-disabled='true'` ni clase `disabled`) y lo clickea una sola vez.

#### Fase 3 — Wait Transcribed (`wait_until_all_transcribed`)
Sondeo refrescando el folder view hasta que cada fila pase de "Transcribing" a "Transcribed". Cada sondeo hace UN solo `_scan_folder_rows(driver, wanted_names)` que recorre los `<a href="/recordings/...">` y devuelve `{filename: [(rid, row_text), ...]}`. **El escaneo se acota a `wanted_names`** (los archivos de la sesión) ANTES de resolver el ancestro, así el costo por sondeo es proporcional a los archivos subidos, no al tamaño total del folder. Para cada fila sube por el DOM al ancestro más chico con `count(.//a[contains(@href,'/recordings/')])=1` Y un badge de estado. Devuelve `{stem: [recording_id, ...]}` con candidatos ordenados (Transcribed-no-duplicado antes que Duplicate-fallback).

#### Fase 4 — Bulk trigger + descarga (`_download_all`)
1. **Disparo paralelo**: `_trigger_exports_bulk(driver, [first_rid de cada archivo])` lanza N POSTs a `/recordings/{id}/export.json` en paralelo via `fetch()` + `Promise.all` dentro de un solo `execute_async_script`.
2. **Sondeo con backoff** (`_fetch_export_file`): GET a `/recordings/{id}/export?key=exports.{id}.0.txt`. Mientras Sonix responde `status="processing"` reintenta con delays `[2,2,3,5,8,13,20,30]` s (luego 30 s). Cuando responde `status="completed"` con `url=https://sonixai.s3...`, hace GET a esa presigned URL y guarda el `.txt` en cache.
3. **Retry por candidato**: si el primer `recording_id` falla, encola y sondea los siguientes (útil cuando Sonix marcó el upload como Duplicate).

### Limpieza (`delete_transcribed_in_folder`)
Login → abre el folder → recoge los recordings `Transcribed` (`_collect_transcribed_ids`) → `DELETE /recordings/{id}` en bloque (patrón Rails UJS con CSRF, `redirect:'manual'`) → confirma reescaneando el folder. Devuelve `{found, deleted, failed, failed_ids}`.

## Patrones clave

**WebDriver anti-detección:** `build_driver()` desactiva las flags de automatización de Chrome (`--disable-blink-features=AutomationControlled`, `excludeSwitches`) y corre `--headless=new` por defecto.

**`fetch()` del navegador para requests con sesión:** Sonix valida cookies + CSRF token y discrimina por headers de navegación. Para el POST a `/export.json` no se usa `requests` directamente — se usa `driver.execute_async_script` con `fetch(..., {credentials:'include'})` leyendo el CSRF del `<meta name="csrf-token">`. Funciona desde cualquier página de Sonix (CSRF de Rails es session-wide).

**Bulk trigger con `Promise.all`:** N exports se encolan en paralelo con una sola llamada a `execute_async_script`. Evita N navegaciones secuenciales del driver.

**Sondeo de estado en lugar de scrape de página:** transcripción y export son ambas asíncronas; se observa el cambio de badge `Transcribing → Transcribed` y el JSON `status="completed"` respectivamente.

**Identificar fila exacta en tablas:** XPath `ancestor::*[count(.//a[contains(@href,'/recordings/')])=1 and contains(.,'Transcribed')...]` sube al ancestro más chico con un solo link a recording y un badge de estado.

**Constantes centralizadas:** timeouts/intervalos/XPaths viven en bloques al inicio de `sonix_transcriber.py` (`LOGIN_TIMEOUT_S`, `UPLOAD_TIMEOUT_S`, `RECORDING_LINK_XPATH`, etc.).

**Serialización del acceso a Sonix:** `main.py` envuelve el pipeline y la limpieza en un `asyncio.Lock` (`_sonix_lock`). El scraper maneja una sola sesión de navegador, así que nunca debe correr más de una transcripción/limpieza a la vez contra la misma cuenta.
