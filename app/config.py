"""Configuración leída del archivo .env (o de variables de entorno)."""
import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

DATOS_DIR = Path(os.getenv("DATOS_DIR", RAIZ / "datos"))
DB_PATH = Path(os.getenv("DB_PATH", DATOS_DIR / "inventario.db"))
ARCHIVOS_DIR = DATOS_DIR / "archivos"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_USUARIOS_PERMITIDOS = {
    u.strip() for u in os.getenv("TELEGRAM_USUARIOS_PERMITIDOS", "").split(",") if u.strip()
}
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5").strip()

CLAVE_ACCESO = os.getenv("CLAVE_ACCESO", "").strip()
URL_PUBLICA = os.getenv("URL_PUBLICA", "").strip().rstrip("/")

MARGEN_POR_DEFECTO = float(os.getenv("MARGEN_POR_DEFECTO", "40"))
STOCK_BAJO = float(os.getenv("STOCK_BAJO", "2"))


def clave_secreta() -> str:
    """Clave para firmar la sesión del navegador; se crea una sola vez y se guarda."""
    valor = os.getenv("SECRET_KEY", "").strip()
    if valor:
        return valor
    DATOS_DIR.mkdir(parents=True, exist_ok=True)
    archivo = DATOS_DIR / ".secret_key"
    if not archivo.exists():
        archivo.write_text(os.urandom(32).hex())
    return archivo.read_text().strip()
