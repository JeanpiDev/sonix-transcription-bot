"""API REST del transcriptor de Sonix.

Pensada para consumo por agentes/IA o scripts: subes uno o varios archivos de
audio/video a `POST /transcribe` y obtienes el texto transcrito en JSON. El
scraper corre headless en segundo plano (no abre ventana de navegador).

Levantar en local:
    uvicorn app.main:app --port 8000

O con Docker (recomendado): `docker compose up --build`.
"""

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from app import sonix_transcriber
from app.config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(
    title="Sonix Transcriber",
    description=(
        "Sube audio/video a Sonix.ai, lo transcribe en español (headless) y "
        "devuelve el texto. Cachea por hash de contenido para no re-transcribir."
    ),
    version="2.0.0",
)

# Serializa el acceso a Sonix: el scraper abre Chrome y maneja UNA sola sesión
# (upload masivo + escaneo del folder asumen exclusividad). Sin esto, dos
# peticiones concurrentes lanzarían dos navegadores contra la misma cuenta y
# chocarían. Las peticiones en paralelo esperan turno en este lock.
_sonix_lock = asyncio.Lock()


def _require_sonix_configured() -> None:
    """Aborta con 503 si faltan las credenciales/folder de Sonix."""
    settings = get_settings()
    if not (settings.sonix_email and settings.sonix_password and settings.sonix_folder_id):
        raise HTTPException(
            status_code=503,
            detail="Sonix no configurado (faltan SONIX_EMAIL / SONIX_PASSWORD / SONIX_FOLDER_ID).",
        )


@app.get("/health", summary="Estado del servicio")
async def health():
    settings = get_settings()
    return {
        "status": "ok",
        "sonix_configured": bool(
            settings.sonix_email and settings.sonix_password and settings.sonix_folder_id
        ),
    }


@app.post("/transcribe", summary="Transcribir uno o varios audios/videos")
async def transcribe(files: list[UploadFile] = File(...)):
    """Recibe archivos (`multipart/form-data`), los transcribe y devuelve
    `{nombre_original: texto}`.

    El procesamiento es bloqueante (abre Chrome headless y sondea a Sonix), así
    que corre en un thread pool para no bloquear el event loop y se serializa con
    `_sonix_lock`. Si un archivo ya fue transcrito antes (mismo contenido), se
    sirve desde la cache local.
    """
    _require_sonix_configured()
    settings = get_settings()

    tmp_dir = Path(tempfile.mkdtemp(prefix="sonix_upload_"))
    items = []        # [(tmp_path, cache_key)]
    key_to_name = {}  # cache_key -> nombre original (para mapear el resultado)
    try:
        for f in files:
            if not f.filename:
                continue
            dest = tmp_dir / Path(f.filename).name  # evita path traversal en el nombre
            with dest.open("wb") as buf:
                shutil.copyfileobj(f.file, buf)
            cache_key = sonix_transcriber.content_cache_key(str(dest), f.filename)
            items.append((str(dest), cache_key))
            key_to_name[cache_key] = f.filename

        if not items:
            raise HTTPException(status_code=400, detail="No se recibió ningún archivo válido.")

        async with _sonix_lock:
            results = await run_in_threadpool(sonix_transcriber.transcribe, items, settings)
    except HTTPException:
        raise
    except RuntimeError as exc:
        # Errores controlados del pipeline (config, timeout, fallo de transcripción).
        raise HTTPException(status_code=502, detail=f"Error de transcripción: {exc}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return {key_to_name.get(k, k): text for k, text in results.items()}


@app.post("/folder/cleanup", summary="Borrar del folder de Sonix los recordings ya transcritos")
async def folder_cleanup():
    """Limpieza operativa: borra del folder de Sonix todos los recordings en
    estado 'Transcribed'. Útil para que el folder no crezca sin límite. La cache
    local de `.txt` NO se toca. Comparte `_sonix_lock` con `/transcribe` para no
    abrir Chrome en paralelo con una transcripción en curso.
    """
    _require_sonix_configured()
    settings = get_settings()
    try:
        async with _sonix_lock:
            result = await run_in_threadpool(
                sonix_transcriber.delete_transcribed_in_folder, settings
            )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"Error en limpieza: {exc}")
    return result
