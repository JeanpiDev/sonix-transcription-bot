# Sonix Transcription Bot

Automatización con Selenium + FastAPI para subir audio/video a [Sonix.ai](https://sonix.ai), esperar la transcripción en español y descargar los `.txt` resultantes.

El pipeline sube **todos los archivos en una sola pasada** al folder configurado, dispara la transcripción con un solo click, polla el estado en el folder view y descarga los `.txt` vía la API interna de Sonix usando la misma sesión del navegador.

## Requisitos

- Python 3.9+
- Google Chrome instalado (el `webdriver-manager` se encarga de bajar el ChromeDriver compatible).
- Cuenta de Sonix.ai con un folder donde subir las grabaciones.

## Setup

### Crear entorno virtual e instalar dependencias

**Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**Linux / macOS:**
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configurar variables de entorno

Crear un archivo `.env` en la raíz del proyecto con:

```env
SONIX_EMAIL=tu_email@dominio.com
SONIX_PASSWORD=tu_password
SONIX_FOLDER_ID=o9y...o            # ID del folder destino en Sonix
SONIX_INPUT_FOLDER=./input_files    # Carpeta local de audios/videos a transcribir
SONIX_OUTPUT_FOLDER=./transcriptions # Carpeta destino de los .txt
```

El `SONIX_FOLDER_ID` se ve en la URL cuando entras a un folder en Sonix: `my.sonix.ai/f/<FOLDER_ID>`.

## Uso

### Opción 1 — Script directo

Coloca los archivos en `input_files/` (formatos: `.mp3 .wav .m4a .mp4 .mov .avi .mkv .flac .ogg .aac .wma`) y ejecuta:

```powershell
python scriptPerPage/sonix/scraper.py
```

El script:
1. Filtra archivos cuyo `.txt` ya existe en `transcriptions/` (no los re-procesa).
2. Sube los restantes en bloque a Sonix.
3. Click único en "TRANSCRIBE IN SPANISH".
4. Polla hasta que cada fila pase a "Transcribed" en el folder view.
5. Encola la generación del `.txt` en paralelo (vía `Promise.all` en el navegador).
6. Descarga cada transcripción a `transcriptions/{nombre_original}.txt`.

### Opción 2 — API REST

Levantar el servidor:

```powershell
uvicorn scriptPerPage.sonix.api:app --reload --port 8000
```

Endpoints disponibles:

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/transcribe` | Sube uno o varios archivos (`multipart/form-data`) y devuelve los textos transcritos en JSON. |
| `POST` | `/transcribe/folder` | Procesa todo lo que esté en `SONIX_INPUT_FOLDER`. |
| `GET` | `/transcriptions/{name}` | Descarga un `.txt` ya procesado por nombre (sin extensión). |
| `GET` | `/health` | Healthcheck. |

Documentación interactiva (Swagger) disponible en `http://localhost:8000/docs` cuando el servidor está corriendo.

Ejemplo con `curl`:

```bash
# Procesar archivos uploaded
curl -X POST http://localhost:8000/transcribe \
  -F "files=@audio1.wav" \
  -F "files=@audio2.mp3"

# Procesar la carpeta configurada
curl -X POST http://localhost:8000/transcribe/folder

# Descargar transcripción ya generada
curl http://localhost:8000/transcriptions/audio1 -o audio1.txt
```

## Arquitectura

```
input_files/                      <- coloca aquí tus audios/videos
transcriptions/                   <- aquí aparecen los .txt
scriptPerPage/sonix/
  scraper.py                      <- pipeline completo (4 fases)
  api.py                          <- wrapper FastAPI sobre scraper.py
```

### Pipeline (4 fases)

| Fase | Función | Qué hace |
|---|---|---|
| **0** | `split_already_transcribed` | Salta archivos cuyo `{stem}.txt` ya existe localmente. |
| **1** | `upload_all_files` | `send_keys("\n".join(paths))` sobre el input multi-file. Espera N indicadores "100% Uploaded". |
| **2** | `click_transcribe_in_spanish_bulk` | Polla hasta que el botón se habilite (no `disabled`/`aria-disabled`) y lo clickea una vez. |
| **3** | `wait_until_all_transcribed` | Refresca el folder view hasta que cada fila pase de "Transcribing" a "Transcribed". Single DOM scan por poll. |
| **4** | `_phase4_download` | Dispara N POST `/export.json` en paralelo (Promise.all). Polla cada export con backoff exponencial. GET a presigned URL de S3 y guarda `.txt`. |

Ver [`CLAUDE.md`](./CLAUDE.md) para detalles técnicos (XPaths, manejo de duplicates, anti-detección de WebDriver, etc).

## Salida (ejemplo de `.txt`)

```
nombre-del-archivo.WAV

Speaker 1: [00:00:00] Aló. Muy buenos días. Habla con Claudia Pérez. ¿Con quién tengo el gusto?

Speaker 2: [00:00:05] ¿Aló? Buenos días. Con Luis David Franco.

Speaker 1: [00:00:07] ¿Cómo se encuentra hoy, señor Luis? Indíqueme qué le podemos ayudar.
...
```

Las transcripciones incluyen timestamps por intervención y detección automática de speakers (configurada en el payload `EXPORT_TXT_PAYLOAD` de `scraper.py`).

## Notas

- No hay tests automatizados ni linter configurado.
- El scraper corre Chrome en modo **no headless** por defecto para facilitar debugging visual. Puedes cambiarlo pasando `headless=True` a `process_files()`.
- Si Sonix detecta tu upload como duplicado de uno existente, el script intenta primero el ID "no duplicado" y cae a candidatos alternativos si el primero falla.
- El polling del export usa backoff exponencial (`[2, 2, 3, 5, 8, 13, 20, 30]` segundos, luego 30s constantes hasta 10 min máximo).
