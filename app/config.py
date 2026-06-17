"""Configuración por variables de entorno (pydantic-settings).

Lee `.env` desde la raíz del proyecto. En Docker, las variables se inyectan vía
`env_file` en `docker-compose.yml`. Todas las claves se documentan en
`.env.example`.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Credenciales de Sonix (obligatorias) --------------------------------
    sonix_email: str = ""
    sonix_password: str = ""
    # ID del folder destino en Sonix (lo ves en my.sonix.ai/f/<FOLDER_ID>).
    sonix_folder_id: str = ""

    # --- Comportamiento del scraper ------------------------------------------
    # Headless por defecto: el servicio corre en segundo plano / Docker y no
    # debe abrir una ventana de Chrome visible. Poner en false solo para debug
    # local cuando se quiere ver el navegador.
    sonix_headless: bool = True

    # Carpeta donde se cachean las transcripciones `.txt`. Si un archivo ya fue
    # transcrito (mismo contenido) se reusa de aquí sin volver a llamar a Sonix.
    sonix_cache_dir: str = "transcriptions"


@lru_cache
def get_settings() -> Settings:
    return Settings()
