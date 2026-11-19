"""
Cuentas en las que el bot OPERO (hizo el clic de Comprar/Vender), para que
"close all" las cierre aunque no esten en "cuentas".

POR QUE. Desde que la orden puede nombrar la cuenta por su NUMERO completo
(18/11/2026), una van opera cuentas que no figuran en config.local.json: el
numero va al login de su firma ("logins"). "all" sale de la lista
configurada, asi que esas quedaban afuera del "close all" (pedido del
usuario, 19/11/2026: "ya en telegram habria mandado una lista con todas las
ordenes y asi saber cuales tienen trades").

QUE SE ANOTA. Cada cuenta en la que se hizo el clic (confirmada o no: una
orden sin verificar tambien puede haber dejado posicion), con la fecha. En
dry_run no hay clic y no se anota nada.

CUANDO SALE. Cuando un cierre la deja verificada sin posiciones (o no tenia
nada), o a los DIAS_VIGENCIA sin operarla: una cuenta muerta no puede quedar
para siempre en el "close all" dando error.

Es estado de ESTA maquina (como programadas.json): no se versiona. Un archivo
roto no puede tumbar nada: se lee como vacio.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("bot_futuros.operadas")

ARCHIVO = Path(__file__).parent / "operadas.json"
DIAS_VIGENCIA = 5


def _leer() -> dict:
    if not ARCHIVO.exists():
        return {}
    try:
        datos = json.loads(ARCHIVO.read_text(encoding="utf-8"))
        return datos if isinstance(datos, dict) else {}
    except (OSError, ValueError):
        log.exception(f"No se pudo leer {ARCHIVO.name}; se ignora.")
        return {}


def _escribir(datos: dict):
    try:
        ARCHIVO.write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        log.exception(f"No se pudo escribir {ARCHIVO.name}")


def anotar(aliases, ahora: datetime | None = None):
    """Que esas cuentas se operaron ahora."""
    aliases = [a for a in aliases if a]
    if not aliases:
        return
    datos = _leer()
    sello = (ahora or datetime.now()).isoformat(timespec="seconds")
    for alias in aliases:
        datos[alias] = sello
    _escribir(datos)


def olvidar(aliases):
    """Que esas cuentas ya no tienen nada abierto."""
    datos = _leer()
    quitar = [a for a in aliases if a in datos]
    if quitar:
        for alias in quitar:
            del datos[alias]
        _escribir(datos)


def vigentes(ahora: datetime | None = None) -> list:
    """Las operadas en los ultimos DIAS_VIGENCIA dias, de la mas vieja a la
    mas nueva. Las vencidas (y las ilegibles) se borran del archivo."""
    ahora = ahora or datetime.now()
    limite = ahora - timedelta(days=DIAS_VIGENCIA)
    datos = _leer()
    siguen = {}
    for alias, sello in datos.items():
        try:
            cuando = datetime.fromisoformat(sello)
        except (TypeError, ValueError):
            continue
        if cuando >= limite:
            siguen[alias] = sello
    if siguen != datos:
        _escribir(siguen)
    return sorted(siguen, key=lambda a: siguen[a])
