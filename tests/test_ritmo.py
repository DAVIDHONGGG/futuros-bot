"""Ritmo: la entrada sale con calma (TopstepX, ~12 s hasta el clic en BUY).

Lo que se prueba es lo que importa para la plata:
  - el clic NO sale antes del tiempo pedido;
  - un stop durante las pausas corta antes del clic;
  - con la posicion abierta (SL/TP) no se espera nada;
  - una config absurda (un minuto, negativo) no se acepta.
Los tests usan segundos chicos: el navegador falso espera de verdad.
"""

import copy
import json
import random
import threading
import time
import unittest
from pathlib import Path

from tests.apoyo import FLOTA_PRUEBA, config_de_prueba, dir_temporal, local_con, trabajo
from tests.navegador_falso import PlaywrightFalso, fabrica

import ejecutor
from ejecutor_web import EjecutorWeb, Ritmo
from pasos import validar_plataforma

RAIZ = Path(__file__).resolve().parent.parent


def plataforma(**cambios):
    p = copy.deepcopy(FLOTA_PRUEBA["plataformas"]["tradovate"])
    p.update(copy.deepcopy(cambios))
    return p


class TestValidacion(unittest.TestCase):

    def problemas(self, ritmo):
        return " | ".join(validar_plataforma("x", plataforma(ritmo=ritmo)))

    def test_valido(self):
        self.assertEqual(self.problemas({"segundos_hasta_el_clic": 12, "variacion_s": 2}), "")
        self.assertEqual(self.problemas({"segundos_hasta_el_clic": 0.5}), "")

    def test_absurdos(self):
        for ritmo in ({"segundos_hasta_el_clic": 61}, {"segundos_hasta_el_clic": 0},
                      {"segundos_hasta_el_clic": -3}, {"segundos_hasta_el_clic": "12"},
                      {"segundos_hasta_el_clic": True}, {},
                      {"segundos_hasta_el_clic": 12, "variacion_s": 12},
                      {"segundos_hasta_el_clic": 12, "variacion_s": -1}, "12"):
            with self.subTest(ritmo=ritmo):
                self.assertNotEqual(self.problemas(ritmo), "")


class _AzarFijo:
    """random con un valor fijo: uniform(a, b) devuelve el medio."""
    def uniform(self, a, b):
        return (a + b) / 2


class TestRitmoSolo(unittest.TestCase):

    def setUp(self):
        self.pw = PlaywrightFalso()
        self.pw.chromium.launch_persistent_context()
        self.page = self.pw.pagina()
        self.addCleanup(ejecutor.limpiar_aborto)

    def test_reparte_y_llega_al_limite(self):
        inicio = time.monotonic()
        r = Ritmo(self.page, {"segundos_hasta_el_clic": 0.6}, inicio, pausas=4, azar=_AzarFijo())
        for i in range(3):
            r.antes_de_un_paso(f"paso {i}")
        self.assertLess(time.monotonic() - inicio, 0.6, "las pausas de los pasos no llegan al limite")
        r.antes_del_clic("clic")
        self.assertGreaterEqual(time.monotonic() - inicio, 0.58)

    def test_las_pausas_son_desparejas(self):
        r = Ritmo(self.page, {"segundos_hasta_el_clic": 1}, time.monotonic(), pausas=6,
                  azar=random.Random(7))
        duraciones = []
        for i in range(5):
            t = time.monotonic()
            r.antes_de_un_paso(f"paso {i}")
            duraciones.append(time.monotonic() - t)
        self.assertGreater(max(duraciones) - min(duraciones), 0.03)

    def test_si_ya_paso_el_tiempo_no_espera(self):
        r = Ritmo(self.page, {"segundos_hasta_el_clic": 1}, time.monotonic() - 5, pausas=3)
        t = time.monotonic()
        r.antes_de_un_paso("paso")
        r.antes_del_clic("clic")
        self.assertLess(time.monotonic() - t, 0.05)

    def test_la_variacion_queda_en_el_rango(self):
        for semilla in range(30):
            r = Ritmo(self.page, {"segundos_hasta_el_clic": 12, "variacion_s": 2}, 0.0,
                      pausas=5, azar=random.Random(semilla))
            self.assertTrue(10 <= r.limite <= 14, r.limite)


class TestRitmoEnLaOrden(unittest.TestCase):

    def crear(self, ritmo, pasos_despues=None, preparar=None, dry_run=False):
        flota = copy.deepcopy(FLOTA_PRUEBA)
        flota["plataformas"]["tradovate"]["ritmo"] = ritmo
        if pasos_despues:
            flota["plataformas"]["tradovate"]["pasos_despues"] = pasos_despues
        cfg = config_de_prueba(flota=flota, local=local_con(dry_run=dry_run))
        self.momentos = {}

        def _preparar(p):
            def al_clic(pag, sel):
                if sel in ("#comprar", "#vender"):
                    self.momentos["clic"] = time.monotonic()
                    pag.posiciones["MNQZ6"] = "1"
                    pag.textos["#posiciones .fila[fila=MNQZ6] >> .risk"] = "$20.00"
            p.efecto_clic = al_clic
            if preparar:
                preparar(p)
        self.pw = PlaywrightFalso(preparar_pagina=_preparar)
        self.ej = EjecutorWeb(cfg, fabrica=fabrica(self.pw), dir_diagnosticos=dir_temporal())
        self.addCleanup(self.ej.cerrar)
        self.addCleanup(ejecutor.limpiar_aborto)

    def test_el_clic_no_sale_antes_de_tiempo(self):
        self.crear({"segundos_hasta_el_clic": 0.8})
        inicio = time.monotonic()
        with self.assertLogs("bot_futuros.ejecutor_web", level="INFO") as logs:
            res = self.ej.colocar_lote([trabajo()])[0]
        self.assertIs(res.confirmado, True)
        self.assertGreaterEqual(self.momentos["clic"] - inicio, 0.75)
        self.assertLess(self.momentos["clic"] - inicio, 2.0)
        self.assertTrue(any("hasta el clic" in m for m in logs.output))

    def test_el_sl_tp_no_espera(self):
        despues = [{"accion": "verificar", "selector": ".risk", "igual": "20", "numerico": True}]
        self.crear({"segundos_hasta_el_clic": 0.8}, pasos_despues=despues)
        res = self.ej.colocar_lote([dict(trabajo(), riesgo_usd="20", ganancia_usd="40")])[0]
        fin = time.monotonic()
        self.assertIsNone(res.sin_proteccion)
        self.assertLess(fin - self.momentos["clic"], 0.5, "despues del clic no hay ritmo")

    def test_un_stop_durante_las_pausas_corta_antes_del_clic(self):
        self.crear({"segundos_hasta_el_clic": 3})
        threading.Timer(0.4, ejecutor.solicitar_aborto).start()
        inicio = time.monotonic()
        res = self.ej.colocar_lote([trabajo()])[0]
        self.assertTrue(res.omitida)
        self.assertNotIn("#comprar", self.pw.clics())
        self.assertLess(time.monotonic() - inicio, 1.5, "el stop no espero el ritmo")

    def test_en_dry_run_tambien(self):
        self.crear({"segundos_hasta_el_clic": 0.5}, dry_run=True)
        inicio = time.monotonic()
        res = self.ej.colocar_lote([trabajo()])[0]
        self.assertIsNone(res.confirmado)
        self.assertGreaterEqual(time.monotonic() - inicio, 0.45)

    def test_sin_ritmo_no_espera(self):
        flota = copy.deepcopy(FLOTA_PRUEBA)
        cfg = config_de_prueba(flota=flota, local=local_con(dry_run=True))
        pw = PlaywrightFalso()
        ej = EjecutorWeb(cfg, fabrica=fabrica(pw), dir_diagnosticos=dir_temporal())
        self.addCleanup(ej.cerrar)
        inicio = time.monotonic()
        ej.colocar_lote([trabajo()])
        self.assertLess(time.monotonic() - inicio, 0.5)


class TestConfigRealDeTopstepX(unittest.TestCase):

    def test_unos_12_segundos(self):
        flota = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))
        tsx = flota["plataformas"]["topstepx"]
        self.assertEqual(tsx["ritmo"], {"segundos_hasta_el_clic": 12, "variacion_s": 2})
        self.assertEqual(validar_plataforma("topstepx", tsx), [])

    def test_tradovate_sin_ritmo(self):
        flota = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))
        self.assertNotIn("ritmo", flota["plataformas"]["tradovate"])


if __name__ == "__main__":
    unittest.main()
