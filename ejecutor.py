"""
Interfaz del ejecutor y la maquinaria que comparten todos: candado de
operacion, aborto cooperativo y la politica de lotes.

El listener nunca sabe COMO se coloca una orden: llama a
`EJECUTOR.colocar_lote(trabajos)` y `EJECUTOR.cerrar_cuentas(aliases)` desde
`asyncio.to_thread`. En la Etapa 1 el ejecutor es el falso (solo log); en la
Etapa 2 sera el web, con su propio hilo dueno de Playwright.

POLITICA DE LOTE (la misma para todos los ejecutores, por eso vive aca):
  - Antes de cada orden se revisa el aborto. Un "stop" corta la orden en
    curso en su proximo punto seguro y NO se intenta ninguna de las que faltan.
  - Si una orden de una cuenta no se confirma (o falla), no se coloca NINGUNA
    orden mas en ESA cuenta en este lote: no sabemos en que estado quedo, y
    seguir es como duplicar a ciegas. Las otras cuentas siguen.
  - Nunca se reintenta un clic.

MARCAS EN EL LOG: cada operacion escribe MARCA_INICIO al empezar y MARCA_FIN
en un `finally`. actualizar.ps1 las usa para negarse a parar el listener con
una orden a medias. En MT5 la linea de fin solo se escribia en el camino
feliz, y una orden abortada dejaba la maquina "en vuelo" toda la tarde.
"""

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

from ordenes import etiqueta_de

log = logging.getLogger("bot_futuros.ejecutor")

MARCA_INICIO = "INICIO OPERACION"
MARCA_FIN = "FIN OPERACION"

# Serializa las operaciones: si una orden programada vence mientras corre un
# lote, espera su turno. RLock porque una operacion puede llamar a otra en el
# mismo hilo.
_OPERACION_LOCK = threading.RLock()

# Bandera de aborto: la levanta el "stop". La cancelacion es COOPERATIVA: el
# flujo la consulta en puntos seguros y, sobre todo, JUSTO ANTES del clic de
# Comprar/Vender. Cortar antes de ese clic significa que no se opero nada.
_ABORTO = threading.Event()


class OperacionAbortada(RuntimeError):
    """Se pidio parar mientras la operacion estaba en curso."""


def solicitar_aborto():
    _ABORTO.set()
    log.warning("ABORTO solicitado: se cortara en el proximo punto seguro.")


def limpiar_aborto():
    _ABORTO.clear()


def aborto_pedido() -> bool:
    return _ABORTO.is_set()


def limpiar_aborto_cuando_libre(timeout: float = 300.0) -> bool:
    """Baja la bandera recien cuando NO queda ninguna operacion en curso.

    Con un plazo fijo (lo que hacia MT5 al principio) la bandera se bajaba a
    mitad de un lote y las ordenes que faltaban se colocaban igual.
    Devuelve False si se agoto `timeout` (y la baja igual, para no dejar al
    bot mudo para siempre).
    """
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if _OPERACION_LOCK.acquire(blocking=False):
            try:
                _ABORTO.clear()
                return True
            finally:
                _OPERACION_LOCK.release()
        time.sleep(0.2)
    log.warning("Se agoto la espera para rehabilitar tras el aborto; se baja "
                "la bandera igual para no dejar el bot mudo.")
    _ABORTO.clear()
    return False


def hay_operacion_en_curso() -> bool:
    """True si hay una operacion corriendo. Lo usa `update` para no matar el
    listener con una orden a medio llenar.

    Se llama desde el hilo del handler, que nunca tiene el candado (es
    reentrante: desde el hilo que lo tiene daria False).
    """
    if _OPERACION_LOCK.acquire(blocking=False):
        _OPERACION_LOCK.release()
        return False
    return True


def chequear_aborto(donde: str = ""):
    """Corta la operacion si se pidio parar. Se llama en puntos seguros."""
    if _ABORTO.is_set():
        raise OperacionAbortada(
            f"Operacion abortada a pedido{' (' + donde + ')' if donde else ''}. "
            "No se hizo el clic final.")


@contextmanager
def operacion(descripcion: str):
    """Toma el candado y deja las marcas de inicio y fin en el log."""
    if not _OPERACION_LOCK.acquire(blocking=False):
        log.info("Otra operacion en curso; '%s' espera su turno.", descripcion)
        _OPERACION_LOCK.acquire()
    log.info(f"{MARCA_INICIO}: {descripcion}")
    try:
        yield
    finally:
        log.info(f"{MARCA_FIN}: {descripcion}")
        _OPERACION_LOCK.release()


@dataclass
class ResultadoOrden:
    etiqueta: str
    cuenta: str
    # None = no se hizo clic (dry_run, ejecutor falso, o no se intento).
    # True/False = se hizo clic y se pudo / no se pudo verificar la posicion.
    confirmado: bool | None = None
    elapsed: float = 0.0
    detalle: str = ""
    omitida: bool = False       # no se intento: aborto o cuenta bloqueada
    error: str | None = None    # fallo antes de poder confirmar nada

    @property
    def bloquea_la_cuenta(self) -> bool:
        return self.confirmado is False or self.error is not None


class EjecutorBase:
    """Lo comun a todos los ejecutores. Las subclases implementan
    `_colocar_una` y `_cerrar_cuenta`; nada mas."""

    nombre = "base"

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run

    # --- ordenes ------------------------------------------------------------

    def colocar_lote(self, trabajos: list) -> list:
        """Coloca los trabajos en orden. Devuelve un ResultadoOrden por cada
        uno, incluidos los que no se intentaron (y por que)."""
        with operacion(f"lote de {len(trabajos)} orden(es)"):
            return self._recorrer_lote(trabajos)

    def _recorrer_lote(self, trabajos: list) -> list:
        """La politica del lote, SIN tomar el candado (lo toma quien llama).
        El ejecutor web la corre en su hilo del navegador."""
        resultados = []
        bloqueadas = {}            # alias -> motivo
        for i, trabajo in enumerate(trabajos, start=1):
            etiqueta = etiqueta_de(i, trabajo)
            alias = trabajo["cuenta"]

            if aborto_pedido():
                log.warning(f"ABORTO: no se coloca la orden #{i} ni las siguientes.")
                resultados.extend(self._omitidas(trabajos, i, "abortado con stop"))
                break

            if alias in bloqueadas:
                log.warning(f"Orden #{i} NO se intenta: la cuenta {alias} quedo "
                            f"bloqueada en este lote ({bloqueadas[alias]}).")
                resultados.append(ResultadoOrden(
                    etiqueta, alias, omitida=True,
                    detalle=f"cuenta bloqueada en este lote: {bloqueadas[alias]}"))
                continue

            t0 = time.monotonic()
            try:
                resultado = self._colocar_una(trabajo, etiqueta)
            except OperacionAbortada as e:
                log.warning(f"ABORTO antes del clic final: la orden #{i} NO se coloco.")
                resultados.append(ResultadoOrden(
                    etiqueta, alias, omitida=True, detalle=str(e),
                    elapsed=time.monotonic() - t0))
                resultados.extend(self._omitidas(trabajos, i + 1, "abortado con stop"))
                break
            except Exception as e:
                log.exception(f"Fallo colocando la orden #{i} ({etiqueta})")
                resultado = ResultadoOrden(etiqueta, alias, error=str(e) or type(e).__name__)
            resultado.elapsed = resultado.elapsed or (time.monotonic() - t0)

            if resultado.bloquea_la_cuenta:
                motivo = (f"la orden #{i} " +
                          ("fallo" if resultado.error else "no se confirmo"))
                bloqueadas[alias] = motivo
                log.error(f"Cuenta {alias} bloqueada por el resto del lote: {motivo}.")
            resultados.append(resultado)
        return resultados

    @staticmethod
    def _omitidas(trabajos, desde: int, motivo: str) -> list:
        return [ResultadoOrden(etiqueta_de(n, t), t["cuenta"], omitida=True, detalle=motivo)
                for n, t in enumerate(trabajos, start=1) if n >= desde]

    def _colocar_una(self, trabajo: dict, etiqueta: str) -> ResultadoOrden:
        raise NotImplementedError

    # --- cierres ------------------------------------------------------------

    def cerrar_cuentas(self, aliases: list) -> dict:
        """{alias: posiciones cerradas, o None si fallo}. Un fallo en una
        cuenta no corta las demas; un aborto si."""
        resultados = {}
        with operacion(f"cierre de {', '.join(aliases)}"):
            for alias in aliases:
                try:
                    chequear_aborto(f"antes de cerrar {alias}")
                    resultados[alias] = self._cerrar_cuenta(alias)
                except OperacionAbortada:
                    log.warning(f"ABORTO: no se cierra {alias} ni las siguientes.")
                    break
                except Exception:
                    log.exception(f"Fallo cerrando la cuenta {alias}")
                    resultados[alias] = None
        return resultados

    def _cerrar_cuenta(self, alias: str) -> int:
        raise NotImplementedError

    # --- salud --------------------------------------------------------------

    def salud(self) -> list:
        """Lineas cortas para el latido. NUNCA puede tener efectos ni
        bloquear: lo llama otro hilo mientras se opera."""
        return []

    def cerrar(self):
        """Libera recursos al apagar."""


def crear_ejecutor(config) -> EjecutorBase:
    """El ejecutor de verdad: el web. Crearlo NO abre ningun navegador ni
    importa Playwright; eso pasa recien con la primera orden.

    El falso (ejecutor_falso.py) queda para los tests.
    """
    from ejecutor_web import EjecutorWeb
    return EjecutorWeb(config)
