"""Playwright FALSO para los tests del ejecutor web. Ningun test abre un
navegador de verdad.

Registra cada accion con el hilo que la hizo, para poder probar que todo
pasa en el hilo del navegador, y deja simular lo que hace falta:

    pagina.posiciones = {"MNQZ6": "1"}      lo que muestra el panel
    pagina.efecto_clic = fn(pagina, sel)    que pasa al hacer clic (llenar)
    pagina.al_escribir = fn(sel, valor)     gancho (ej. pedir un stop)
    pagina.al_tecla = fn(pag, sel, tecla)   que pasa al apretar una tecla (Enter)
    pagina.sin_sesion = True                el selector de sesion no aparece
    pagina.sin_panel = True                 el panel de posiciones no aparece
    pagina.falla_en = {"#cantidad"}         esos selectores levantan error
    pagina.falla_al_clic = {"#comprar"}     se ve, pero el clic falla
    pagina.textos = {"#boton": "BUY +2"}    lo que lee un paso "verificar"
                                            (y evaluate: True = autocompletado)
    pagina.ocultos = {"#dialogo"}           nunca aparece (wait_for falla,
                                            is_visible da False)

Los locators relativos (la fila de una posicion, en pasos_despues) se
registran con el selector compuesto, para poder ver que todo quedo DENTRO de
la fila:  "#posiciones .fila[fila=/MNQ] >> [data-field='risk']".
"""

import itertools
import threading
import time
from pathlib import Path


class ErrorFalso(Exception):
    """Lo que levantaria Playwright (un TimeoutError, por ejemplo)."""


class _Teclado:
    def __init__(self, pagina):
        self.pagina = pagina

    def press(self, tecla):
        self.pagina._reg("tecla", None, tecla)


class LocatorFalso:
    def __init__(self, pagina, selector):
        self.pagina, self.selector = pagina, selector

    @property
    def first(self):
        return self

    def nth(self, _i):
        return self

    def filter(self, has_text=None):
        return LocatorFalso(self.pagina, f"{self.selector}[fila={has_text}]")

    def locator(self, selector):
        return LocatorFalso(self.pagina, f"{self.selector} >> {selector}")

    def get_by_text(self, texto, exact=True):
        return LocatorFalso(self.pagina, f"{self.selector} >> texto={texto}")

    def dblclick(self, timeout=None):
        self._chequear()
        if self.selector in self.pagina.falla_al_clic:
            raise ErrorFalso(f"{self.selector} no se pudo clickear (tapado)")
        self.pagina._reg("doble_click", self.selector)

    def _chequear(self):
        if self.selector in self.pagina.falla_en:
            raise ErrorFalso(f"Timeout esperando {self.selector}")

    def click(self, timeout=None):
        self._chequear()
        if self.selector in self.pagina.falla_al_clic:
            raise ErrorFalso(f"{self.selector} no se pudo clickear (tapado)")
        self.pagina._reg("click", self.selector)
        if self.pagina.efecto_clic:
            self.pagina.efecto_clic(self.pagina, self.selector)

    def fill(self, valor, timeout=None):
        self._chequear()
        self.pagina._reg("escribir", self.selector, valor)
        if self.pagina.al_escribir:
            self.pagina.al_escribir(self.selector, valor)

    def press(self, tecla, timeout=None):
        self._chequear()
        self.pagina._reg("tecla", self.selector, tecla)
        if self.pagina.al_tecla:
            self.pagina.al_tecla(self.pagina, self.selector, tecla)

    def press_sequentially(self, texto, delay=None, timeout=None):
        self._chequear()
        self.pagina._reg("teclear", self.selector, texto)

    def check(self, timeout=None):
        self._chequear()
        self.pagina._reg("marcar", self.selector)

    def evaluate(self, _expresion, arg=None, timeout=None):
        """Lo usa el paso "verificar" para leer un valor o un texto."""
        self._chequear()
        self.pagina._reg("leer", self.selector)
        return self.pagina.textos.get(self.selector, "")

    def is_visible(self, timeout=None):
        """Sin esperar. Mismas reglas que wait_for (ocultos, sin_sesion,
        sin_panel); falla_en tambien cuenta como "no se ve"."""
        self.pagina._reg("visible", self.selector)
        if self.selector in self.pagina.falla_en or self.selector in self.pagina.ocultos:
            return False
        if self.pagina.sin_sesion and self.selector == self.pagina.selector_sesion:
            return False
        if self.pagina.sin_panel and self.selector == self.pagina.selector_panel:
            return False
        return True

    def wait_for(self, state="visible", timeout=None):
        self._chequear()
        self.pagina._reg("esperar", self.selector)
        if self.selector in self.pagina.ocultos:
            raise ErrorFalso(f"{self.selector} no aparece")
        if self.pagina.sin_sesion and self.selector == self.pagina.selector_sesion:
            raise ErrorFalso("no aparece: sesion no iniciada")
        if self.pagina.sin_panel and self.selector == self.pagina.selector_panel:
            raise ErrorFalso("no aparece el panel de posiciones")

    def all(self):
        self.pagina._reg("leer_filas", self.selector)
        return [_FilaFalsa(self.pagina, c, q) for c, q in self.pagina.posiciones.items()]


class _FilaFalsa:
    """Una fila del panel. Su texto trae un P&L que cambia en CADA lectura:
    el ejecutor tiene que comparar la cantidad, no la fila entera."""
    _pnl = itertools.count()

    def __init__(self, pagina, contrato, cantidad):
        self.pagina, self.contrato, self.cantidad = pagina, contrato, cantidad

    def inner_text(self, timeout=None):
        return f"{self.contrato}  {self.cantidad}  P&L {next(self._pnl)}.25"

    def locator(self, _selector):
        fila = self

        class _Celda:
            first = None

            def inner_text(self, timeout=None):
                return fila.cantidad
        celda = _Celda()
        celda.first = celda
        return celda


class PaginaFalsa:
    def __init__(self, contexto):
        self.contexto = contexto
        self.url = "about:blank"
        self.cerrada = False
        self.keyboard = _Teclado(self)
        self.posiciones = {}
        self.efecto_clic = None
        self.al_escribir = None
        self.al_tecla = None           # fn(pagina, selector, tecla)
        self.sin_sesion = False
        self.sin_panel = False
        self.falla_en = set()
        self.falla_al_clic = set()
        self.textos = {}           # selector -> lo que lee "verificar"
        self.ocultos = set()       # selectores que nunca aparecen (wait_for falla)
        self.selector_sesion = "#ticket"
        self.selector_panel = "#posiciones"
        self.capturas = []

    def _reg(self, accion, selector=None, detalle=None):
        self.contexto.pw.registro.append(
            (threading.get_ident(), accion, selector, detalle))

    def is_closed(self):
        return self.cerrada

    def goto(self, url, timeout=None, wait_until=None):
        self._reg("goto", None, url)
        self.url = url

    def locator(self, selector):
        return LocatorFalso(self, selector)

    def get_by_text(self, texto, exact=True):
        return LocatorFalso(self, f"texto={texto}")

    def wait_for_timeout(self, ms):
        time.sleep(ms / 1000)

    def screenshot(self, path=None):
        self._reg("captura", None, path)
        Path(path).write_bytes(b"png falso")
        self.capturas.append(path)

    def reload(self, timeout=None, wait_until=None):
        self._reg("recargar")

    def bring_to_front(self):
        raise AssertionError("el ejecutor NUNCA puede traer la ventana al frente")


class ContextoFalso:
    def __init__(self, pw, opciones):
        self.pw, self.opciones = pw, opciones
        self.pages = [PaginaFalsa(self)]
        self.cerrado = False
        self._al_cerrar = []

    def new_page(self):
        p = PaginaFalsa(self)
        self.pages.append(p)
        return p

    def on(self, evento, fn):
        if evento == "close":
            self._al_cerrar.append(fn)

    def close(self):
        self.cerrado = True
        for fn in self._al_cerrar:
            fn(self)

    def wait_for_event(self, evento, timeout=None):
        self.pw.registro.append((threading.get_ident(), "esperar_evento", None, evento))


class _ChromiumFalso:
    def __init__(self, pw):
        self.pw = pw

    def launch_persistent_context(self, **opciones):
        self.pw.registro.append((threading.get_ident(), "lanzar", None, opciones))
        ctx = ContextoFalso(self.pw, opciones)
        self.pw.contextos.append(ctx)
        if self.pw.preparar_pagina:
            self.pw.preparar_pagina(ctx.pages[0])
        return ctx


class PlaywrightFalso:
    def __init__(self, preparar_pagina=None):
        self.registro = []           # (hilo, accion, selector, detalle)
        self.contextos = []
        self.detenido = False
        self.hilo_que_arranco = None
        self.chromium = _ChromiumFalso(self)
        self.preparar_pagina = preparar_pagina

    def stop(self):
        self.detenido = True

    # --- ayudas para los asserts -------------------------------------------

    def acciones(self, accion=None):
        return [r for r in self.registro if accion is None or r[1] == accion]

    def clics(self):
        return [r[2] for r in self.acciones("click")]

    def hilos(self):
        return {r[0] for r in self.registro}

    def pagina(self, n=0):
        return self.contextos[n].pages[0]


def fabrica(pw: PlaywrightFalso):
    """La funcion que se le pasa a EjecutorWeb(fabrica=...)."""
    def _arrancar():
        pw.hilo_que_arranco = threading.get_ident()
        return pw
    return _arrancar
