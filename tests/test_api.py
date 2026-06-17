"""Tests de los endpoints FastAPI.

El scraper de Sonix se mockea, así que estos tests NO tocan Sonix ni abren
Chrome: validan el wiring del endpoint (config, errores, mapeo del resultado).
"""

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app


class FakeSettings:
    def __init__(self, configured: bool = True):
        self.sonix_email = "e@x.com" if configured else ""
        self.sonix_password = "pw" if configured else ""
        self.sonix_folder_id = "FID" if configured else ""
        self.sonix_headless = True
        self.sonix_cache_dir = "transcriptions"


@pytest.fixture
def client():
    return TestClient(app)


def _use_settings(monkeypatch, configured: bool):
    monkeypatch.setattr(main_module, "get_settings", lambda: FakeSettings(configured))


def test_health_configured(client, monkeypatch):
    _use_settings(monkeypatch, True)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "sonix_configured": True}


def test_health_not_configured(client, monkeypatch):
    _use_settings(monkeypatch, False)
    assert client.get("/health").json()["sonix_configured"] is False


def test_transcribe_requires_config(client, monkeypatch):
    _use_settings(monkeypatch, False)
    r = client.post("/transcribe", files={"files": ("a.wav", b"x", "audio/wav")})
    assert r.status_code == 503


def test_transcribe_success_maps_filename(client, monkeypatch):
    _use_settings(monkeypatch, True)

    def fake_transcribe(items, settings):
        # items = [(tmp_path, cache_key)]; devuelve {cache_key: texto}.
        return {ck: f"texto {ck}" for _, ck in items}

    monkeypatch.setattr(main_module.sonix_transcriber, "transcribe", fake_transcribe)

    r = client.post("/transcribe", files={"files": ("hola.wav", b"abc", "audio/wav")})
    assert r.status_code == 200
    body = r.json()
    # La respuesta se re-mapea del cache_key al nombre original subido.
    assert list(body.keys()) == ["hola.wav"]
    assert body["hola.wav"].startswith("texto hola__")


def test_transcribe_pipeline_error_returns_502(client, monkeypatch):
    _use_settings(monkeypatch, True)

    def boom(items, settings):
        raise RuntimeError("TRANSCRIPTION_ERROR")

    monkeypatch.setattr(main_module.sonix_transcriber, "transcribe", boom)

    r = client.post("/transcribe", files={"files": ("hola.wav", b"abc", "audio/wav")})
    assert r.status_code == 502
    assert "TRANSCRIPTION_ERROR" in r.json()["detail"]


def test_folder_cleanup_requires_config(client, monkeypatch):
    _use_settings(monkeypatch, False)
    assert client.post("/folder/cleanup").status_code == 503


def test_folder_cleanup_returns_counts(client, monkeypatch):
    _use_settings(monkeypatch, True)
    monkeypatch.setattr(
        main_module.sonix_transcriber,
        "delete_transcribed_in_folder",
        lambda settings: {"found": 2, "deleted": 2, "failed": 0, "failed_ids": []},
    )
    r = client.post("/folder/cleanup")
    assert r.status_code == 200
    assert r.json()["deleted"] == 2
