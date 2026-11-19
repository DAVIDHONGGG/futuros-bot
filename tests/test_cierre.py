"""El "close" por la web y el "close all" con las cuentas operadas
(19/11/2026).

Hasta aca el ejecutor web no cerraba NADA (NotImplementedError de la Etapa
3): cualquier close respondia "ERROR - revisa la plataforma". Y "all" eran
solo las cuentas configuradas, asi que una cuenta operada por NUMERO (logins)
quedaba afuera.

Lo que protege la plata, y se prueba aca:
  - sin "cierre" medido en la plataforma no se hace clic en nada;
  - sin posiciones no se hace clic; sin poder leer el panel, tampoco;
  - dry_run no cierra;
  - el cierre se da por bueno SOLO si el panel queda sin posiciones de esa
    cuenta; si no, es ERROR (cerrar a mano);
  - con texto_fila solo cuentan las filas de ESA cuenta (para un panel que
    muestre varias cuentas; TopstepX y Tradovate muestran solo la elegida);
  - Tradovate: clic derecho en la fila y "Exit at MKT&CXL" en el menu de la
    pagina (medido en FUTVAN4 el 19/11/2026);
  - close all = configuradas + operadas por el bot, y una cuenta sale de la
    lista cuando un cierre la deja sin nada.
"""

import copy
import unittest
from datetime import datetime, timedelta

from tests.apoyo import (FLOTA_PRUEBA, ListenerAislado, config_de_prueba, dir_temporal,
                         local_con, trabajo)
from tests.navegador_falso import PlaywrightFalso, fabrica

import ejecutor
import operadas
from ejecutor import EjecutorBase, ResultadoOrden
from ejecutor_web import EjecutorWeb
from pasos import validar_plataforma

CIERRE = {"pasos": [{"accion": "click", "selector": "#cerrar"},
                    {"accion": "click_si_aparece", "selector": "#confirmar", "ms": 50}]}


def flota_con_cierre(cierre=CIERRE):
    flota = copy.deepcopy(FLOTA_PRUEBA)
    flota["plataformas"]["tradovate"]["cierre"] = copy.deepcopy(cierre)
    return flota


def cerrar_al_clic(cuenta=None):
    """Simula una plataforma que cierra: al confirmar (o al clic de cerrar)
    desaparecen las filas (todas, o solo las de `cuenta`)."""
    def efecto(pagina, selector):
        if selector == "#cerrar":
            for clave in list(pagina.posiciones):
                if cuenta is None or cuenta in clave:
                    pagina.posiciones[clave] = "0"
    return efecto


class BaseCierreWeb(unittest.TestCase):

    def crear(self, dry_run=False, flota=None, local=None, preparar=None):
        cfg = config_de_prueba(flota=flota or flota_con_cierre(),
                               local=local or local_con(dry_run=dry_run))
        self.pw = PlaywrightFalso(preparar_pagina=preparar)
        self.diag = dir_temporal()
        self.ej = EjecutorWeb(cfg, fabrica=fabrica(self.pw), dir_diagnosticos=self.diag)
        self.addCleanup(self.ej.cerrar)
        self.addCleanup(ejecutor.limpiar_aborto)
        return self.ej

    def capturas(self):
        return sorted(p.name for p in self.diag.glob("*.png"))


class TestCierreWeb(BaseCierreWeb):

    def test_sin_cierre_medido_no_toca_nada(self):
        self.crear(flota=FLOTA_PRUEBA,
                   preparar=lambda p: setattr(p, "posiciones", {"MNQZ6": "1"}))
        with self.assertLogs("bot_futuros.ejecutor", level="ERROR") as logs:
            self.assertEqual(self.ej.cerrar_cuentas(["TV1"]), {"TV1": None})
        self.assertIn("todavia no esta medido", "\n".join(logs.output))
        self.assertEqual(self.pw.clics(), [])

    def test_sin_posiciones_no_hace_clic(self):
        self.crear()
        self.assertEqual(self.ej.cerrar_cuentas(["TV1"]), {"TV1": 0})
        self.assertNotIn("#cerrar", self.pw.clics())

    def test_cierra_y_verifica(self):
        def preparar(p):
            p.posiciones = {"MNQZ6": "2", "MESZ6": "-1"}
            p.efecto_clic = cerrar_al_clic()
        self.crear(preparar=preparar)
        self.assertEqual(self.ej.cerrar_cuentas(["TV1"]), {"TV1": 2})
        self.assertEqual(self.pw.clics(), ["#cerrar", "#confirmar"])

    def test_si_el_panel_no_queda_vacio_es_error_con_captura(self):
        self.crear(preparar=lambda p: setattr(p, "posiciones", {"MNQZ6": "1"}))
        with self.assertLogs("bot_futuros.ejecutor_web", level="ERROR") as logs:
            self.assertEqual(self.ej.cerrar_cuentas(["TV1"]), {"TV1": None})
        self.assertIn("CIERRE NO VERIFICADO en TV1", "\n".join(logs.output))
        self.assertTrue(any("cierre-sin-verificar" in c for c in self.capturas()))

    def test_dry_run_no_cierra(self):
        self.crear(dry_run=True, preparar=lambda p: setattr(p, "posiciones", {"MNQZ6": "1"}))
        resultado = self.ej.cerrar_cuentas(["TV1"])["TV1"]
        self.assertIsInstance(resultado, str)
        self.assertIn("dry_run, NO se cerro: 1 posicion", resultado)
        self.assertEqual(self.pw.clics(), [])

    def test_sin_panel_no_hace_clic(self):
        def preparar(p):
            p.posiciones = {"MNQZ6": "1"}
            p.sin_panel = True
        self.crear(preparar=preparar)
        with self.assertLogs("bot_futuros.ejecutor", level="ERROR"):
            self.assertEqual(self.ej.cerrar_cuentas(["TV1"]), {"TV1": None})
        self.assertEqual(self.pw.clics(), [])

    def test_stop_antes_no_cierra_nada(self):
        self.crear(preparar=lambda p: setattr(p, "posiciones", {"MNQZ6": "1"}))
        ejecutor.solicitar_aborto()
        self.assertEqual(self.ej.cerrar_cuentas(["TV1", "TV2"]), {})
        self.assertEqual(self.pw.clics(), [])

    def test_corre_en_el_hilo_del_navegador(self):
        def preparar(p):
            p.posiciones = {"MNQZ6": "1"}
            p.efecto_clic = cerrar_al_clic()
        self.crear(preparar=preparar)
        self.ej.cerrar_cuentas(["TV1"])
        self.assertEqual(self.pw.hilos(), {self.pw.hilo_que_arranco})

    def test_texto_fila_solo_mira_las_filas_de_esa_cuenta(self):
        # Un login con dos cuentas: el panel muestra las dos.
        cuentas = {"TV1": {"plataforma": "tradovate", "max_contratos": 3,
                           "cuenta_plataforma": "LFE0001"},
                   "TV2": {"plataforma": "tradovate", "max_contratos": 3,
                           "cuenta_plataforma": "LFE0002"}}

        def preparar(p):
            p.posiciones = {"MNQZ6 LFE0001": "1", "MNQZ6 LFE0002": "3"}
            p.efecto_clic = cerrar_al_clic("LFE0001")
        self.crear(flota=flota_con_cierre(dict(CIERRE, texto_fila="{cuenta_plataforma}")),
                   local=local_con(dry_run=False, cuentas=cuentas), preparar=preparar)
        self.assertEqual(self.ej.cerrar_cuentas(["TV1"]), {"TV1": 1})
        # La otra cuenta sigue con lo suyo (y no hizo falta que se cerrara).
        self.assertEqual(self.pw.pagina().posiciones["MNQZ6 LFE0002"], "3")

    def test_un_fallo_no_corta_las_demas(self):
        def preparar(p):
            p.posiciones = {"MNQZ6": "1"}
            p.efecto_clic = cerrar_al_clic()
        cuentas = {"TV1": {"plataforma": "tradovate", "max_contratos": 3},
                   "TS1": {"plataforma": "topstepx", "max_contratos": 2}}
        self.crear(local=local_con(dry_run=False, cuentas=cuentas), preparar=preparar)
        with self.assertLogs("bot_futuros.ejecutor", level="ERROR"):
            res = self.ej.cerrar_cuentas(["TS1", "TV1"])
        self.assertEqual(res, {"TS1": None, "TV1": 1})


CIERRE_POR_FILA = {"por_fila": True,
                   "pasos": [{"accion": "click", "selector": "[data-testid^='cerrar-']"}]}
BOTON_DE_FILA = "#posiciones .fila >> [data-testid^='cerrar-']"


def cerrar_una_fila_por_clic(pagina, selector):
    """Como TopstepX: la X de la fila cierra ESA posicion y la fila se va."""
    if selector == BOTON_DE_FILA:
        for clave, cantidad in pagina.posiciones.items():
            if cantidad != "0":
                del pagina.posiciones[clave]
                return


class TestCierrePorFila(BaseCierreWeb):
    """TopstepX (medido el 19/11/2026): un boton por fila, sin confirmacion."""

    def test_un_clic_por_cada_posicion_dentro_de_su_fila(self):
        def preparar(p):
            p.posiciones = {"/M6E": "1", "/MNQ": "-2"}
            p.efecto_clic = cerrar_una_fila_por_clic
        self.crear(flota=flota_con_cierre(CIERRE_POR_FILA), preparar=preparar)
        self.assertEqual(self.ej.cerrar_cuentas(["TV1"]), {"TV1": 2})
        self.assertEqual(self.pw.clics(), [BOTON_DE_FILA, BOTON_DE_FILA])

    def test_si_la_fila_no_se_va_no_sigue_clickeando(self):
        def preparar(p):
            p.posiciones = {"/M6E": "1", "/MNQ": "-2"}
        self.crear(flota=flota_con_cierre(CIERRE_POR_FILA), preparar=preparar)
        with self.assertLogs("bot_futuros.ejecutor_web", level="ERROR"):
            self.assertEqual(self.ej.cerrar_cuentas(["TV1"]), {"TV1": None})
        self.assertEqual(self.pw.clics(), [BOTON_DE_FILA])

    def test_el_de_topstepx_en_config_json_es_valido(self):
        import json
        from pathlib import Path
        datos = json.loads((Path(__file__).parent.parent / "config.json")
                           .read_text(encoding="utf-8"))
        topstepx = datos["plataformas"]["topstepx"]
        self.assertTrue(topstepx["cierre"]["por_fila"])
        self.assertEqual(validar_plataforma("topstepx", topstepx), [])


CIERRE_POR_MENU = {"por_fila": True,
                   "pasos": [{"accion": "click_derecho", "selector": "[role=gridcell] >> nth=1"},
                             {"accion": "click_texto", "selector": ".menu .acciones",
                              "texto": "Exit at MKT&CXL", "en_pagina": True}]}
CELDA_DE_FILA = "#posiciones .fila >> [role=gridcell] >> nth=1"
OPCION_DEL_MENU = ".menu .acciones >> texto=Exit at MKT&CXL"


def exit_cierra_una_fila(pagina, selector):
    """Como Tradovate: 'Exit at MKT&CXL' cierra la posicion y la fila QUEDA
    con NET POS 0 (no desaparece)."""
    if selector == OPCION_DEL_MENU:
        for clave, cantidad in pagina.posiciones.items():
            if cantidad != "0":
                pagina.posiciones[clave] = "0"
                return


class TestCierrePorMenu(BaseCierreWeb):
    """Tradovate (medido en FUTVAN4 el 19/11/2026): la fila no tiene boton; el
    clic derecho abre un menu EN LA PAGINA con 'Exit at MKT&CXL', que cierra
    sin confirmacion y cancela el bracket."""

    def test_clic_derecho_en_la_fila_y_exit_en_el_menu_de_la_pagina(self):
        def preparar(p):
            p.posiciones = {"MNQZ6": "1", "M6EZ6": "-2"}
            p.efecto_clic = exit_cierra_una_fila
        self.crear(flota=flota_con_cierre(CIERRE_POR_MENU), preparar=preparar)
        self.assertEqual(self.ej.cerrar_cuentas(["TV1"]), {"TV1": 2})
        derechos = [r[2] for r in self.pw.acciones("click_derecho")]
        self.assertEqual(derechos, [CELDA_DE_FILA, CELDA_DE_FILA])
        # La opcion se busco en la PAGINA, no dentro de la fila.
        self.assertEqual(self.pw.clics(), [OPCION_DEL_MENU, OPCION_DEL_MENU])

    def test_la_fila_que_queda_en_0_no_se_vuelve_a_cerrar(self):
        def preparar(p):
            p.posiciones = {"MESZ6": "0", "MNQZ6": "1"}
            p.efecto_clic = exit_cierra_una_fila
        self.crear(flota=flota_con_cierre(CIERRE_POR_MENU), preparar=preparar)
        self.assertEqual(self.ej.cerrar_cuentas(["TV1"]), {"TV1": 1})
        self.assertEqual(len(self.pw.acciones("click_derecho")), 1)
        self.assertEqual(self.pw.clics(), [OPCION_DEL_MENU])

    def test_si_el_menu_no_aparece_es_error_y_nada_mas(self):
        def preparar(p):
            p.posiciones = {"MNQZ6": "1"}
            p.falla_en = {OPCION_DEL_MENU}
        self.crear(flota=flota_con_cierre(CIERRE_POR_MENU), preparar=preparar)
        with self.assertLogs("bot_futuros.ejecutor_web", level="ERROR") as logs:
            self.assertEqual(self.ej.cerrar_cuentas(["TV1"]), {"TV1": None})
        self.assertIn("CIERRE NO VERIFICADO en TV1", "\n".join(logs.output))
        self.assertEqual(self.pw.clics(), [])

    def test_dry_run_ni_abre_el_menu(self):
        self.crear(dry_run=True, flota=flota_con_cierre(CIERRE_POR_MENU),
                   preparar=lambda p: setattr(p, "posiciones", {"MNQZ6": "1"}))
        self.assertIn("dry_run, NO se cerro", self.ej.cerrar_cuentas(["TV1"])["TV1"])
        self.assertEqual(self.pw.acciones("click_derecho"), [])
        self.assertEqual(self.pw.clics(), [])

    def test_el_de_tradovate_en_config_json_es_valido(self):
        import json
        from pathlib import Path
        datos = json.loads((Path(__file__).parent.parent / "config.json")
                           .read_text(encoding="utf-8"))
        tradovate = datos["plataformas"]["tradovate"]
        cierre = tradovate["cierre"]
        self.assertTrue(cierre["por_fila"])
        # Positions muestra SOLO la cuenta elegida (medido). Con texto_fila
        # "{cuenta_plataforma}", una van de una cuenta (sin columna ACCOUNT)
        # leeria "sin posiciones" y no cerraria nada.
        self.assertNotIn("texto_fila", cierre)
        self.assertEqual([p["accion"] for p in cierre["pasos"]], ["click_derecho", "click_texto"])
        self.assertTrue(cierre["pasos"][1]["en_pagina"])
        self.assertEqual(cierre["pasos"][1]["texto"], "Exit at MKT&CXL")
        self.assertEqual(validar_plataforma("tradovate", tradovate), [])


class TestValidacionDelCierre(unittest.TestCase):

    def plataforma(self, cierre):
        datos = copy.deepcopy(FLOTA_PRUEBA["plataformas"]["tradovate"])
        datos["cierre"] = cierre
        return validar_plataforma("tradovate", datos)

    def test_el_de_prueba_es_valido(self):
        self.assertEqual(self.plataforma(CIERRE), [])
        self.assertEqual(self.plataforma(dict(CIERRE, texto_fila="{cuenta_plataforma}")), [])

    def test_sin_pasos(self):
        self.assertTrue(self.plataforma({"pasos": []}))
        self.assertTrue(self.plataforma({}))
        self.assertTrue(self.plataforma("cerrar"))

    def test_todo_en_un_selector(self):
        problemas = self.plataforma({"pasos": [{"accion": "click", "selector": "TODO: medir"}]})
        self.assertTrue(any("sin configurar" in p for p in problemas))

    def test_click_derecho_y_en_pagina(self):
        self.assertEqual(self.plataforma(CIERRE_POR_MENU), [])
        sin_selector = {"pasos": [{"accion": "click_derecho"}]}
        self.assertTrue(any("falta 'selector'" in p for p in self.plataforma(sin_selector)))
        for paso, motivo in [
                ({"accion": "click", "selector": "#x", "en_pagina": True}, "solo sirve en click_texto"),
                ({"accion": "click_texto", "texto": "Exit", "selector": "#m", "en_pagina": "si"},
                 "true o false"),
                ({"accion": "click_texto", "texto": "Exit", "en_pagina": True}, "necesita 'selector'")]:
            problemas = self.plataforma({"pasos": [paso]})
            self.assertTrue(any(motivo in p for p in problemas), (paso, problemas))

    def test_un_cierre_no_tiene_contrato(self):
        problemas = self.plataforma(dict(CIERRE, texto_fila="{contrato}"))
        self.assertTrue(any("no sirve en un cierre" in p for p in problemas))
        problemas = self.plataforma({"pasos": [
            {"accion": "click_texto", "texto": "{contratos}"}]})
        self.assertTrue(any("no sirve en un cierre" in p for p in problemas))


class TestOperadas(unittest.TestCase):

    def setUp(self):
        from unittest.mock import patch
        parche = patch.object(operadas, "ARCHIVO", dir_temporal() / "operadas.json")
        parche.start()
        self.addCleanup(parche.stop)

    def test_anotar_y_olvidar(self):
        operadas.anotar(["TV1", "LFE0009"])
        self.assertEqual(sorted(operadas.vigentes()), ["LFE0009", "TV1"])
        operadas.olvidar(["TV1", "NO-ESTABA"])
        self.assertEqual(operadas.vigentes(), ["LFE0009"])

    def test_vencen_a_los_dias(self):
        ahora = datetime(2026, 11, 19, 10, 0)
        operadas.anotar(["VIEJA"], ahora - timedelta(days=operadas.DIAS_VIGENCIA, minutes=1))
        operadas.anotar(["NUEVA"], ahora - timedelta(days=1))
        self.assertEqual(operadas.vigentes(ahora), ["NUEVA"])
        self.assertNotIn("VIEJA", operadas.ARCHIVO.read_text(encoding="utf-8"))

    def test_archivo_roto_es_vacio(self):
        operadas.ARCHIVO.write_text("{esto no es json", encoding="utf-8")
        with self.assertLogs("bot_futuros.operadas", level="ERROR"):
            self.assertEqual(operadas.vigentes(), [])
        operadas.anotar(["TV1"])
        self.assertEqual(operadas.vigentes(), ["TV1"])


class EjecutorDeCierres(EjecutorBase):
    """Coloca confirmando (o no) y cierra devolviendo lo que se le diga."""

    def __init__(self, confirmado=True, cierres=None):
        super().__init__(dry_run=confirmado is None)
        self.confirmado = confirmado
        self.cierres = cierres or {}
        self.cerradas = []

    def _colocar_una(self, t, etiqueta):
        return ResultadoOrden(etiqueta, t["cuenta"], confirmado=self.confirmado)

    def _cerrar_cuenta(self, alias):
        self.cerradas.append(alias)
        resultado = self.cierres.get(alias, 1)
        if isinstance(resultado, Exception):
            raise resultado
        return resultado


FIRMAS = {"lucid": {"plataforma": "tradovate", "cuentas_empiezan_con": "LFE"},
          "topstep": {"plataforma": "topstepx", "cuentas_empiezan_con": None}}


def config_con_login():
    flota = copy.deepcopy(FLOTA_PRUEBA)
    flota["firmas"] = FIRMAS
    local = local_con(logins={"lucid": {"perfil": "perfiles/LUCID", "max_contratos": 2}})
    return config_de_prueba(flota=flota, local=local)


class TestCloseAllConOperadas(ListenerAislado, unittest.IsolatedAsyncioTestCase):

    def preparar(self, **opciones):
        self.aislar(config_con_login())
        self.ej = EjecutorDeCierres(**opciones)
        self.lf.aplicar_config(self.lf.CONFIG, self.ej)

    async def test_la_cuenta_operada_por_numero_entra_en_el_close_all(self):
        self.preparar()
        await self.mandar("FUT1 MNQ buy 1 40 80 LFE05089433290009")
        self.assertEqual(operadas.vigentes(), ["LFE05089433290009"])
        bot = await self.mandar("FUT1 close all")
        self.assertEqual(self.ej.cerradas, ["TV1", "TV2", "TS1", "LFE05089433290009"])
        self.assertIn("LFE05089433290009: 1 posicion(es) cerrada(s)", bot.textos())

    async def test_cerrada_sale_de_la_lista_y_con_error_queda(self):
        self.preparar(cierres={"LFE05089433290001": RuntimeError("no cerro"),
                               "LFE05089433290002": 0})
        await self.mandar("FUT1 MNQ buy 1 40 80 LFE05089433290001,LFE05089433290002")
        bot = await self.mandar("FUT1 close all")
        self.assertIn("LFE05089433290001: ERROR", bot.textos())
        self.assertIn("LFE05089433290002: sin posiciones", bot.textos())
        self.assertEqual(operadas.vigentes(), ["LFE05089433290001"])

    async def test_dry_run_no_anota(self):
        self.preparar(confirmado=None)
        await self.mandar("FUT1 MNQ buy 1 40 80 LFE05089433290009")
        self.assertEqual(operadas.vigentes(), [])

    async def test_no_confirmada_tambien_se_anota(self):
        # Se hizo el clic: puede haber quedado posicion.
        self.preparar(confirmado=False)
        await self.mandar("FUT1 MNQ buy 1 40 80 LFE05089433290009")
        self.assertEqual(operadas.vigentes(), ["LFE05089433290009"])

    async def test_configurada_operada_no_se_cierra_dos_veces(self):
        self.preparar()
        await self.mandar("FUT1 MNQ buy 1 40 80 TV1")
        await self.mandar("FUT1 close all")
        self.assertEqual(self.ej.cerradas, ["TV1", "TV2", "TS1"])

    async def test_dry_run_del_cierre_no_la_saca_de_la_lista(self):
        self.preparar(cierres={"LFE05089433290009": "dry_run, NO se cerro: 1 posicion(es)"})
        await self.mandar("FUT1 MNQ buy 1 40 80 LFE05089433290009")
        bot = await self.mandar("FUT1 close all")
        self.assertIn("LFE05089433290009: dry_run, NO se cerro", bot.textos())
        self.assertEqual(operadas.vigentes(), ["LFE05089433290009"])

    async def test_sin_configuradas_alcanza_con_las_operadas(self):
        flota = copy.deepcopy(FLOTA_PRUEBA)
        flota["firmas"] = FIRMAS
        local = local_con(cuentas={}, cuenta_por_defecto=None,
                          logins={"lucid": {"perfil": "perfiles/LUCID", "max_contratos": 2}})
        self.aislar(config_de_prueba(flota=flota, local=local))
        self.ej = EjecutorDeCierres()
        self.lf.aplicar_config(self.lf.CONFIG, self.ej)
        bot = await self.mandar("FUT1 close all")
        self.assertIn("ni cuentas operadas", bot.textos())
        await self.mandar("FUT1 MNQ buy 1 40 80 LFE05089433290009")
        await self.mandar("FUT1 close all")
        self.assertEqual(self.ej.cerradas, ["LFE05089433290009"])


if __name__ == "__main__":
    unittest.main()
