"""
Ejecutor WEB: coloca las ordenes en la terminal web de cada firma con
Playwright.

UN HILO DUENO DEL NAVEGADOR. El listener llama al ejecutor con
asyncio.to_thread, que usa un hilo DISTINTO en cada llamada, y la API sync de
Playwright solo funciona en el hilo que la creo. Por eso todo lo que toca
Playwright corre en un unico hilo (HiloNavegador) que recibe trabajos por una
cola; el que llama espera el resultado con un Future.

El candado de operacion se toma en el hilo que LLAMA, antes de encolar: asi
"hay una operacion en curso" cubre tambien el rato en que el lote espera su
turno en la cola, y el "stop" no baja la bandera de aborto antes de tiempo.

FLUJO DE UNA ORDEN (en el hilo del navegador):
  1. pagina de la cuenta (perfil persistente propio; se abre la primera vez
     y se reusa). Sin sesion iniciada -> error: el login lo hace el operador
     a mano con abrir_perfil.py; este bot nunca maneja contrasenas.
  2. pasos_seleccionar_cuenta (si la plataforma los tiene).
  3. leer el panel de posiciones ANTES. Si no se puede leer, NO se hace clic:
     sin eso no hay forma de verificar despues.
  4. pasos_orden (simbolo, cantidad, SL y TP en ticks...). Antes de CADA paso
     se revisa el aborto.
  5. ultima revision del aborto, JUSTO antes del clic de Comprar/Vender.
     dry_run: aca se detiene, guarda una captura en logs/diagnosticos/ y
     limpia el ticket. NO hace clic.
  6. clic. Desde aca no hay vuelta atras.
  7. pasos_confirmar (si la plataforma pide confirmar; el aborto se revisa
     antes de cada uno: ese es el clic final de verdad).
  8. verificar leyendo el panel: la CANTIDAD de la fila del contrato tiene
     que cambiar. Si no cambia en segundos_verificacion -> no confirmado, y
     EjecutorBase no coloca nada mas en esa cuenta en este lote.
  9. pasos_despues (si la plataforma los tiene), sobre la FILA de la
     posicion: en TopstepX es donde se cargan Risk y To Make. Sin mirar el
     aborto. Si fallan, o si ya habia posicion en ese contrato (el SL/TP de
     la fila es de toda la posicion), la orden queda "SIN SL/TP": alerta al
     grupo de errores y la cuenta se bloquea por el resto del lote. Nunca se
     cierra nada solo.

VENTANA. Con no_robar_foco (default) la ventana se abre FUERA de la pantalla y
nunca se trae al frente: en una PC con el bot de MT5, una ventana encima le
tapa las plantillas de pixeles y se lleva sus teclas. Y como una ventana
tapada o minimizada hace que Chromium frene timers y render (la plataforma se
quedaria con precios y posiciones viejas), se lanza con los flags que lo
desactivan. HAY QUE MEDIRLO en cada maquina (ver INSTALAR): que la plataforma
siga actualizando y que la ventana no aparezca.
"""

import logging
import queue
import threading
import time
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path

from ejecutor import EjecutorBase, OperacionAbortada, ResultadoOrden, chequear_aborto, operacion
from pasos import (ejecutar_pasos, fila_de, leer_posiciones, texto_de_fila,
                   validar_plataforma, valores_de)

log = logging.getLogger("bot_futuros.ejecutor_web")

BASE_DIR = Path(__file__).parent
DIR_DIAGNOSTICOS = BASE_DIR / "logs" / "diagnosticos"
MAXIMO_CAPTURAS = 300
PAUSA_VERIFICACION_MS = 250

# Que Chromium no frene una pagina que no se ve: sin esto, con la ventana
# tapada o fuera de pantalla, la plataforma podria dejar de actualizar.
ARGS_SIN_FRENOS = [
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
]
# Ventana fuera de la pantalla visible.
ARGS_NO_ROBAR_FOCO = [
    "--window-position=-32000,-32000",
    "--window-size=1600,1000",
]


def _fabrica_real():
    """Arranca Playwright. Se importa aca adentro a proposito: crear el
    ejecutor (lo hace el listener al importarse) no necesita Playwright, y
    los tests reemplazan esta funcion para que nunca se abra un navegador."""
    from playwright.sync_api import sync_playwright
    return sync_playwright().start()


class HiloNavegador:
    """El unico hilo que toca Playwright. Se crea al primer trabajo."""

    def __init__(self):
        self._cola = queue.Queue()
        self._hilo = None
        self._candado = threading.Lock()

    @property
    def hilo(self):
        return self._hilo

    def vivo(self) -> bool:
        return self._hilo is not None and self._hilo.is_alive()

    def ejecutar(self, fn, *args):
        """Corre fn(*args) en el hilo del navegador y devuelve su resultado
        (o levanta su excepcion)."""
        if threading.current_thread() is self._hilo:
            return fn(*args)          # ya estamos adentro: encolar seria deadlock
        with self._candado:
            if not self.vivo():
                self._hilo = threading.Thread(target=self._bucle, name="navegador",
                                              daemon=True)
                self._hilo.start()
        futuro = Future()
        self._cola.put((fn, args, futuro))
        return futuro.result()

    def _bucle(self):
        while True:
            item = self._cola.get()
            if item is None:
                return
            fn, args, futuro = item
            if not futuro.set_running_or_notify_cancel():
                continue
            try:
                futuro.set_result(fn(*args))
            except BaseException as e:      # el hilo no puede morirse por un trabajo
                futuro.set_exception(e)

    def apagar(self, timeout: float = 30.0):
        if self.vivo():
            self._cola.put(None)
            self._hilo.join(timeout)


class EjecutorWeb(EjecutorBase):
    nombre = "web"

    def __init__(self, config, fabrica=None, dir_diagnosticos=None):
        super().__init__(dry_run=config.dry_run)
        self.config = config
        self.opciones = config.navegador
        self._fabrica = fabrica
        self._dir_diagnosticos = Path(dir_diagnosticos) if dir_diagnosticos else DIR_DIAGNOSTICOS
        self._hilo = HiloNavegador()
        # Solo los toca el hilo del navegador.
        self._pw = None
        self._contextos = {}          # ruta del perfil -> BrowserContext
        # Para el latido: se leen desde otro hilo, por eso son datos simples.
        self._ultimo_error = None

    # --- interfaz -----------------------------------------------------------

    def colocar_lote(self, trabajos: list) -> list:
        with operacion(f"lote de {len(trabajos)} orden(es)"):
            return self._hilo.ejecutar(self._recorrer_lote, trabajos)

    def _cerrar_cuenta(self, alias: str) -> int:
        raise NotImplementedError(
            "los cierres por la web llegan en la Etapa 3; cerra a mano en la plataforma")

    def salud(self) -> list:
        """Sin tocar el navegador: lo llama el latido desde otro hilo."""
        lineas = [f"ejecutor web, {len(self._contextos)} navegador(es) abierto(s)"]
        if self._ultimo_error:
            lineas.append(f"ultimo error: {self._ultimo_error}")
        return lineas

    def cerrar(self):
        if self._hilo.vivo():
            try:
                self._hilo.ejecutar(self._cerrar_todo)
            except Exception:
                log.exception("No se pudo cerrar el navegador limpio.")
            self._hilo.apagar()

    # --- una orden (hilo del navegador) ------------------------------------

    def _colocar_una(self, trabajo: dict, etiqueta: str) -> ResultadoOrden:
        alias = trabajo["cuenta"]
        cuenta = self.config.cuentas.get(alias)
        if cuenta is None:
            raise ValueError(f"cuenta {alias} desconocida en esta maquina")
        nombre_plataforma = cuenta.plataforma
        plataforma = self.config.plataformas.get(nombre_plataforma)
        problemas = validar_plataforma(nombre_plataforma, plataforma)
        if problemas:
            extra = f" (y {len(problemas) - 3} mas)" if len(problemas) > 3 else ""
            raise ValueError("plataforma sin configurar: " + "; ".join(problemas[:3]) + extra)

        to = self.opciones["timeout_ms"]
        valores = valores_de(trabajo, cuenta)
        comprar = trabajo["accion"] == "buy"
        boton = plataforma["boton_comprar" if comprar else "boton_vender"]
        nombre_boton = "Comprar" if comprar else "Vender"
        en_usd = (f" = riesgo {trabajo['riesgo_usd']} / ganancia {trabajo['ganancia_usd']} USD"
                  if trabajo.get("riesgo_usd") else "")
        log.info(f"Iniciando orden web {etiqueta} [{nombre_plataforma}, "
                 f"{trabajo['contrato']}, SL {trabajo['sl_ticks']} TP {trabajo['tp_ticks']} "
                 f"ticks{en_usd}]")

        page = self._pagina(cuenta, plataforma)

        # --- hasta el clic: todo se puede deshacer -------------------------
        try:
            if plataforma.get("pasos_seleccionar_cuenta"):
                ejecutar_pasos(page, plataforma["pasos_seleccionar_cuenta"], valores, to,
                               "seleccionar cuenta")
            try:
                antes = leer_posiciones(page, plataforma["posiciones"],
                                        texto_de_fila(plataforma["posiciones"], valores), to)
            except OperacionAbortada:
                raise
            except Exception as e:
                raise RuntimeError(
                    f"no se pudo leer el panel de posiciones ({e}); sin eso no se "
                    f"puede verificar la orden, asi que NO se hace clic") from e
            ejecutar_pasos(page, plataforma["pasos_orden"], valores, to, "orden")
            page.locator(boton).wait_for(state="visible", timeout=to)
            chequear_aborto(f"antes del clic de {nombre_boton}")

            if self.dry_run:
                captura = self._capturar(page, alias, "dryrun")
                log.warning(f"DRY_RUN activo: NO se hizo clic en {nombre_boton} ({etiqueta}). "
                            f"Captura: {captura}")
                if plataforma.get("pasos_despues"):
                    log.info("DRY_RUN: el SL/TP (pasos_despues) se carga en la fila de la "
                             "posicion y sin posicion no se puede probar.")
                self._limpiar(page, plataforma, valores)
                return ResultadoOrden(
                    etiqueta, alias, confirmado=None,
                    detalle=(f"dry_run, ticket lleno sin clic; posicion antes: "
                             f"{list(antes) or 'ninguna'}"))
        except BaseException as e:
            # Nada se clickeo: foto del momento exacto, ticket limpio, y que
            # EjecutorBase decida (aborto -> omitida; error -> bloquea cuenta).
            if not isinstance(e, OperacionAbortada):
                self._ultimo_error = f"{alias}: {e}"[:200]
            self._capturar(page, alias, "aborto" if isinstance(e, OperacionAbortada) else "error")
            self._limpiar(page, plataforma, valores)
            raise

        # --- el clic: desde aca no hay vuelta atras ------------------------
        try:
            page.locator(boton).click(timeout=to)
        except Exception as e:
            self._ultimo_error = f"{alias}: clic fallo: {e}"[:200]
            self._capturar(page, alias, "clic-fallo")
            log.error(f"No se pudo hacer clic en {nombre_boton} ({etiqueta}): {e}")
            return ResultadoOrden(etiqueta, alias,
                                  error=f"no se pudo hacer clic en {nombre_boton}: {e}")
        log.warning(f"Clic ejecutado en {nombre_boton}: {etiqueta}")

        try:
            if plataforma.get("pasos_confirmar"):
                ejecutar_pasos(page, plataforma["pasos_confirmar"], valores, to, "confirmar")
            confirmado, despues = self._verificar(page, plataforma, trabajo, antes)
        except OperacionAbortada:
            # Se freno antes del clic de CONFIRMAR: la orden no salio.
            self._capturar(page, alias, "aborto-confirmar")
            self._limpiar(page, plataforma, valores)
            raise
        except Exception as e:
            self._ultimo_error = f"{alias}: verificacion: {e}"[:200]
            self._capturar(page, alias, "sin-verificar")
            log.error(f"Se hizo clic pero no se pudo verificar ({etiqueta}): {e}")
            return ResultadoOrden(etiqueta, alias, confirmado=False,
                                  detalle=f"se hizo clic pero no se pudo verificar: {e}")

        if not confirmado:
            self._ultimo_error = f"{alias}: orden no confirmada"
            captura = self._capturar(page, alias, "no-confirmada")
            log.error(f"Orden NO confirmada ({etiqueta}): la posicion de "
                      f"{trabajo['contrato']} no cambio en "
                      f"{self.opciones['segundos_verificacion']} s. Captura: {captura}")
            return ResultadoOrden(etiqueta, alias, confirmado=False,
                                  detalle=f"la posicion no cambio (sigue {list(antes)})")
        log.info(f"Orden confirmada ({etiqueta}): posicion {list(antes)} -> {list(despues)}")
        detalle = f"posicion {list(antes)} -> {list(despues)}"
        if not plataforma.get("pasos_despues"):
            return ResultadoOrden(etiqueta, alias, confirmado=True, detalle=detalle)

        sin_proteccion = self._proteger(page, plataforma, trabajo, valores, antes, alias)
        if sin_proteccion:
            self._ultimo_error = f"{alias}: SIN SL/TP: {sin_proteccion}"[:200]
            captura = self._capturar(page, alias, "sin-sl-tp")
            log.error(f"POSICION ABIERTA SIN SL/TP CONFIRMADO en {alias} ({etiqueta}): "
                      f"{sin_proteccion}. PONELO A MANO YA. Captura: {captura}")
            return ResultadoOrden(etiqueta, alias, confirmado=True, detalle=detalle,
                                  sin_proteccion=sin_proteccion)
        log.info(f"SL/TP puestos y verificados en la fila ({etiqueta}): riesgo "
                 f"{trabajo.get('riesgo_usd')} / ganancia {trabajo.get('ganancia_usd')} USD")
        return ResultadoOrden(etiqueta, alias, confirmado=True,
                              detalle=f"{detalle}; SL/TP puestos")

    def _proteger(self, page, plataforma, trabajo, valores, antes, alias):
        """Corre pasos_despues sobre la fila de la posicion (SL/TP en TopstepX).
        Devuelve None si quedo verificado, o el motivo por el que NO.

        Nunca levanta: la orden ya salio y lo unico que importa es avisar.
        Tampoco mira el aborto: un stop no puede dejar una posicion sin SL.
        """
        if antes:
            # El Risk/To Make de la fila es de TODA la posicion: escribir el
            # riesgo de ESTA orden sobre una posicion que ya existia lo dejaria
            # mal. Mejor no tocar y avisar.
            return (f"ya habia posicion en {trabajo['contrato']} antes de esta orden "
                    f"({list(antes)}); el SL/TP de la fila es de TODA la posicion y "
                    f"no se toco: revisalo")
        conf = plataforma["posiciones"]
        try:
            fila = fila_de(page, conf, texto_de_fila(conf, valores))
            ejecutar_pasos(page, plataforma["pasos_despues"], valores,
                           self.opciones["timeout_ms"], "SL/TP", con_aborto=False, raiz=fila)
        except Exception as e:
            # WARNING y no ERROR: el aviso al grupo de errores es UNO, el de
            # "POSICION ABIERTA SIN SL/TP" que arma quien llama.
            log.warning(f"Fallaron los pasos de SL/TP en {alias}", exc_info=True)
            # Que no quede una celda a medio editar con un valor que nadie confirmo.
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            return str(e).splitlines()[0][:300] or type(e).__name__
        return None

    def _verificar(self, page, plataforma, trabajo, antes):
        """(confirmado, despues): espera a que cambie la cantidad del contrato.
        No mira el aborto: el clic ya se hizo, y lo que importa ahora es
        saber si la orden entro."""
        limite = time.monotonic() + self.opciones["segundos_verificacion"]
        to = self.opciones["timeout_ms"]
        conf = plataforma["posiciones"]
        buscado = texto_de_fila(conf, valores_de(trabajo, self.config.cuentas.get(trabajo["cuenta"])))
        despues = antes
        while True:
            despues = leer_posiciones(page, conf, buscado, to)
            if despues != antes:
                return True, despues
            if time.monotonic() >= limite:
                return False, despues
            page.wait_for_timeout(PAUSA_VERIFICACION_MS)

    # --- navegador (hilo del navegador) ------------------------------------

    def _playwright(self):
        if self._pw is None:
            fabrica = self._fabrica or _fabrica_real
            self._pw = fabrica()
        return self._pw

    def _contexto(self, cuenta):
        clave = str(cuenta.perfil)
        ctx = self._contextos.get(clave)
        if ctx is not None:
            return ctx
        Path(clave).mkdir(parents=True, exist_ok=True)
        headless = self.opciones["headless"]
        args = list(ARGS_SIN_FRENOS)
        if self.config.no_robar_foco and not headless:
            args += ARGS_NO_ROBAR_FOCO
        opciones = {"user_data_dir": clave, "headless": headless, "args": args,
                    "no_viewport": True}
        if self.opciones["canal"]:
            opciones["channel"] = self.opciones["canal"]
        log.info(f"Abriendo navegador con el perfil {clave} (headless={headless}, "
                 f"no_robar_foco={self.config.no_robar_foco}).")
        ctx = self._playwright().chromium.launch_persistent_context(**opciones)
        # Si el operador (o algo) cierra la ventana, la proxima orden la reabre.
        ctx.on("close", lambda *_: self._contextos.pop(clave, None))
        self._contextos[clave] = ctx
        return ctx

    def _pagina(self, cuenta, plataforma):
        ctx = self._contexto(cuenta)
        page = next((p for p in ctx.pages if not p.is_closed()), None) or ctx.new_page()
        if not page.url or page.url == "about:blank":
            page.goto(plataforma["url"], timeout=self.opciones["carga_ms"],
                      wait_until="domcontentloaded")
        try:
            page.locator(plataforma["sesion_iniciada"]).wait_for(
                state="visible", timeout=self.opciones["carga_ms"])
        except Exception as e:
            self._capturar(page, cuenta.alias, "sin-sesion")
            raise RuntimeError(
                f"la sesion de {cuenta.alias} en {cuenta.plataforma} no esta iniciada "
                f"(o la pagina no cargo). Hace el login a mano: parar el listener y "
                f"correr  python abrir_perfil.py {cuenta.alias}") from e
        return page

    def _limpiar(self, page, plataforma, valores):
        """Deja el ticket vacio para que la orden siguiente no herede valores.
        Nunca levanta: se corre justo cuando algo ya salio mal."""
        try:
            if plataforma.get("pasos_limpiar"):
                ejecutar_pasos(page, plataforma["pasos_limpiar"], valores,
                               self.opciones["timeout_ms"], "limpiar", con_aborto=False)
            else:
                page.reload(timeout=self.opciones["carga_ms"], wait_until="domcontentloaded")
        except Exception:
            log.exception("No se pudo limpiar el ticket de la orden.")

    def _capturar(self, page, alias: str, motivo: str):
        """Foto del momento exacto (leccion de MT5: una captura resolvio en un
        vistazo un fallo que llevaba horas). Nunca levanta."""
        try:
            self._dir_diagnosticos.mkdir(parents=True, exist_ok=True)
            ruta = self._dir_diagnosticos / f"{datetime.now():%Y%m%d-%H%M%S}-{alias}-{motivo}.png"
            page.screenshot(path=str(ruta))
            self._podar_capturas()
            return ruta
        except Exception:
            log.exception("No se pudo guardar la captura de diagnostico.")
            return None

    def _podar_capturas(self):
        capturas = sorted(self._dir_diagnosticos.glob("*.png"))
        for vieja in capturas[:-MAXIMO_CAPTURAS]:
            try:
                vieja.unlink()
            except OSError:
                pass

    def _cerrar_todo(self):
        for ctx in list(self._contextos.values()):
            try:
                ctx.close()
            except Exception:
                log.exception("No se pudo cerrar un navegador.")
        self._contextos.clear()
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                log.exception("No se pudo detener Playwright.")
            self._pw = None
