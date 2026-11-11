"""Piezas comunes de los tests: config de prueba, ejecutor de prueba y
Updates de Telegram falsos.

Todo archivo de test importa este modulo (from tests.apoyo import ...): eso
importa el paquete tests/ y activa su red de seguridad aunque la suite se
corra sin `-t .` (ver tests/__init__.py).
"""

import atexit
import copy
import json
import shutil
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import ejecutor
from config import Config
from ejecutor import EjecutorBase, ResultadoOrden, chequear_aborto

TOKEN_PRUEBA = "1111111:" + "A" * 35
USUARIO = 111          # autorizado en FLOTA_PRUEBA
INTRUSO = 999          # no autorizado

FLOTA_PRUEBA = {
    "dry_run": True,
    "telegram_allowed_user_id": [USUARIO],
    "telegram_chat_errores": None,
    "navegador": {"timeout_ms": 100, "carga_ms": 100, "segundos_verificacion": 1},
    "plataformas": {
        # Completa, con selectores inventados para el navegador falso.
        "tradovate": {
            "url": "https://tradovate.prueba/trader",
            "simbolos": {"MNQ": "MNQZ6", "MES": "MESZ6"},
            "sesion_iniciada": "#ticket",
            "pasos_orden": [
                {"accion": "click", "selector": "#simbolo"},
                {"accion": "escribir", "selector": "#simbolo", "valor": "{contrato}"},
                {"accion": "tecla", "tecla": "Enter"},
                {"accion": "escribir", "selector": "#cantidad", "valor": "{contratos}"},
                {"accion": "escribir", "selector": "#sl", "valor": "{sl_ticks}"},
                {"accion": "escribir", "selector": "#tp", "valor": "{tp_ticks}"},
            ],
            "boton_comprar": "#comprar",
            "boton_vender": "#vender",
            "pasos_limpiar": [{"accion": "tecla", "tecla": "Escape"}],
            "posiciones": {"panel": "#posiciones", "filas": "#posiciones .fila",
                           "cantidad": ".cantidad"},
        },
        # Con TODO: el ejecutor web se tiene que negar a operar aca.
        "topstepx": {"url": "TODO", "simbolos": {"MNQ": "MNQ-TSX", "MES": "TODO: falta"}},
    },
}

LOCAL_PRUEBA = {
    "machine_id": "PRUEBA",
    "prefijo_telegram": "FUT1",
    "telegram_bot_token": TOKEN_PRUEBA,
    "cuentas": {
        "TV1": {"plataforma": "tradovate", "max_contratos": 3},
        "TV2": {"plataforma": "tradovate", "max_contratos": 1},
        "TS1": {"plataforma": "topstepx", "max_contratos": 2},
    },
    "cuenta_por_defecto": "TV1",
}

_DIRS = []


def _limpiar_dirs():
    for d in _DIRS:
        shutil.rmtree(d, ignore_errors=True)


atexit.register(_limpiar_dirs)


def dir_temporal() -> Path:
    d = Path(tempfile.mkdtemp(prefix="botfut-cfg-"))
    _DIRS.append(d)
    return d


def config_de_prueba(flota=None, local=None, entorno=None, sin_local=False) -> Config:
    """Una Config armada en un directorio temporal, SIN mirar los archivos ni
    las variables de entorno reales de la maquina."""
    d = dir_temporal()
    flota = copy.deepcopy(FLOTA_PRUEBA if flota is None else flota)
    (d / "config.json").write_text(json.dumps(flota), encoding="utf-8")
    if not sin_local:
        local = copy.deepcopy(LOCAL_PRUEBA if local is None else local)
        (d / "config.local.json").write_text(json.dumps(local), encoding="utf-8")
    return Config(base_dir=d, entorno=entorno or {})


def local_con(**cambios) -> dict:
    """LOCAL_PRUEBA con algunas claves cambiadas (None = sacar la clave)."""
    local = copy.deepcopy(LOCAL_PRUEBA)
    for clave, valor in cambios.items():
        if valor is None:
            local.pop(clave, None)
        else:
            local[clave] = valor
    return local


class EjecutorDePrueba(EjecutorBase):
    """Registra lo que le piden y deja simular cada caso:

        no_confirma = {"TV1"}       -> las ordenes de TV1 vuelven confirmado=False
        falla = {"TS1"}             -> las de TS1 levantan una excepcion
        abortar_en = 1              -> la orden #1 pide "stop" (como si llegara
                                       mientras se llena) y la siguiente lo ve
        frenar = threading.Event()  -> la primera orden espera a que se libere
        aborta_antes_del_clic       -> la orden revisa el aborto donde el web
                                       lo revisara: justo antes del clic
    """

    nombre = "prueba"

    def __init__(self, dry_run=True, no_confirma=(), falla=(), abortar_en=None,
                 frenar=None, aborta_antes_del_clic=False):
        super().__init__(dry_run=dry_run)
        self.no_confirma = set(no_confirma)
        self.falla = set(falla)
        self.abortar_en = abortar_en
        self.frenar = frenar
        self.aborta_antes_del_clic = aborta_antes_del_clic
        self.colocadas = []      # trabajos que llegaron al "clic"
        self.intentadas = []     # trabajos que se empezaron
        self.cerradas = []
        self.en_curso = threading.Event()

    def _colocar_una(self, trabajo, etiqueta):
        self.intentadas.append(trabajo)
        self.en_curso.set()
        if self.frenar is not None and len(self.intentadas) == 1:
            self.frenar.wait(10)
        if self.abortar_en == len(self.intentadas):
            ejecutor.solicitar_aborto()
        if self.aborta_antes_del_clic:
            chequear_aborto("antes del clic")
        if trabajo["cuenta"] in self.falla:
            raise RuntimeError(f"fallo simulado en {trabajo['cuenta']}")
        self.colocadas.append(trabajo)
        if trabajo["cuenta"] in self.no_confirma:
            return ResultadoOrden(etiqueta, trabajo["cuenta"], confirmado=False)
        return ResultadoOrden(etiqueta, trabajo["cuenta"],
                              confirmado=None if self.dry_run else True,
                              detalle="prueba")

    def _cerrar_cuenta(self, alias):
        if alias in self.falla:
            raise RuntimeError(f"fallo simulado cerrando {alias}")
        self.cerradas.append(alias)
        return 2


def trabajo(cuenta="TV1", simbolo="MNQ", accion="buy", contratos=1):
    return {"simbolo": simbolo, "accion": accion, "contratos": contratos,
            "sl_ticks": 40, "tp_ticks": 80, "cuenta": cuenta, "at": None,
            "plataforma": "tradovate", "contrato": "MNQZ6"}


def update_falso(texto, user_id=USUARIO, hace=timedelta(seconds=1), chat_id=42):
    mensaje = SimpleNamespace(
        text=texto,
        date=datetime.now(timezone.utc) - hace,
        reply_text=AsyncMock(),
    )
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id, username="prueba",
                                       full_name=None, first_name=None),
        effective_chat=SimpleNamespace(id=chat_id),
        message=mensaje,
    )


class BotFalso:
    def __init__(self):
        self.enviados = []

    async def send_message(self, chat_id, text):
        self.enviados.append((chat_id, text))

    def textos(self) -> str:
        return "\n".join(t for _, t in self.enviados)


def contexto_falso():
    return SimpleNamespace(bot=BotFalso())


class ListenerAislado:
    """Mixin de unittest.TestCase: deja el listener con una config de prueba,
    un EjecutorDePrueba y un programadas.json temporal, y lo restaura todo
    al terminar el test."""

    def aislar(self, config=None, **opciones_ejecutor):
        from unittest.mock import patch

        import listener_futuros as lf
        from tests import CONFIG_NEUTRA

        self.ej = EjecutorDePrueba(**opciones_ejecutor)
        lf.aplicar_config(config or config_de_prueba(), self.ej)
        self.addCleanup(lf.aplicar_config, CONFIG_NEUTRA)

        parche = patch.object(lf, "ARCHIVO_PROGRAMADAS", dir_temporal() / "programadas.json")
        parche.start()
        self.addCleanup(parche.stop)

        def _cancelar_tareas():
            for t in list(lf._TAREAS_PROGRAMADAS):
                t.cancel()
            lf._TAREAS_PROGRAMADAS.clear()
        self.addCleanup(_cancelar_tareas)
        self.addCleanup(ejecutor.limpiar_aborto)
        self.lf = lf
        return lf

    async def mandar(self, texto, **kw):
        """Pasa `texto` por on_message (o on_stop si es un stop, como hace
        Telegram con el filtro del handler) y devuelve el BotFalso."""
        ctx = contexto_falso()
        upd = update_falso(texto, **kw)
        if self.lf.REGEX_STOP.match(texto):
            await self.lf.on_stop(upd, ctx)
        else:
            await self.lf.on_message(upd, ctx)
        self.ultimo_update = upd
        return ctx.bot
