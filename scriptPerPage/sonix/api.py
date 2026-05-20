import os
import sys
import shutil
from pathlib import Path
from typing import List

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.concurrency import run_in_threadpool
from dotenv import load_dotenv

# Permite importar scraper.py tanto desde la raiz del proyecto como desde este directorio
sys.path.insert(0, os.path.dirname(__file__))
from scraper import process_files, get_input_files  # noqa: E402

load_dotenv()

OUTPUT_FOLDER = os.getenv("SONIX_OUTPUT_FOLDER", "./transcriptions")
TEMP_FOLDER   = "./temp_uploads"

app = FastAPI(
    title="Sonix Transcription Bot",
    description=(
        "Sube archivos de audio/video a Sonix.ai, "
        "los transcribe en espanol y devuelve el texto como .txt."
    ),
    version="1.0.0",
)


@app.post("/transcribe", summary="Transcribir archivos subidos directamente")
async def transcribe_uploaded(files: List[UploadFile] = File(...)):
    """
    Recibe uno o varios archivos de audio/video, los sube a Sonix,
    espera la transcripcion y devuelve los resultados.
    """
    os.makedirs(TEMP_FOLDER, exist_ok=True)
    saved_paths = []

    try:
        for f in files:
            temp_path = os.path.join(TEMP_FOLDER, f.filename)
            with open(temp_path, "wb") as buf:
                shutil.copyfileobj(f.file, buf)
            saved_paths.append(temp_path)

        results = await run_in_threadpool(process_files, saved_paths)
        return JSONResponse(content=results)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        for path in saved_paths:
            if os.path.exists(path):
                os.remove(path)


@app.post("/transcribe/folder", summary="Transcribir archivos de la carpeta de entrada configurada")
async def transcribe_from_folder():
    """
    Toma todos los archivos de audio/video de SONIX_INPUT_FOLDER,
    los procesa y devuelve los resultados.
    """
    file_paths = get_input_files()
    if not file_paths:
        return JSONResponse(content={"message": "No se encontraron archivos en la carpeta de entrada."})

    results = await run_in_threadpool(process_files, file_paths)
    return JSONResponse(content=results)


@app.get("/transcriptions/{name}", summary="Descargar una transcripcion guardada")
async def download_transcription(name: str):
    """Descarga el archivo .txt de una transcripcion ya procesada."""
    file_path = os.path.join(OUTPUT_FOLDER, f"{name}.txt")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"Transcripcion '{name}' no encontrada.")
    return FileResponse(file_path, media_type="text/plain", filename=f"{name}.txt")


@app.get("/health", summary="Estado del servicio")
async def health():
    return {"status": "ok", "service": "Sonix Transcription Bot"}
