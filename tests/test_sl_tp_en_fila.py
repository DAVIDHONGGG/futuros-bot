"""SL/TP cargado en la FILA de la posicion despues de la entrada (pasos_despues).

Medido con una orden real en TopstepX (12/11/2026): el dialogo de brackets
cambio y cerrarlo con la X descarta los valores, asi que el bot hace lo que
hace el operador: entra sin SL/TP y edita Risk / To Make en la fila.

Lo que protege la plata aca:
  - en dry_run nunca se toca la fila (no hay posicion);
  - todos los pasos quedan DENTRO de la fila del contrato;
  - un stop NO corta la proteccion (la posicion ya esta abierta);
  - si algo falla, la orden queda "SIN SL/TP": alerta, la cuenta se bloquea
    por el resto del lote, y no se cierra nada solo;
  - si ya habia posicion en ese contrato no se toca la fila (el SL/TP es de
    TODA la posicion) y se avisa.
"""

import copy
import json
import logging
import unittest
from pathlib import Path

from tests.apoyo import FLOTA_PRUEBA, config_de_prueba, dir_temporal, local_con, trabajo
from tests.navegador_falso import PlaywrightFalso, fabrica

import ejecutor
import listener_futuros
from ejecutor import ResultadoOrden
from ejecutor_web import EjecutorWeb
from pasos import ejecutar_pasos, validar_plataforma, valores_de

RAIZ = Path(__file__).resolve().parent.parent
FILA = "#posiciones .fila[fila=MNQZ6]"     # la fila del contrato en el falso

PASOS_DESPUES = [
    {"accion": "doble_click", "selector": ".risk .lapiz"},
    {"accion": "escribir", "selector": ".risk input", "valor": "{riesgo_usd}", "teclear": True},
    {"accion": "tecla", "selector": ".risk input", "tecla": "Enter"},
    {"accion": "verificar", "selector": ".risk", "igual": "{riesgo_usd}", "numerico": True},
]


def con_riesgo(t, riesgo="20", ganancia="40"):
    return dict(t, riesgo_usd=riesgo, ganancia_usd=ganancia)


class TestValidacion(unittest.TestCase):

    def test_doble_click_y_pasos_despues_se_validan(self):
        p = copy.deepcopy(FLOTA_PRUEBA["plataformas"]["tradovate"])
        p["pasos_despues"] = copy.deepcopy(PASOS_DESPUES)
        self.assertEqual(validar_plataforma("x", p), [])
        p["pasos_despues"].append({"accion": "doble_click"})
        self.assertIn("pasos_despues paso 5: falta 'selector'",
                      " | ".join(validar_plataforma("x", p)))


class TestInterpreteConRaiz(unittest.TestCase):

    def setUp(self):
        self.pw = PlaywrightFalso()
        self.pw.chromium.launch_persistent_context()
        self.page = self.pw.pagina()
        self.addCleanup(ejecutor.limpiar_aborto)

    def test_todo_se_resuelve_dentro_de_la_raiz(self):
        raiz = self.page.locator("#filas").filter(has_text="/MNQ")
        self.page.textos["#filas[fila=/MNQ] >> .risk"] = "$20.00"
        v = valores_de(con_riesgo(trabajo()), None)
        ejecutar_pasos(self.page, PASOS_DESPUES, v, 100, "SL/TP", raiz=raiz)
        selectores = [s for _, a, s, _ in self.pw.registro if a not in ("lanzar",)]
        self.assertTrue(selectores)
        self.assertTrue(all(s.startswith("#filas[fila=/MNQ] >> ") for s in selectores),
                        selectores)
        self.assertEqual(self.pw.acciones("doble_click")[0][2], "#filas[fila=/MNQ] >> .risk .lapiz")

    def test_sin_aborto_un_stop_no_corta(self):
        ejecutor.solicitar_aborto()
        raiz = self.page.locator("#filas").filter(has_text="/MNQ")
        self.page.textos["#filas[fila=/MNQ] >> .risk"] = "20"
        ejecutar_pasos(self.page, PASOS_DESPUES, valores_de(con_riesgo(trabajo()), None),
                       100, "SL/TP", con_aborto=False, raiz=raiz)
        self.assertTrue(self.pw.acciones("doble_click"))


class TestProteccionEnElEjecutor(unittest.TestCase):

    def crear(self, dry_run=False, pasos=None, ya_habia=None, celda="$20.00",
              falla_en=()):
        flota = copy.deepcopy(FLOTA_PRUEBA)
        flota["plataformas"]["tradovate"]["pasos_despues"] = copy.deepcopy(
            PASOS_DESPUES if pasos is None else pasos)
        cfg = config_de_prueba(flota=flota, local=local_con(dry_run=dry_run))

        def preparar(pagina):
            if ya_habia:
                pagina.posiciones["MNQZ6"] = ya_habia

            def comprar(pag, selector):
                if selector == "#comprar":
                    pag.posiciones["MNQZ6"] = str(int(pag.posiciones.get("MNQZ6", "0")) + 1)
            pagina.efecto_clic = comprar
            pagina.textos[f"{FILA} >> .risk"] = celda
            pagina.falla_en = set(falla_en)
        self.pw = PlaywrightFalso(preparar_pagina=preparar)
        self.ej = EjecutorWeb(cfg, fabrica=fabrica(self.pw), dir_diagnosticos=dir_temporal())
        self.addCleanup(self.ej.cerrar)
        self.addCleanup(ejecutor.limpiar_aborto)

    def acciones_en_fila(self):
        return [(a, s) for _, a, s, _ in self.pw.registro if s and s.startswith(FILA)]

    def test_despues_de_confirmar_carga_el_sl_tp_en_la_fila(self):
        self.crear()
        res = self.ej.colocar_lote([con_riesgo(trabajo())])[0]
        self.assertIs(res.confirmado, True)
        self.assertIsNone(res.sin_proteccion)
        self.assertIn("SL/TP puestos", res.detalle)
        self.assertEqual(self.pw.acciones("teclear")[-1][2:], (f"{FILA} >> .risk input", "20"))
        # Y en ese orden: primero el clic de Comprar, despues la fila.
        orden = [(a, s) for _, a, s, _ in self.pw.registro
                 if (a, s) in (("click", "#comprar"), ("doble_click", f"{FILA} >> .risk .lapiz"))]
        self.assertEqual([a for a, _ in orden], ["click", "doble_click"])

    def test_en_dry_run_nunca_toca_la_fila(self):
        self.crear(dry_run=True)
        res = self.ej.colocar_lote([con_riesgo(trabajo())])[0]
        self.assertIsNone(res.confirmado)
        self.assertEqual(self.acciones_en_fila(), [])

    def test_si_la_celda_no_quedo_bien_alerta_y_bloquea_la_cuenta(self):
        self.crear(celda="$0.00")
        with self.assertLogs("bot_futuros.ejecutor_web", level="ERROR") as logs:
            res = self.ej.colocar_lote([con_riesgo(trabajo()), con_riesgo(trabajo())])
        self.assertIs(res[0].confirmado, True)
        self.assertIn("'$0.00'", res[0].sin_proteccion)
        self.assertTrue(res[0].bloquea_la_cuenta)
        self.assertTrue(res[1].omitida, "la segunda orden en la misma cuenta no se intenta")
        self.assertEqual(self.pw.clics().count("#comprar"), 1)
        errores = [r for r in logs.records if r.levelno >= logging.ERROR]
        self.assertTrue(any("POSICION ABIERTA SIN SL/TP" in r.getMessage() for r in errores))
        # UN solo aviso al grupo de errores por la posicion desprotegida.
        self.assertEqual(sum("SL/TP" in r.getMessage() for r in errores), 1)

    def test_si_un_paso_falla_sale_de_la_edicion_y_alerta(self):
        self.crear(falla_en=[f"{FILA} >> .risk .lapiz"])
        res = self.ej.colocar_lote([con_riesgo(trabajo())])[0]
        self.assertIs(res.confirmado, True)
        self.assertIn("Timeout", res.sin_proteccion)
        self.assertIn(("tecla", None, "Escape"),
                      [(a, s, d) for _, a, s, d in self.pw.registro if a == "tecla"])
        # Nunca intenta cerrar la posicion solo: despues de Comprar, ningun clic.
        self.assertEqual(self.pw.clics()[-1], "#comprar")

    def test_sin_valor_tick_no_inventa_el_riesgo(self):
        self.crear()
        res = self.ej.colocar_lote([trabajo()])[0]       # sin riesgo_usd
        self.assertIn("valor_tick", res.sin_proteccion)
        self.assertEqual(self.pw.acciones("teclear"), [])

    def test_si_ya_habia_posicion_no_toca_la_fila_y_avisa(self):
        self.crear(ya_habia="1")
        res = self.ej.colocar_lote([con_riesgo(trabajo())])[0]
        self.assertIs(res.confirmado, True)
        self.assertIn("ya habia posicion", res.sin_proteccion)
        self.assertEqual(self.acciones_en_fila(), [])

    def test_un_stop_despues_del_clic_no_deja_la_posicion_sin_sl(self):
        self.crear()
        orig = self.pw.preparar_pagina

        def preparar(pagina):
            orig(pagina)
            efecto = pagina.efecto_clic

            def comprar_y_stop(pag, selector):
                efecto(pag, selector)
                if selector == "#comprar":
                    ejecutor.solicitar_aborto()
            pagina.efecto_clic = comprar_y_stop
        self.pw.preparar_pagina = preparar
        res = self.ej.colocar_lote([con_riesgo(trabajo())])[0]
        self.assertIsNone(res.sin_proteccion)
        self.assertTrue(self.pw.acciones("doble_click"))


class TestClickSiAparece(unittest.TestCase):
    """El dialogo "Switch Chart Symbol" de TopstepX: a veces sale y a veces no."""

    def setUp(self):
        self.pw = PlaywrightFalso()
        self.pw.chromium.launch_persistent_context()
        self.page = self.pw.pagina()
        self.raiz = self.page.locator("#filas").filter(has_text="/MNQ")
        self.addCleanup(ejecutor.limpiar_aborto)

    def correr(self):
        ejecutar_pasos(self.page, [{"accion": "click_si_aparece", "selector": "#cancel",
                                    "ms": 50}], {}, 100, "SL/TP", raiz=self.raiz)

    def test_si_aparece_hace_clic_en_la_PAGINA_no_en_la_fila(self):
        self.correr()
        self.assertEqual(self.pw.clics(), ["#cancel"])

    def test_si_no_aparece_sigue_sin_error(self):
        self.page.ocultos.add("#cancel")
        self.correr()
        self.assertEqual(self.pw.clics(), [])

    def test_valida(self):
        p = copy.deepcopy(FLOTA_PRUEBA["plataformas"]["tradovate"])
        p["pasos_despues"] = [{"accion": "click_si_aparece"}]
        self.assertIn("falta 'selector'", " | ".join(validar_plataforma("x", p)))


class TestMensajeDeTelegram(unittest.TestCase):

    def test_sin_proteccion_se_grita(self):
        r = ResultadoOrden("1. MNQ buy 1 @ TOP1", "TOP1", confirmado=True,
                           sin_proteccion="la celda dice '$0.00'")
        texto = listener_futuros._estado_de(r)
        self.assertIn("SIN SL/TP", texto)
        self.assertIn("PONELO A MANO", texto)

    def test_confirmada_normal(self):
        r = ResultadoOrden("1", "TOP1", confirmado=True)
        self.assertEqual(listener_futuros._estado_de(r), "confirmada")


class TestConfigRealDeTopstepX(unittest.TestCase):

    def setUp(self):
        flota = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))
        self.tsx = flota["plataformas"]["topstepx"]

    def test_la_orden_ya_no_toca_el_dialogo_de_brackets(self):
        # Desde la 2.4.36.2 ese dialogo pide SAVE CHANGES: cerrarlo con la X
        # descarta los valores y la orden saldria creyendo que tiene SL.
        texto = json.dumps(self.tsx["pasos_orden"])
        for viejo in ("oco-bracket", "auto-oco-brackets", "[role='dialog']", "{riesgo_usd}"):
            self.assertNotIn(viejo, texto)

    def test_sl_tp_en_la_fila_en_dolares_y_verificado(self):
        pasos = self.tsx["pasos_despues"]
        escritos = {p["selector"]: p["valor"] for p in pasos if p["accion"] == "escribir"}
        self.assertEqual(escritos, {"[data-field='risk'] input": "{riesgo_usd}",
                                    "[data-field='toMake'] input": "{ganancia_usd}"})
        verificados = {p["selector"]: p["igual"] for p in pasos if p["accion"] == "verificar"}
        self.assertEqual(verificados, {"[data-field='risk']": "{riesgo_usd}",
                                       "[data-field='toMake']": "{ganancia_usd}"})
        self.assertTrue(all(p.get("numerico") for p in pasos if p["accion"] == "verificar"))
        # El ultimo paso es verificar: nada se da por puesto sin releerlo.
        self.assertEqual(pasos[-1]["accion"], "verificar")
        enter = [p for p in pasos if p["accion"] == "tecla"]
        self.assertEqual([p["tecla"] for p in enter], ["Enter", "Enter"])

    def test_ningun_clic_simple_en_la_fila(self):
        # Medido el 12/11/2026: un clic SIMPLE en la fila abre "Switch Chart
        # Symbol" y tapa la celda (asi quedo la primera orden real sin TP).
        # "teclear" hace clic en el input: tampoco.
        for paso in self.tsx["pasos_despues"]:
            with self.subTest(paso=paso):
                self.assertNotIn(paso["accion"], ("click", "click_texto", "repetir", "marcar"))
                self.assertFalse(paso.get("teclear"))

    def test_el_dialogo_se_cancela_antes_de_cada_Enter_y_de_verificar(self):
        pasos = self.tsx["pasos_despues"]
        cancelar = [p for p in pasos if p["accion"] == "click_si_aparece"]
        self.assertTrue(cancelar)
        for p in cancelar:
            self.assertIn("Switch Chart Symbol", p["selector"])
            self.assertIn("CANCEL", p["selector"])
            self.assertNotIn("SWITCH')", p["selector"])
        for i, p in enumerate(pasos):
            if p["accion"] == "tecla" and p["tecla"] == "Enter":
                self.assertEqual(pasos[i - 1]["accion"], "click_si_aparece", i)
        primer_verificar = next(i for i, p in enumerate(pasos) if p["accion"] == "verificar")
        self.assertIn("click_si_aparece",
                      [p["accion"] for p in pasos[primer_verificar - 2:primer_verificar]])

    def test_pasos_despues_no_tocan_botones_peligrosos(self):
        peligrosos = ("dom-click-button", "close", "flatten", "reverse", "cancel")
        for paso in self.tsx["pasos_despues"]:
            with self.subTest(paso=paso):
                if paso["accion"] == "click_si_aparece":
                    # Su CANCEL es el del dialogo del grafico, no "Cancel all"
                    # ni "Cancel orders": tiene que estar acotado a ese dialogo.
                    self.assertTrue(paso["selector"].startswith(
                        "[role='dialog']:has-text('Switch Chart Symbol') "))
                    continue
                self.assertFalse(any(x in paso.get("selector", "").lower() for x in peligrosos))


if __name__ == "__main__":
    unittest.main()
