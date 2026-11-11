"""
Diagnostico de una maquina: revisa lo que el bot necesita y dice que falta.

    python check_setup.py

No abre navegadores ni coloca ordenes: solo mira. Devuelve codigo de salida 1
si falta algo critico (run_listener.bat no arranca el listener en ese caso).

Etapa 1: librerias, config, token, prefijo, usuarios, modo y advertencias de
config. La Etapa 3 suma Playwright, Chromium, plataformas y perfiles.
"""

import importlib
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent

# Un token de @BotFather es "<id numerico>:<clave>". Solo se valida la forma
# (el diagnostico no toca la red): alcanza para descartar el placeholder.
_TOKEN_PATTERN = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")


def _token_con_forma_valida(token: str) -> bool:
    return bool(_TOKEN_PATTERN.match(str(token).strip()))


OK, AVISO, FALLA = "OK  ", "AVISO", "FALLA"
_resultados = []


def _chequeo(estado, titulo, detalle=""):
    _resultados.append((estado, titulo))
    linea = f"[{estado}] {titulo}"
    if detalle:
        linea += f"\n         {detalle}"
    print(linea)


def revisar_dependencias() -> bool:
    faltantes = []
    for modulo, paquete in [("telegram", "python-telegram-bot")]:
        try:
            importlib.import_module(modulo)
        except ImportError:
            faltantes.append(paquete)
    if faltantes:
        _chequeo(FALLA, f"Faltan librerias de Python: {', '.join(faltantes)}",
                 "Instalalas con:  python -m pip install -r requirements.txt")
        return False
    _chequeo(OK, "Librerias de Python instaladas")
    return True


def revisar_config(config):
    if not config.fuentes:
        _chequeo(AVISO, "No hay archivo de configuracion",
                 "Copia config.example.json a config.local.json (o usa configurar.bat).")
    else:
        _chequeo(OK, "Configuracion cargada", config.resumen())

    token = config.telegram_bot_token
    if not token:
        _chequeo(FALLA, "Falta el token del bot de Telegram",
                 'Ponelo como "telegram_bot_token" en config.local.json.')
    elif not _token_con_forma_valida(token):
        _chequeo(FALLA, "El token del bot de Telegram no tiene forma de token",
                 'Parece el placeholder de config.example.json. Un token de '
                 '@BotFather es "<numero>:<letras y numeros>".')
    else:
        _chequeo(OK, "Token del bot de Telegram configurado")

    if not config.prefijo_telegram:
        _chequeo(FALLA, "Falta prefijo_telegram",
                 "Es obligatorio en este bot: sin prefijo el listener no arranca.")
    else:
        _chequeo(OK, f"Prefijo en el grupo: {config.prefijo_telegram}")

    autorizados = sorted(config.telegram_allowed_user_ids)
    if not autorizados:
        _chequeo(AVISO, "No hay usuarios autorizados de Telegram",
                 "El bot le contesta su user id a quien le escriba.")
    else:
        _chequeo(OK, f"Usuarios autorizados ({len(autorizados)})",
                 ", ".join(str(i) for i in autorizados)
                 + "  <- todos pueden operar de verdad")

    if not config.cuentas:
        _chequeo(AVISO, "No hay cuentas configuradas",
                 'Agrega "cuentas" en config.local.json (ver config.example.json).')
    else:
        _chequeo(OK, f"Cuentas: {', '.join(sorted(config.cuentas))}",
                 f"por defecto: {config.cuenta_por_defecto or '- (hay que nombrarla en cada orden)'}")

    for advertencia in config.advertencias:
        _chequeo(AVISO, "Config", advertencia)

    if config.dry_run:
        _chequeo(AVISO, "Modo: DRY_RUN (llena las ordenes pero NO hace clic)")
    else:
        _chequeo(OK, "Modo: EN VIVO (coloca ordenes reales)")


def main() -> int:
    print(f"Diagnostico de la instalacion  ({BASE_DIR})")
    print(f"Python {sys.version.split()[0]}  ({sys.executable})")
    print("-" * 70)

    if not revisar_dependencias():
        print("-" * 70)
        print("Instala las librerias que faltan y volve a correr esto.")
        return 1

    # Si el config existe pero no se puede leer, config.py aborta al
    # importarse. Se corta aca: sin config no se puede confiar en el resto,
    # empezando por si DRY_RUN esta activo.
    try:
        from config import CONFIG
    except Exception as e:
        _chequeo(FALLA, "El archivo de configuracion no se puede leer", str(e))
        print("-" * 70)
        print("Corregi (o borra) ese archivo y volve a correr esto.")
        return 1

    revisar_config(CONFIG)

    print("-" * 70)
    fallas = sum(1 for estado, _ in _resultados if estado == FALLA)
    avisos = sum(1 for estado, _ in _resultados if estado == AVISO)
    if fallas:
        print(f"{fallas} problema(s) critico(s) y {avisos} aviso(s). "
              "El bot NO va a funcionar hasta resolver los criticos.")
        return 1
    print(f"Todo lo critico esta en orden ({avisos} aviso(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
