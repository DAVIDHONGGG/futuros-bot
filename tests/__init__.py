"""Red de seguridad de la suite: ningun test puede salir a la red, lanzar el
actualizador, abrir un navegador real, escribir el programadas.json real ni
depender de la config de ESTA maquina.

POR QUE EXISTE. La suite no corre solo en la PC de desarrollo: es el
SEMAFORO del update. actualizar.ps1 la ejecuta en cada maquina antes de
aceptar el codigo nuevo, y ahi hay cuentas reales y un listener que acaba de
pararse. En MT5 los tests llegaron a tocar el teclado y a cerrar MT5 de
verdad; mockear test por test no alcanzo, porque el que escribe un test
nuevo no tiene por que acordarse.

COMO SE GARANTIZA QUE ESTO CORRA. Medido con Python 3.12: con
`unittest discover -s tests` (sin -t) este __init__ NO se importa -los
tests se cargan como modulos sueltos-. Por eso:
  - todo archivo de test importa `tests.apoyo`, que importa este paquete
    (y test_red_de_seguridad.py verifica que ningun archivo se lo olvide);
  - los comandos documentados usan `-s tests -t .`.
"""

import atexit
import logging
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path
from types import SimpleNamespace

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

# main() llama a logging.basicConfig; con un handler ya puesto no hace nada y
# la salida de la suite queda limpia.
logging.getLogger().addHandler(logging.NullHandler())


# 1. Nada sale a la red (avisos.py usa urllib para hablarle a Telegram).
def _urlopen_bloqueado(*args, **kwargs):
    raise RuntimeError("Un test intento salir a la red (urlopen). Mockealo.")


urllib.request.urlopen = _urlopen_bloqueado


# 2. Ninguna sesion de tests escribe en el programadas.json REAL: un test
#    que programe algo sin parchear dejaria ordenes falsas que el listener
#    de verdad rearmaria al arrancar.
DIR_TEMPORAL = Path(tempfile.mkdtemp(prefix="botfut-tests-"))
atexit.register(shutil.rmtree, DIR_TEMPORAL, True)

import listener_futuros as _lf  # noqa: E402

_lf.ARCHIVO_PROGRAMADAS = DIR_TEMPORAL / "programadas.json"


# 3. Ningun test lanza actualizar.ps1 (pararia el listener real y haria git
#    pull). Los tests del update parchean _lf.subprocess.Popen.
def _popen_bloqueado(*args, **kwargs):
    raise RuntimeError("Un test intento lanzar un proceso (actualizar.ps1). Mockealo.")


_lf.subprocess = SimpleNamespace(Popen=_popen_bloqueado)


# 4. Ningun test abre un navegador REAL. Los del ejecutor web le pasan un
#    Playwright falso (tests/navegador_falso.py); lo que llegue aca es una
#    fuga. Va antes de crear cualquier ejecutor.
import ejecutor_web as _ew  # noqa: E402


def _playwright_bloqueado():
    raise RuntimeError("Un test intento abrir un navegador REAL. Pasale fabrica= "
                       "con el Playwright falso de tests/navegador_falso.py.")


_ew._fabrica_real = _playwright_bloqueado


# 5. Config NEUTRA: la suite no puede depender del config.local.json de esta
#    maquina (prefijo, cuentas, dry_run...).
from tests.apoyo import config_de_prueba  # noqa: E402

CONFIG_NEUTRA = config_de_prueba()
_lf.aplicar_config(CONFIG_NEUTRA)

RED_ACTIVA = True
