"""Escribe el token del bot y el user id autorizado en config.local.json.

Copiado del bot de MT5. Existe para no editar el JSON a mano y evitar los
tres errores que alla costaron una sesion entera:

  1. `Set-Content -Encoding utf8` de PowerShell 5.1 escribe un BOM.
  2. getpass no muestra NADA al pegar, asi que parece colgado y se corta.
  3. Pegar el token en el prompt equivocado.

    python configurar_telegram.py --portapapeles   toma el token del portapapeles
    python configurar_telegram.py --token          lo pide por teclado (oculto)
    python configurar_telegram.py --user-id        solo el user id
    python configurar_telegram.py                  token + user id

Si config.local.json no existe, se crea a partir de config.example.json.

Diferencia con MT5: el portapapeles se lee con PowerShell (Get-Clipboard) en
vez de pyperclip, que alla venia con pyautogui y aca no hace falta.
"""

import getpass
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

from check_setup import _token_con_forma_valida

BASE_DIR = Path(__file__).parent
LOCAL = BASE_DIR / "config.local.json"
EJEMPLO = BASE_DIR / "config.example.json"


def _cargar() -> dict:
    if not LOCAL.exists():
        if not EJEMPLO.exists():
            sys.exit(f"No existe {LOCAL.name} ni {EJEMPLO.name}.")
        shutil.copy(EJEMPLO, LOCAL)
        print(f"{LOCAL.name} no existia; se creo a partir de {EJEMPLO.name}.")
    with open(LOCAL, encoding="utf-8-sig") as f:
        return json.load(f)


def _guardar(datos: dict):
    """Sin BOM y a un temporal primero: un fallo a mitad no deja el config
    truncado (con el config ilegible el bot no arranca)."""
    tmp = LOCAL.with_suffix(".json.tmp")
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(datos, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(LOCAL)


def _guardar_token(datos: dict, token: str, origen: str) -> bool:
    """Valida y guarda, sin imprimir NUNCA el token entero."""
    if not token:
        print(f"  ERROR: {origen} esta vacio. No se guardo nada.")
        return False
    if not _token_con_forma_valida(token):
        print(f"  ERROR: {origen} no tiene forma de token de BotFather "
              f"(largo {len(token)}). No se guardo nada.")
        if len(token) > 60:
            print("  Ojo: parece un comando o texto largo, no un token. "
                  "Copia SOLO el token desde @BotFather.")
        return False
    datos["telegram_bot_token"] = token
    print(f"  OK: guardado (termina en ...{token[-6:]}).")
    return True


def _leer_portapapeles() -> str:
    resultado = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return (resultado.stdout or "").strip()


def token_del_portapapeles(datos: dict) -> bool:
    print("\nLeyendo el token del portapapeles...")
    try:
        token = _leer_portapapeles()
    except (OSError, subprocess.SubprocessError) as e:
        print(f"  ERROR: no se pudo leer el portapapeles ({e}).")
        return False
    return _guardar_token(datos, token, "el portapapeles")


def pedir_token(datos: dict) -> bool:
    print("\nToken de @BotFather (NO se muestra al escribirlo; es normal que "
          "no veas nada al pegar).")
    print("Formato: <numero>:<letras y numeros>, por ejemplo 123456789:AAH...")
    return _guardar_token(datos, getpass.getpass("  Token: ").strip(),
                          "lo que escribiste")


def pedir_user_id(datos: dict) -> bool:
    actual = datos.get("telegram_allowed_user_id", [])
    print(f"\nUser id numerico autorizado en ESTA maquina (actual: {actual}).")
    print("Se SUMA a los de config.json. Si no lo sabes: mandale cualquier "
          "mensaje a @userinfobot en Telegram y te lo responde.")
    valor = input("  User id: ").strip()
    if not valor:
        print("  Sin cambios.")
        return False
    try:
        nuevo = int(valor)
    except ValueError:
        print("  ERROR: eso no es un numero entero. No se guardo nada.")
        return False
    lista = actual if isinstance(actual, list) else ([actual] if actual else [])
    if nuevo not in lista:
        lista.append(nuevo)
    datos["telegram_allowed_user_id"] = lista
    print(f"  Guardado: {lista}.")
    return True


def main() -> int:
    portapapeles = "--portapapeles" in sys.argv
    solo_token = "--token" in sys.argv
    solo_user = "--user-id" in sys.argv
    ambos = not (solo_token or solo_user or portapapeles)

    datos = _cargar()
    cambio = False
    if portapapeles:
        cambio |= token_del_portapapeles(datos)
    if ambos or solo_token:
        cambio |= pedir_token(datos)
    if ambos or solo_user:
        cambio |= pedir_user_id(datos)

    if not cambio:
        print("\nNo se modifico config.local.json.")
        return 1

    _guardar(datos)
    print(f"\n{LOCAL.name} actualizado. Verifica con:  python check_setup.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
