# Sonix Transcriber

Microservicio que transcribe **audio/video a texto** automatizando [Sonix.ai](https://sonix.ai) con Selenium. Expone una API REST: subes uno o varios archivos y recibes el texto transcrito en español.

Pensado para consumo programático (por un agente/IA o un script): el navegador corre **headless en segundo plano** (no abre ventana) y todo se levanta con **Docker**.

El pipeline sube **todos los archivos en una sola pasada** al folder configurado, dispara la transcripción con un solo click, sondea el estado en el folder view y descarga los `.txt` vía la API interna de Sonix usando la misma sesión del navegador. Cachea por hash de contenido para no re-transcribir lo mismo.

> [!IMPORTANT]
> Este es un proyecto **no oficial** y sin relación con Sonix.ai. Automatiza su interfaz web con un navegador (no usa una API pública), por lo que el funcionamiento puede romperse si Sonix cambia su UI, y su uso queda sujeto a los [Términos de servicio de Sonix](https://sonix.ai/terms). Úsalo solo con una cuenta propia y bajo tu responsabilidad. Las credenciales viven en `.env` (gitignored): **nunca las subas al repositorio**.

## Requisitos

- **Con Docker:** solo Docker (la imagen trae Chromium + chromedriver).
- **En local:** Python 3.12, Google Chrome instalado (`webdriver-manager` baja el ChromeDriver compatible).
- Cuenta de Sonix.ai con un folder donde subir las grabaciones.

## Configuración

Copia `.env.example` a `.env` y completa las credenciales:

```env
SONIX_EMAIL=tu_email@dominio.com
SONIX_PASSWORD=tu_password
SONIX_FOLDER_ID=o9yKNWDo        # ID del folder: my.sonix.ai/f/<FOLDER_ID>

# Opcionales
SONIX_HEADLESS=true             # false para ver el navegador (debug local)
SONIX_CACHE_DIR=transcriptions  # carpeta de cache de los .txt
```

> El `.env` está en `.gitignore`. Nunca subas credenciales reales al repo.

## Uso con Docker (recomendado)

```bash
docker compose up --build
```

La API queda en `http://localhost:8000` (Swagger en `/docs`).

## Uso en local (sin Docker)

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1        # Windows
# source .venv/bin/activate       # Linux/macOS
pip install -r requirements.txt

uvicorn app.main:app --port 8000
```

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/transcribe` | Sube uno o varios archivos (`multipart/form-data`, campo `files`) y devuelve `{ "nombre_original": "texto transcrito" }`. |
| `POST` | `/folder/cleanup` | Borra del folder de Sonix los recordings ya `Transcribed` (la cache local no se toca). |
| `GET` | `/health` | Healthcheck + si Sonix está configurado. |

### Ejemplo

```bash
curl -X POST http://localhost:8000/transcribe \
  -F "files=@audio1.wav" \
  -F "files=@llamada.mp4"
```

Respuesta:

```json
{
  "audio1.wav": "Speaker 1: [00:00:00] Aló, buenos días...\n\nSpeaker 2: ...",
  "llamada.mp4": "Speaker 1: [00:00:00] ..."
}
```

## Cómo funciona (pipeline)

| Fase | Qué hace |
|---|---|
| **0 — Cache** | Salta archivos cuyo `{cache_key}.txt` ya existe en `SONIX_CACHE_DIR`. La clave es `{nombre}__{sha256[:8]}`: reusa solo si el contenido es idéntico. |
| **1 — Upload** | `send_keys` con todos los paths sobre el input multi-file. Espera N indicadores "100% Uploaded". |
| **2 — Transcribe** | Sondea hasta que el botón "TRANSCRIBE IN SPANISH" se habilite y lo clickea una vez. |
| **3 — Wait** | Refresca el folder view hasta que cada fila pase de "Transcribing" a "Transcribed". El escaneo se acota a los archivos de la sesión. |
| **4 — Download** | Dispara N POST `/export.json` en paralelo (`Promise.all`), sondea cada export con backoff exponencial, descarga el `.txt` desde la presigned URL de S3 y lo guarda en cache. |

Ver [`CLAUDE.md`](./CLAUDE.md) para detalles técnicos (XPaths, manejo de duplicates, anti-detección de WebDriver, etc.).

## Estructura

```
app/
  config.py            # Settings (pydantic-settings, lee .env)
  sonix_transcriber.py # pipeline completo (4 fases) + limpieza del folder
  main.py              # API FastAPI (/transcribe, /folder/cleanup, /health)
transcriptions/        # cache de los .txt — gitignored
Dockerfile
docker-compose.yml
```

## Desarrollo

Instala las dependencias de desarrollo (incluyen las de runtime) y corre el linter y los tests:

```bash
pip install -r requirements-dev.txt

ruff check .        # linter
ruff format .       # formateo (opcional)
pytest              # tests
```

Los tests cubren las funciones puras (cache por hash, helpers del scraper) y los endpoints FastAPI **con el scraper de Sonix mockeado**, así que no tocan Sonix ni abren Chrome. La configuración de `ruff` y `pytest` vive en `pyproject.toml`.

## Notas

- **Las peticiones a Sonix se serializan**: el scraper maneja una sola sesión de navegador a la vez, así que las llamadas concurrentes a `/transcribe` (y `/folder/cleanup`) esperan turno. Manda varios archivos en una sola petición para transcribirlos en bloque.
- Headless por defecto. Para ver el navegador en local: `SONIX_HEADLESS=false`.
- Si Sonix marca un upload como duplicado, el pipeline prueba primero el ID "no duplicado" y cae a candidatos alternativos.
- El sondeo del export usa backoff exponencial (`[2, 2, 3, 5, 8, 13, 20, 30]` s, luego 30 s, hasta 10 min máx).
- Sonix.ai es un servicio de pago: requiere una cuenta válida con créditos/suscripción.

## Licencia

[MIT](./LICENSE) © 2026 Jean Ramirez.
