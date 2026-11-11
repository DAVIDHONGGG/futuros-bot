"""Parser y validacion de ordenes y cierres (ordenes.py)."""

import unittest
from datetime import datetime

from tests.apoyo import config_de_prueba, local_con

from ordenes import (GRACIA_HORA_PASADA, agrupar_por_hora, expandir_lote, expandir_orden,
                     hora_objetivo, parse_cierre, parse_orden, resolver_cuentas)


class TestParseOrden(unittest.TestCase):

    def test_minima(self):
        self.assertEqual(parse_orden("MNQ buy 1 40 80"), {
            "simbolo": "MNQ", "accion": "buy", "contratos": 1, "sl_ticks": 40,
            "tp_ticks": 80, "cuenta": None, "at": None})

    def test_normaliza_mayusculas(self):
        o = parse_orden("mnq SELL 2 30 60")
        self.assertEqual((o["simbolo"], o["accion"]), ("MNQ", "sell"))

    def test_con_cuenta_y_hora(self):
        o = parse_orden("MNQ buy 1 40 80 TS1 14:30")
        self.assertEqual((o["cuenta"], o["at"]), ("TS1", "14:30"))

    def test_solo_hora(self):
        o = parse_orden("MNQ buy 1 40 80 9:05")
        self.assertEqual((o["cuenta"], o["at"]), (None, "9:05"))

    def test_varias_cuentas_y_all(self):
        self.assertEqual(parse_orden("MNQ buy 1 40 80 TS1,TV2")["cuenta"], "TS1,TV2")
        self.assertEqual(parse_orden("MNQ buy 1 40 80 all")["cuenta"], "all")

    def test_contratos_con_decimales_se_rechazan(self):
        # Un contrato no se fracciona y redondear en silencio es operar una
        # cantidad que nadie escribio.
        for valor in ("1.0", "1.5", "1,5", "0.5", "2.", ".5"):
            with self.subTest(contratos=valor):
                with self.assertRaises(ValueError) as e:
                    parse_orden(f"MNQ buy {valor} 40 80")
                self.assertIn("CONTRATOS", str(e.exception))

    def test_sl_y_tp_tambien_enteros(self):
        with self.assertRaises(ValueError) as e:
            parse_orden("MNQ buy 1 40.5 80")
        self.assertIn("SL_TICKS", str(e.exception))
        with self.assertRaises(ValueError) as e:
            parse_orden("MNQ buy 1 40 8e1")
        self.assertIn("TP_TICKS", str(e.exception))

    def test_cero_y_negativos(self):
        for texto in ("MNQ buy 0 40 80", "MNQ buy 1 0 80", "MNQ buy 1 40 0",
                      "MNQ buy -1 40 80", "MNQ buy +1 40 80"):
            with self.subTest(texto=texto):
                with self.assertRaises(ValueError):
                    parse_orden(texto)

    def test_accion_invalida(self):
        with self.assertRaises(ValueError) as e:
            parse_orden("MNQ long 1 40 80")
        self.assertIn("buy o sell", str(e.exception))

    def test_cantidad_de_campos(self):
        for texto in ("MNQ buy 1 40", "MNQ buy 1 40 80 TS1 14:30 extra",
                      "MNQ buy 1 40 80 TS1 TV2", ""):
            with self.subTest(texto=texto):
                with self.assertRaises(ValueError):
                    parse_orden(texto)

    def test_una_hora_invalida_no_pasa_como_cuenta(self):
        with self.assertRaises(ValueError) as e:
            parse_orden("MNQ buy 1 40 80 25:00")
        self.assertIn("HH:MM", str(e.exception))
        with self.assertRaises(ValueError):
            parse_orden("MNQ buy 1 40 80 TS1 25:00")

    def test_simbolo_raro(self):
        with self.assertRaises(ValueError):
            parse_orden("MNQ/Z6 buy 1 40 80")


class TestParseCierre(unittest.TestCase):

    def test_formas(self):
        casos = {
            "close": ("actual", None, None),
            "cerrar": ("actual", None, None),
            "close TS1": ("cuenta", "TS1", None),
            "ts1 close": ("cuenta", "TS1", None),
            "close TS1,TV2": ("cuenta", "TS1,TV2", None),
            "close all": ("todas", None, None),
            "close all accounts": ("todas", None, None),
            "Cerrar todas las cuentas": ("todas", None, None),
            "close 15:30": ("actual", None, "15:30"),
            "TS1 close 15:30": ("cuenta", "TS1", "15:30"),
            "close all accounts 15:30": ("todas", None, "15:30"),
        }
        for texto, (modo, cuenta, hora) in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(parse_cierre(texto),
                                 {"modo": modo, "cuenta": cuenta, "at": hora})

    def test_no_es_un_cierre(self):
        for texto in ("MNQ buy 1 40 80", "closed", "close TS1 TV2", "15:30", "",
                      "close 1.5"):
            with self.subTest(texto=texto):
                self.assertIsNone(parse_cierre(texto))


class TestResolverYExpandir(unittest.TestCase):

    def setUp(self):
        self.c = config_de_prueba()

    def test_sin_cuenta_va_a_la_por_defecto(self):
        self.assertEqual(resolver_cuentas(None, self.c), ["TV1"])

    def test_sin_cuenta_y_sin_defecto_es_error(self):
        c = config_de_prueba(local=local_con(cuenta_por_defecto=None))
        with self.assertRaises(ValueError) as e:
            resolver_cuentas(None, c)
        self.assertIn("Indica la cuenta", str(e.exception))

    def test_all_y_lista(self):
        self.assertEqual(sorted(resolver_cuentas("all", self.c)), ["TS1", "TV1", "TV2"])
        self.assertEqual(resolver_cuentas("tv2,TS1,tv2", self.c), ["TV2", "TS1"])

    def test_cuenta_desconocida(self):
        with self.assertRaises(ValueError) as e:
            resolver_cuentas("TV1,XX9", self.c)
        self.assertIn("XX9", str(e.exception))

    def test_expande_y_traduce_el_contrato(self):
        trabajos = expandir_orden(parse_orden("MNQ buy 1 40 80 TV1,TS1"), self.c)
        self.assertEqual([(t["cuenta"], t["plataforma"], t["contrato"]) for t in trabajos],
                         [("TV1", "tradovate", "MNQZ6"), ("TS1", "topstepx", "MNQ-TSX")])

    def test_respeta_el_maximo_de_la_cuenta(self):
        with self.assertRaises(ValueError) as e:
            expandir_orden(parse_orden("MNQ buy 2 40 80 TV2"), self.c)
        self.assertIn("maximo", str(e.exception))
        # En el limite, pasa.
        self.assertEqual(len(expandir_orden(parse_orden("MNQ buy 3 40 80 TV1"), self.c)), 1)

    def test_simbolo_sin_contrato_en_esa_plataforma(self):
        with self.assertRaises(ValueError):
            expandir_orden(parse_orden("MES buy 1 40 80 TS1"), self.c)

    def test_un_lote_con_una_linea_mala_se_rechaza_entero(self):
        ordenes = [parse_orden("MNQ buy 1 40 80 TV1"), parse_orden("MNQ buy 5 40 80 TV2")]
        with self.assertRaises(ValueError) as e:
            expandir_lote(ordenes, self.c)
        self.assertIn("linea 2", str(e.exception))


class TestHoras(unittest.TestCase):

    def test_mas_tarde_hoy(self):
        ahora = datetime(2026, 11, 11, 10, 0)
        self.assertEqual(hora_objetivo("14:30", ahora), datetime(2026, 11, 11, 14, 30))

    def test_ya_paso_va_a_manana(self):
        ahora = datetime(2026, 11, 11, 15, 0)
        self.assertEqual(hora_objetivo("14:30", ahora), datetime(2026, 11, 12, 14, 30))

    def test_dentro_de_la_gracia_es_ya(self):
        ahora = datetime(2026, 11, 11, 14, 38)
        self.assertEqual(hora_objetivo("14:30", ahora), datetime(2026, 11, 11, 14, 30))
        self.assertEqual(GRACIA_HORA_PASADA.total_seconds(), 600)

    def test_agrupa_por_hora_preservando_el_orden(self):
        grupos = agrupar_por_hora([{"s": 1, "at": None}, {"s": 2, "at": "14:30"},
                                   {"s": 3, "at": "14:30"}, {"s": 4, "at": "16:00"}])
        self.assertEqual(list(grupos), [None, "14:30", "16:00"])
        self.assertEqual([o["s"] for o in grupos["14:30"]], [2, 3])


if __name__ == "__main__":
    unittest.main()
