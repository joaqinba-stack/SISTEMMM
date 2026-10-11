import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SECRET_KEY", "pruebas")
os.environ["TELEGRAM_TOKEN"] = ""
os.environ["CLAVE_ACCESO"] = ""

from app import config, db  # noqa: E402


@pytest.fixture(autouse=True)
def base_temporal(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "prueba.db")
    monkeypatch.setattr(config, "ARCHIVOS_DIR", tmp_path / "archivos")
    monkeypatch.setattr(config, "TELEGRAM_TOKEN", "")
    monkeypatch.setattr(config, "CLAVE_ACCESO", "")
    db.inicializar()
    yield tmp_path


@pytest.fixture
def con():
    with db.conexion() as c:
        yield c
