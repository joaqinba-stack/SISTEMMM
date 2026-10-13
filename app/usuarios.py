"""Cuentas de usuario: contraseñas, recuperación sin correo y registro de actividad."""
import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import datetime, timedelta

from app.inventario import ErrorNegocio

ROLES = {"admin": "Administrador", "usuario": "Usuario"}
ESTADOS = {"pendiente": "Esperando aprobación", "activo": "Activo", "bloqueado": "Bloqueado"}

LARGO_MINIMO_CLAVE = 8
MAX_INTENTOS = 5        # contraseñas o códigos errados seguidos antes de frenar la cuenta
MINUTOS_BLOQUEO = 15
MAX_PENDIENTES = 20     # cuentas esperando aprobación; evita que llenen la lista desde afuera
_LETRAS_CODIGO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin 0/O ni 1/I, que se confunden

MAL_ENTRADA = "El usuario o la contraseña no son correctos."
MAL_CODIGO = "El usuario o el código de recuperación no son correctos."


# ---------------------------------------------------------------- contraseñas y códigos

def _hash(secreto: str) -> str:
    sal = secrets.token_bytes(16)
    derivado = hashlib.scrypt(secreto.encode("utf-8"), salt=sal, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${sal.hex()}${derivado.hex()}"


def _coincide(secreto: str, guardado: str) -> bool:
    try:
        _, sal, esperado = guardado.split("$")
        derivado = hashlib.scrypt(secreto.encode("utf-8"), salt=bytes.fromhex(sal),
                                  n=2 ** 14, r=8, p=1, dklen=32)
    except ValueError:
        return False
    return hmac.compare_digest(derivado.hex(), esperado)


_HASH_FALSO = _hash(secrets.token_hex(8))  # para tardar lo mismo exista o no la cuenta


def nuevo_codigo() -> str:
    """Código de recuperación: se muestra una sola vez y solo se guarda su hash."""
    letras = "".join(secrets.choice(_LETRAS_CODIGO) for _ in range(12))
    return "-".join(letras[i:i + 4] for i in range(0, 12, 4))


def _limpiar_codigo(codigo: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (codigo or "").upper())


def _validar_clave(clave: str, repetir: str | None) -> None:
    if len(clave or "") < LARGO_MINIMO_CLAVE:
        raise ErrorNegocio(f"La contraseña tiene que tener al menos {LARGO_MINIMO_CLAVE} letras o números.")
    if repetir is not None and clave != repetir:
        raise ErrorNegocio("Las dos contraseñas no son iguales. Escribilas de nuevo.")


# ---------------------------------------------------------------- consultas

def obtener(con: sqlite3.Connection, usuario_id: int):
    return con.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()


def _buscar(con: sqlite3.Connection, usuario: str):
    return con.execute("SELECT * FROM usuarios WHERE usuario = ?",
                       ((usuario or "").strip().lower(),)).fetchone()


def listar(con: sqlite3.Connection):
    return con.execute(
        """SELECT * FROM usuarios
           ORDER BY CASE estado WHEN 'pendiente' THEN 0 WHEN 'activo' THEN 1 ELSE 2 END,
                    nombre COLLATE NOCASE"""
    ).fetchall()


def pendientes(con: sqlite3.Connection) -> int:
    return con.execute("SELECT COUNT(*) FROM usuarios WHERE estado = 'pendiente'").fetchone()[0]


# ---------------------------------------------------------------- crear cuenta y entrar

def crear(con: sqlite3.Connection, usuario: str, nombre: str, clave: str,
          repetir: str | None = None) -> tuple[sqlite3.Row, str]:
    """Crea la cuenta y devuelve (cuenta, código de recuperación). La primera cuenta es
    administradora y entra enseguida; las demás esperan la aprobación de un administrador."""
    usuario = (usuario or "").strip().lower()
    nombre = re.sub(r"\s+", " ", nombre or "").strip()[:60] or usuario
    if not re.fullmatch(r"[a-z0-9._-]{3,30}", usuario):
        raise ErrorNegocio("El usuario tiene que tener entre 3 y 30 letras o números, sin espacios ni acentos.")
    _validar_clave(clave, repetir)
    if _buscar(con, usuario):
        raise ErrorNegocio("Ya hay una cuenta con ese usuario. Elegí otro.")
    primera = con.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 0
    if not primera and pendientes(con) >= MAX_PENDIENTES:
        raise ErrorNegocio("Hay muchas cuentas esperando aprobación. Pedile a un administrador que las revise.")
    codigo = nuevo_codigo()
    usuario_id = con.execute(
        """INSERT INTO usuarios (usuario, nombre, clave_hash, recuperacion_hash, rol, estado)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (usuario, nombre, _hash(clave), _hash(_limpiar_codigo(codigo)),
         "admin" if primera else "usuario", "activo" if primera else "pendiente"),
    ).lastrowid
    return obtener(con, usuario_id), codigo


def _minutos_de_espera(fila) -> int:
    if not fila["bloqueado_hasta"]:
        return 0
    falta = datetime.fromisoformat(fila["bloqueado_hasta"]) - datetime.now()
    return max(0, int(falta.total_seconds() // 60) + 1) if falta.total_seconds() > 0 else 0


def _anotar_fallo(con: sqlite3.Connection, fila, que: str, mensaje: str) -> str:
    """Cuenta el intento errado (queda en el registro) y frena la cuenta si son demasiados."""
    if fila["intentos"] + 1 >= MAX_INTENTOS:
        hasta = (datetime.now() + timedelta(minutes=MINUTOS_BLOQUEO)).isoformat(timespec="seconds")
        con.execute("UPDATE usuarios SET intentos = 0, bloqueado_hasta = ? WHERE id = ?", (hasta, fila["id"]))
        registrar(con, fila, "Intento fallido", f"{que}. Cuenta frenada {MINUTOS_BLOQUEO} minutos por demasiados intentos")
        return f"Demasiados intentos. Por seguridad hay que esperar {MINUTOS_BLOQUEO} minutos."
    con.execute("UPDATE usuarios SET intentos = intentos + 1 WHERE id = ?", (fila["id"],))
    registrar(con, fila, "Intento fallido", que)
    return mensaje


def entrar(con: sqlite3.Connection, usuario: str, clave: str) -> tuple[sqlite3.Row | None, str]:
    """Devuelve (cuenta, "") si puede entrar, o (None, motivo). No lanza errores para que los
    intentos fallidos queden guardados."""
    fila = _buscar(con, usuario)
    if fila is None:
        _coincide(clave or "", _HASH_FALSO)
        return None, MAL_ENTRADA
    espera = _minutos_de_espera(fila)
    if espera:
        return None, f"Demasiados intentos. Probá de nuevo en {espera} minuto(s)."
    if not _coincide(clave or "", fila["clave_hash"]):
        return None, _anotar_fallo(con, fila, "Contraseña incorrecta al entrar", MAL_ENTRADA)
    con.execute("UPDATE usuarios SET intentos = 0, bloqueado_hasta = NULL WHERE id = ?", (fila["id"],))
    if fila["estado"] == "pendiente":
        return None, "Tu cuenta todavía espera la aprobación de un administrador."
    if fila["estado"] != "activo":
        return None, "Tu cuenta está bloqueada. Hablá con un administrador."
    return obtener(con, fila["id"]), ""


# ---------------------------------------------------------------- contraseña olvidada

def recuperar(con: sqlite3.Connection, usuario: str, codigo: str, clave: str,
              repetir: str | None = None) -> tuple[str | None, str]:
    """Cambia la contraseña con el código de recuperación. Devuelve (código nuevo, "") o
    (None, motivo). El código usado deja de servir: se entrega uno nuevo."""
    try:
        _validar_clave(clave, repetir)
    except ErrorNegocio as exc:
        return None, str(exc)
    fila = _buscar(con, usuario)
    if fila is None:
        _coincide(_limpiar_codigo(codigo), _HASH_FALSO)
        return None, MAL_CODIGO
    espera = _minutos_de_espera(fila)
    if espera:
        return None, f"Demasiados intentos. Probá de nuevo en {espera} minuto(s)."
    if not _coincide(_limpiar_codigo(codigo), fila["recuperacion_hash"]):
        return None, _anotar_fallo(con, fila, "Código de recuperación incorrecto", MAL_CODIGO)
    nuevo = nuevo_codigo()
    con.execute(
        """UPDATE usuarios SET clave_hash = ?, recuperacion_hash = ?, sesion = sesion + 1,
                               intentos = 0, bloqueado_hasta = NULL WHERE id = ?""",
        (_hash(clave), _hash(_limpiar_codigo(nuevo)), fila["id"]))
    registrar(con, fila, "Recuperó su contraseña", "Con el código de recuperación")
    return nuevo, ""


def clave_correcta(con: sqlite3.Connection, usuario_id: int, clave: str) -> bool:
    fila = obtener(con, usuario_id)
    return bool(fila) and _coincide(clave or "", fila["clave_hash"])


def cambiar_clave(con: sqlite3.Connection, usuario_id: int, actual: str, nueva: str,
                  repetir: str | None = None) -> int:
    """Cambia la contraseña sabiendo la actual. Devuelve el nuevo número de sesión."""
    fila = obtener(con, usuario_id)
    if not fila or not _coincide(actual or "", fila["clave_hash"]):
        raise ErrorNegocio("La contraseña actual no es correcta.")
    _validar_clave(nueva, repetir)
    con.execute("UPDATE usuarios SET clave_hash = ?, sesion = sesion + 1 WHERE id = ?",
                (_hash(nueva), usuario_id))
    return fila["sesion"] + 1


def regenerar_codigo(con: sqlite3.Connection, usuario_id: int) -> str:
    """Código de recuperación nuevo; el anterior deja de servir."""
    if not obtener(con, usuario_id):
        raise ErrorNegocio("Esa cuenta no existe.")
    codigo = nuevo_codigo()
    con.execute("UPDATE usuarios SET recuperacion_hash = ?, intentos = 0, bloqueado_hasta = NULL WHERE id = ?",
                (_hash(_limpiar_codigo(codigo)), usuario_id))
    return codigo


# ---------------------------------------------------------------- administración

def _exigir_un_administrador(con: sqlite3.Connection) -> None:
    if not con.execute("SELECT 1 FROM usuarios WHERE rol = 'admin' AND estado = 'activo'").fetchone():
        raise ErrorNegocio("Tiene que quedar al menos un administrador activo.")


def cambiar_estado(con: sqlite3.Connection, usuario_id: int, estado: str):
    if estado not in ("activo", "bloqueado") or not obtener(con, usuario_id):
        raise ErrorNegocio("Esa cuenta no existe.")
    con.execute("UPDATE usuarios SET estado = ?, intentos = 0, bloqueado_hasta = NULL WHERE id = ?",
                (estado, usuario_id))
    _exigir_un_administrador(con)
    return obtener(con, usuario_id)


def cambiar_rol(con: sqlite3.Connection, usuario_id: int, rol: str):
    if rol not in ROLES or not obtener(con, usuario_id):
        raise ErrorNegocio("Esa cuenta no existe.")
    con.execute("UPDATE usuarios SET rol = ? WHERE id = ?", (rol, usuario_id))
    _exigir_un_administrador(con)
    return obtener(con, usuario_id)


# ---------------------------------------------------------------- registro de actividad

def registrar(con: sqlite3.Connection, usuario, accion: str, detalle: str = "", nombre: str = "") -> None:
    """Anota una acción. usuario: la cuenta que la hizo, o None con un nombre suelto
    (por ejemplo «Telegram: Ana» o el usuario que se escribió en un intento fallido)."""
    con.execute(
        "INSERT INTO actividad (usuario_id, usuario, accion, detalle) VALUES (?, ?, ?, ?)",
        (usuario["id"] if usuario else None, usuario["nombre"] if usuario else nombre[:80],
         accion, (detalle or "")[:500]))


def actividad(con: sqlite3.Connection, usuario_id=None, desde: str = "", hasta: str = "",
              limite: int = 500):
    filtros, valores = [], []
    if usuario_id:
        filtros.append("usuario_id = ?")
        valores.append(int(usuario_id))
    if desde:
        filtros.append("date(fecha) >= ?")
        valores.append(desde)
    if hasta:
        filtros.append("date(fecha) <= ?")
        valores.append(hasta)
    donde = (" WHERE " + " AND ".join(filtros)) if filtros else ""
    return con.execute(f"SELECT * FROM actividad{donde} ORDER BY id DESC LIMIT ?",
                       (*valores, limite)).fetchall()
