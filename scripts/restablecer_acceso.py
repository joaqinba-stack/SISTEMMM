"""Rescate de una cuenta desde la computadora donde corre el sistema.

Uso:  python scripts/restablecer_acceso.py USUARIO  [--admin]

Sirve cuando nadie puede entrar para arreglarlo desde la página (por ejemplo, el único
administrador olvidó su contraseña y perdió su código de recuperación). Genera un código de
recuperación nuevo para esa cuenta y lo muestra; después se usa en «Olvidé mi contraseña».

Con --admin además deja la cuenta habilitada y como administradora.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, usuarios  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera un código de recuperación nuevo para una cuenta.")
    parser.add_argument("usuario")
    parser.add_argument("--admin", action="store_true", help="dejarla habilitada y como administradora")
    args = parser.parse_args()

    db.inicializar()
    with db.conexion() as con:
        cuenta = con.execute("SELECT * FROM usuarios WHERE usuario = ?",
                             (args.usuario.strip().lower(),)).fetchone()
        if not cuenta:
            existentes = ", ".join(c["usuario"] for c in usuarios.listar(con)) or "ninguna"
            sys.exit(f"No hay una cuenta «{args.usuario}». Cuentas que existen: {existentes}.")
        if args.admin:
            con.execute("UPDATE usuarios SET rol = 'admin', estado = 'activo' WHERE id = ?", (cuenta["id"],))
        codigo = usuarios.regenerar_codigo(con, cuenta["id"])
        usuarios.registrar(con, None, "Rescate de cuenta desde la computadora",
                           f"{cuenta['nombre']} ({cuenta['usuario']})" + (" · ahora administra" if args.admin else ""),
                           nombre="Sistema")
    print(f"Código de recuperación de {cuenta['usuario']}: {codigo}")
    print("Usalo en la pantalla de entrada, en «Olvidé mi contraseña». El código anterior ya no sirve.")


if __name__ == "__main__":
    main()
