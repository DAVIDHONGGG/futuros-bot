"""Tope de riesgo en USD (max_riesgo_usd, valor_tick) y los pasos que pidio el
bracket en dolares de TopstepX: repetir, marcar, verificar y sus marcadores.

Lo que protege la plata: una orden con mas riesgo que el tope se rechaza
ENTERA antes de abrir el navegador, y un "verificar" que no coincide corta la
orden SIN el clic final.
"""

import copy
import json
import unittest
from decimal import Decimal
from pathlib import Path

from tests.apoyo import FLOTA_PRUEBA, config_de_prueba, dir_temporal, local_con, trabajo
from tests.navegador_falso import PlaywrightFalso, fabrica

import ejecutor
from config import CLAVES_LOCALES
from ejecutor import OperacionAbortada
from ejecutor_web import EjecutorWeb
from ordenes import expandir_lote, expandir_orden, parse_orden, usd
from pasos import (MAXIMO_REPETICIONES, VerificacionFallida, ejecutar_pasos, rellenar,
                   texto_de_fila, validar_plataforma, valores_de)

RAIZ = Path(__file__).resolve().parent.parent


def flota_con_ticks(**cambios):
    flota = copy.deepcopy(FLOTA_PRUEBA)
    flota["valor_tick"] = {"MNQ": 0.5, "MES": "1.25"}
    flota.update(cambios)
    return flota


def config_riesgo(tope=800, flota=None, **local):
    return config_de_prueba(flota=flota or flota_con_ticks(),
                            local=local_con(max_riesgo_usd=tope, **local))


class TestConfigRiesgo(unittest.TestCase):

    def test_max_riesgo_usd_es_clave_local(self):
        self.assertIn("max_riesgo_usd", CLAVES_LOCALES)
        cfg = config_riesgo(tope="800")
        self.assertEqual(cfg.max_riesgo_usd, Decimal("800"))
        self.assertEqual(cfg.advertencias, [])

    def test_sin_tope_es_None(self):
        self.assertIsNone(config_de_prueba().max_riesgo_usd)

    def test_un_tope_invalido_NO_lo_apaga(self):
        # Igual que dry_run: un typo no puede dejar la maquina sin limite.
        for raro in ("ochocientos", -5, 0, True, "nan"):
            with self.subTest(tope=raro):
                cfg = config_riesgo(tope=raro)
                self.assertEqual(cfg.max_riesgo_usd, Decimal(0))
                self.assertTrue(any("max_riesgo_usd" in a for a in cfg.advertencias))

    def test_valor_tick(self):
        cfg = config_riesgo()
        self.assertEqual(cfg.valor_tick("mnq"), Decimal("0.5"))
        self.assertEqual(cfg.valor_tick("MES"), Decimal("1.25"))
        with self.assertRaises(ValueError) as e:
            cfg.valor_tick("M6E")
        self.assertIn("valor_tick", str(e.exception))

    def test_un_valor_tick_TODO_no_vale(self):
        cfg = config_riesgo(flota=flota_con_ticks(valor_tick={"MNQ": "TODO"}))
        with self.assertRaises(ValueError):
            cfg.valor_tick("MNQ")


class TestRiesgoEnOrdenes(unittest.TestCase):

    def test_usd_sin_ceros_de_mas(self):
        self.assertEqual([usd(Decimal(x)) for x in ("80", "12.50", "48.75", "0.5")],
                         ["80", "12.5", "48.75", "0.5"])

    def test_calcula_riesgo_y_ganancia_por_cuenta(self):
        # 40 ticks x 0.50 USD x 3 contratos = 60; 80 ticks -> 120.
        t = expandir_orden(parse_orden("MNQ buy 3 40 80 TV1"), config_riesgo())[0]
        self.assertEqual((t["riesgo_usd"], t["ganancia_usd"]), ("60", "120"))
        t = expandir_orden(parse_orden("MES sell 3 13 27 TV1"), config_riesgo())[0]
        self.assertEqual((t["riesgo_usd"], t["ganancia_usd"]), ("48.75", "101.25"))

    def test_el_trabajo_se_puede_guardar_en_json(self):
        t = expandir_orden(parse_orden("MNQ buy 1 40 80 TV1"), config_riesgo())[0]
        json.dumps(t)

    def test_justo_en_el_tope_pasa_y_un_tick_mas_no(self):
        # 800 USD = 1600 ticks x 0.50 x 1 contrato.
        expandir_orden(parse_orden("MNQ buy 1 1600 80 TV1"), config_riesgo())
        with self.assertRaises(ValueError) as e:
            expandir_orden(parse_orden("MNQ buy 1 1601 80 TV1"), config_riesgo())
        self.assertIn("800.5 USD", str(e.exception))
        self.assertIn("800 USD", str(e.exception))

    def test_el_tope_es_POR_CUENTA_y_no_suma_las_cuentas(self):
        # 600 ticks x 0.5 x 1 = 300 por cuenta; con all son 3 cuentas -> 900
        # en total, pero el tope es por cuenta: pasa.
        trabajos = expandir_orden(parse_orden("MNQ buy 1 600 80 all"), config_riesgo(tope=300))
        self.assertEqual(len(trabajos), 3)
        self.assertTrue(all(t["riesgo_usd"] == "300" for t in trabajos))

    def test_un_lote_con_una_orden_de_mas_riesgo_se_rechaza_entero(self):
        ordenes = [parse_orden("MNQ buy 1 40 80 TV1"), parse_orden("MNQ buy 3 1000 80 TV1")]
        with self.assertRaises(ValueError) as e:
            expandir_lote(ordenes, config_riesgo())
        self.assertIn("linea 2", str(e.exception))

    def test_con_tope_y_sin_valor_tick_se_rechaza(self):
        with self.assertRaises(ValueError) as e:
            expandir_orden(parse_orden("M6E buy 1 10 20 TV1"), config_riesgo())
        self.assertIn("valor_tick", str(e.exception))

    def test_sin_tope_y_sin_valor_tick_sigue_como_antes(self):
        t = expandir_orden(parse_orden("MNQ buy 1 40 80 TV1"), config_de_prueba())[0]
        self.assertIsNone(t["riesgo_usd"])

    def test_tope_invalido_rechaza_todo(self):
        with self.assertRaises(ValueError):
            expandir_orden(parse_orden("MNQ buy 1 1 1 TV1"), config_riesgo(tope="talvez"))


class TestMarcadoresNuevos(unittest.TestCase):

    def test_contratos_menos_1_y_dolares(self):
        v = valores_de(dict(trabajo(contratos=4), riesgo_usd="80", ganancia_usd="160"), None)
        self.assertEqual(rellenar("{contratos_menos_1}|{riesgo_usd}|{ganancia_usd}", v),
                         "3|80|160")

    def test_cuenta_ultimos4(self):
        cfg = config_de_prueba(local=local_con(cuentas={
            "TOP1": {"plataforma": "tradovate", "max_contratos": 1,
                     "cuenta_plataforma": "668214-20702304"}}, cuenta_por_defecto=None))
        v = valores_de(trabajo("TOP1"), cfg.cuentas["TOP1"])
        self.assertEqual(rellenar("(...{cuenta_ultimos4})", v), "(...2304)")

    def test_sin_dolares_el_error_dice_que_falta(self):
        v = valores_de(trabajo(), None)
        with self.assertRaises(ValueError) as e:
            rellenar("{riesgo_usd}", v)
        self.assertIn("valor_tick", str(e.exception))


def plataforma_con(*pasos):
    p = copy.deepcopy(FLOTA_PRUEBA["plataformas"]["tradovate"])
    p["pasos_orden"] = list(pasos)
    return p


class TestValidarPasosNuevos(unittest.TestCase):

    def test_los_pasos_nuevos_validos(self):
        p = plataforma_con(
            {"accion": "repetir", "selector": "#mas", "veces": "{contratos_menos_1}"},
            {"accion": "marcar", "selector": "#auto"},
            {"accion": "verificar", "selector": "#b", "contiene": "+{contratos}"},
            {"accion": "verificar", "selector": "#r", "igual": "{riesgo_usd}", "numerico": True})
        self.assertEqual(validar_plataforma("x", p), [])

    def test_errores_de_forma(self):
        casos = [
            ({"accion": "repetir", "selector": "#mas"}, "falta 'veces'"),
            ({"accion": "marcar"}, "falta 'selector'"),
            ({"accion": "verificar", "selector": "#b"}, "'igual' o 'contiene'"),
            ({"accion": "verificar", "selector": "#b", "igual": "1", "contiene": "1"},
             "'igual' o 'contiene'"),
            ({"accion": "verificar", "selector": "#b", "contiene": "{precio}"}, "{precio}"),
            ({"accion": "repetir", "selector": "#m", "veces": "{x}"}, "{x}"),
        ]
        for paso, esperado in casos:
            with self.subTest(paso=paso):
                problemas = " | ".join(validar_plataforma("x", plataforma_con(paso)))
                self.assertIn(esperado, problemas)


class TestInterpretePasosNuevos(unittest.TestCase):

    def setUp(self):
        self.pw = PlaywrightFalso()
        self.pw.chromium.launch_persistent_context()
        self.page = self.pw.pagina()
        self.addCleanup(ejecutor.limpiar_aborto)
        self.v = valores_de(dict(trabajo(contratos=3), riesgo_usd="60", ganancia_usd="120"),
                            None)

    def correr(self, *pasos):
        ejecutar_pasos(self.page, list(pasos), self.v, 100, "orden")

    def test_repetir_hace_N_clics(self):
        self.correr({"accion": "repetir", "selector": "#mas", "veces": "{contratos_menos_1}"})
        self.assertEqual(self.pw.clics(), ["#mas", "#mas"])

    def test_repetir_cero_veces_no_hace_nada(self):
        self.v["contratos_menos_1"] = "0"
        self.correr({"accion": "repetir", "selector": "#mas", "veces": "{contratos_menos_1}"})
        self.assertEqual(self.pw.clics(), [])

    def test_repetir_tiene_tope_y_no_acepta_basura(self):
        for veces in (str(MAXIMO_REPETICIONES + 1), "-1", "dos"):
            with self.subTest(veces=veces):
                with self.assertRaises(ValueError):
                    self.correr({"accion": "repetir", "selector": "#mas", "veces": veces})
        self.assertEqual(self.pw.clics(), [])

    def test_un_stop_corta_a_mitad_de_las_repeticiones(self):
        def pedir_stop(pagina, selector):
            ejecutor.solicitar_aborto()
        self.page.efecto_clic = pedir_stop
        with self.assertRaises(OperacionAbortada):
            self.correr({"accion": "repetir", "selector": "#mas", "veces": "5"})
        self.assertEqual(self.pw.clics(), ["#mas"])

    def test_escribir_tecleando_es_clic_seleccionar_todo_y_teclear(self):
        # Medido en TopstepX: con fill() el autocompletado no filtra, y
        # fill("") no vacia el campo (React lo repone).
        self.correr({"accion": "escribir", "selector": "#c", "valor": "{contrato}",
                     "teclear": True})
        hechos = [(a, s, d) for _, a, s, d in self.pw.registro if a != "lanzar"]
        self.assertEqual(hechos, [("click", "#c", None), ("tecla", "#c", "Control+a"),
                                  ("teclear", "#c", "MNQZ6")])
        self.assertEqual(self.pw.acciones("escribir"), [], "no tiene que usar fill")

    def test_marcar_usa_check_y_no_click(self):
        # Un click desmarcaria una casilla que ya estaba marcada.
        self.correr({"accion": "marcar", "selector": "#auto"})
        self.assertEqual(self.pw.clics(), [])
        self.assertEqual(self.pw.acciones("marcar")[0][2], "#auto")

    def test_verificar_contiene(self):
        self.page.textos["#buy"] = "BUY +3 MARKET"
        self.correr({"accion": "verificar", "selector": "#buy", "contiene": "+{contratos} MARKET"})
        self.page.textos["#buy"] = "BUY +30 MARKET"
        with self.assertRaises(VerificacionFallida) as e:
            self.correr({"accion": "verificar", "selector": "#buy",
                         "contiene": "+{contratos} MARKET"})
        self.assertIn("NO se hace clic", str(e.exception))

    def test_verificar_igual_exacto(self):
        self.page.textos["#c"] = "MNQZ6"
        self.correr({"accion": "verificar", "selector": "#c", "igual": "{contrato}"})
        self.page.textos["#c"] = "MNQ"
        with self.assertRaises(VerificacionFallida):
            self.correr({"accion": "verificar", "selector": "#c", "igual": "{contrato}"})

    def test_verificar_numerico_tolera_formato(self):
        for leido in ("60", "60.00", "$60", " 60.0 "):
            with self.subTest(leido=leido):
                self.page.textos["#r"] = leido
                self.correr({"accion": "verificar", "selector": "#r", "igual": "{riesgo_usd}",
                             "numerico": True})
        for leido in ("", "6", "600", "sesenta"):
            with self.subTest(leido=leido):
                self.page.textos["#r"] = leido
                with self.assertRaises(VerificacionFallida):
                    self.correr({"accion": "verificar", "selector": "#r",
                                 "igual": "{riesgo_usd}", "numerico": True})


class TestVerificarEnElEjecutor(unittest.TestCase):
    """De punta a punta: un verificar que no coincide no deja hacer clic y
    bloquea la cuenta por el resto del lote."""

    def crear(self, texto_boton):
        flota = copy.deepcopy(FLOTA_PRUEBA)
        flota["plataformas"]["tradovate"]["pasos_orden"].append(
            {"accion": "verificar", "selector": "#comprar", "contiene": "+{contratos}"})
        cfg = config_de_prueba(flota=flota, local=local_con(dry_run=False))
        self.pw = PlaywrightFalso(
            preparar_pagina=lambda p: p.textos.update({"#comprar": texto_boton}))
        self.ej = EjecutorWeb(cfg, fabrica=fabrica(self.pw), dir_diagnosticos=dir_temporal())
        self.addCleanup(self.ej.cerrar)
        self.addCleanup(ejecutor.limpiar_aborto)

    def test_no_coincide_no_hay_clic_y_la_cuenta_queda_bloqueada(self):
        self.crear("BUY +1")
        res = self.ej.colocar_lote([trabajo("TV1", contratos=2), trabajo("TV1")])
        self.assertNotIn("#comprar", self.pw.clics())
        self.assertIn("NO se hace clic", res[0].error)
        self.assertTrue(res[1].omitida)

    def test_coincide_y_sigue_hasta_el_clic(self):
        self.crear("BUY +2")
        self.ej.colocar_lote([trabajo("TV1", contratos=2)])
        self.assertEqual(self.pw.clics().count("#comprar"), 1)


class TestTextoDeLaFila(unittest.TestCase):
    """TopstepX muestra '/M6E' en la fila, no 'M6EZ26' (posicion real del
    11/11/2026). Buscando el contrato, ninguna orden real se confirmaria."""

    def test_por_defecto_busca_el_contrato(self):
        v = valores_de(trabajo(), None)
        self.assertEqual(texto_de_fila({}, v), "MNQZ6")

    def test_con_plantilla(self):
        v = valores_de(trabajo(simbolo="M6E"), None)
        self.assertEqual(texto_de_fila({"texto_fila": "/{simbolo}"}, v), "/M6E")

    def test_marcador_desconocido_bloquea_la_plataforma(self):
        p = plataforma_con({"accion": "click", "selector": "#a"})
        p["posiciones"]["texto_fila"] = "/{raiz}"
        self.assertIn("{raiz}", " | ".join(validar_plataforma("x", p)))

    def crear(self, texto_fila):
        flota = copy.deepcopy(FLOTA_PRUEBA)
        if texto_fila:
            flota["plataformas"]["tradovate"]["posiciones"]["texto_fila"] = texto_fila
        cfg = config_de_prueba(flota=flota, local=local_con(dry_run=False))

        def preparar(pagina):
            def vender(pag, selector):
                if selector == "#vender":     # como TopstepX: "/MNQ" y "-1"
                    pag.posiciones["/MNQ"] = "-1"
            pagina.efecto_clic = vender
        self.pw = PlaywrightFalso(preparar_pagina=preparar)
        self.ej = EjecutorWeb(cfg, fabrica=fabrica(self.pw), dir_diagnosticos=dir_temporal())
        self.addCleanup(self.ej.cerrar)
        self.addCleanup(ejecutor.limpiar_aborto)

    def test_con_texto_fila_la_venta_se_confirma(self):
        self.crear("/{simbolo}")
        res = self.ej.colocar_lote([trabajo("TV1", accion="sell")])
        self.assertIs(res[0].confirmado, True)
        self.assertIn("[] -> ['-1']", res[0].detalle)

    def test_buscando_el_contrato_no_se_confirmaria(self):
        # El bug que evita texto_fila: la fila nunca "menciona" MNQZ6.
        self.crear(None)
        res = self.ej.colocar_lote([trabajo("TV1", accion="sell")])
        self.assertIs(res[0].confirmado, False)


class TestConfigRealDeTopstepX(unittest.TestCase):
    """Lo que quedo en config.json despues de medir TopstepX (11/11/2026)."""

    def setUp(self):
        self.flota = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))
        self.tsx = self.flota["plataformas"]["topstepx"]

    def test_esta_completa(self):
        # La fila se midio con una posicion real abierta (11/11/2026).
        self.assertEqual(validar_plataforma("topstepx", self.tsx), [])

    def test_las_filas_se_buscan_DENTRO_del_panel_de_posiciones(self):
        # Orders y Trades son tablas del mismo tipo (MUI DataGrid): un
        # selector suelto podria leer filas de ordenes pendientes.
        pos = self.tsx["posiciones"]
        self.assertTrue(pos["filas"].startswith(pos["panel"] + " "))

    def test_el_bracket_va_en_dolares_y_se_verifica(self):
        pasos = self.tsx["pasos_orden"]
        valores = [p.get("valor") for p in pasos if p["accion"] == "escribir"]
        self.assertIn("{riesgo_usd}", valores)
        self.assertIn("{ganancia_usd}", valores)
        verificados = [p.get("igual") for p in pasos if p["accion"] == "verificar"]
        self.assertIn("{riesgo_usd}", verificados)
        self.assertIn("{ganancia_usd}", verificados)
        self.assertTrue(any(p["accion"] == "marcar" for p in pasos))

    def test_la_fila_se_busca_por_la_raiz_con_barra(self):
        self.assertEqual(self.tsx["posiciones"]["texto_fila"], "/{simbolo}")

    def test_la_cuenta_elegida_se_verifica(self):
        ultimo = self.tsx["pasos_seleccionar_cuenta"][-1]
        self.assertEqual(ultimo["accion"], "verificar")
        self.assertIn("{cuenta_ultimos4}", ultimo["contiene"])

    def test_ningun_paso_toca_comprar_vender_ni_cerrar(self):
        # Los botones peligrosos solo se tocan en el clic final del ejecutor.
        peligrosos = ("dom-click-button-buy", "dom-click-button-sell", "close-position",
                      "flatten", "reverse", "cancel")
        for lista in ("pasos_seleccionar_cuenta", "pasos_orden"):
            for paso in self.tsx[lista]:
                if paso["accion"] in ("click", "repetir", "marcar", "escribir", "tecla"):
                    with self.subTest(lista=lista, paso=paso):
                        self.assertFalse(any(x in paso.get("selector", "") for x in peligrosos))

    def test_hay_valor_tick_para_cada_simbolo(self):
        for simbolo in self.tsx["simbolos"]:
            with self.subTest(simbolo=simbolo):
                self.assertIn(simbolo, self.flota["valor_tick"])


if __name__ == "__main__":
    unittest.main()
