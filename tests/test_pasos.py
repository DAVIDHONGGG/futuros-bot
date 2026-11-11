"""Validacion de plataformas, marcadores e interprete de pasos (pasos.py), y
abrir_perfil.py."""

import contextlib
import copy
import io
import json
import unittest
from pathlib import Path

from tests.apoyo import FLOTA_PRUEBA, config_de_prueba, trabajo
from tests.navegador_falso import PlaywrightFalso, fabrica

import abrir_perfil
import ejecutor
from config import Config
from ejecutor import OperacionAbortada
from pasos import ejecutar_pasos, rellenar, validar_plataforma, valores_de

RAIZ = Path(__file__).resolve().parent.parent


def plataforma_ok():
    return copy.deepcopy(FLOTA_PRUEBA["plataformas"]["tradovate"])


class TestValidarPlataforma(unittest.TestCase):

    def test_la_de_prueba_esta_completa(self):
        self.assertEqual(validar_plataforma("tradovate", plataforma_ok()), [])

    def test_un_TODO_en_cualquier_selector_la_bloquea(self):
        for ruta in (("url",), ("boton_comprar",), ("sesion_iniciada",),
                     ("posiciones", "cantidad"), ("pasos_orden", 3, "selector")):
            with self.subTest(ruta=ruta):
                p = plataforma_ok()
                destino = p
                for clave in ruta[:-1]:
                    destino = destino[clave]
                destino[ruta[-1]] = "TODO: falta"
                self.assertTrue(validar_plataforma("x", p))

    def test_errores_de_forma(self):
        casos = [
            ({"accion": "volar", "selector": "#a"}, "desconocida"),
            ({"accion": "click"}, "falta 'selector'"),
            ({"accion": "escribir", "selector": "#a"}, "falta 'valor'"),
            ({"accion": "esperar"}, "necesita 'ms' o 'selector'"),
            ({"accion": "escribir", "selector": "#a", "valor": "{precio}"}, "{precio}"),
        ]
        for paso, esperado in casos:
            with self.subTest(paso=paso):
                p = plataforma_ok()
                p["pasos_orden"] = [paso]
                problemas = " | ".join(validar_plataforma("x", p))
                self.assertIn(esperado, problemas)

    def test_sin_panel_de_posiciones_no_se_puede_verificar(self):
        p = plataforma_ok()
        del p["posiciones"]
        self.assertTrue(any("posiciones" in x for x in validar_plataforma("x", p)))

    def test_las_del_config_json_real_tienen_la_forma_correcta(self):
        # Las que tienen TODO pueden tenerlos, pero ningun problema puede ser
        # de FORMA: cada uno tiene que ser un "sin configurar". TopstepX ya
        # esta medida completa (11/11/2026): no puede tener ninguno.
        flota = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))
        for nombre, datos in flota["plataformas"].items():
            with self.subTest(plataforma=nombre):
                problemas = validar_plataforma(nombre, datos)
                if nombre == "topstepx":
                    self.assertEqual(problemas, [])
                    continue
                self.assertTrue(problemas, "la plataforma real ya no tiene TODO?")
                for p in problemas:
                    self.assertIn("sin configurar", p)


class TestMarcadores(unittest.TestCase):

    def test_rellena(self):
        valores = valores_de(dict(trabajo(contratos=2), contrato="MNQZ6"), None)
        self.assertEqual(rellenar("{contrato} x{contratos} {sl_ticks}/{tp_ticks}", valores),
                         "MNQZ6 x2 40/80")

    def test_cuenta_plataforma_sin_configurar_es_error(self):
        valores = valores_de(trabajo(), None)
        with self.assertRaises(ValueError) as e:
            rellenar("{cuenta_plataforma}", valores)
        self.assertIn("cuenta_plataforma", str(e.exception))


class TestInterprete(unittest.TestCase):

    def setUp(self):
        self.pw = PlaywrightFalso()
        self.pw.chromium.launch_persistent_context()
        self.page = self.pw.pagina()
        self.addCleanup(ejecutor.limpiar_aborto)

    def test_cada_accion(self):
        pasos = [
            {"accion": "click", "selector": "#a"},
            {"accion": "escribir", "selector": "#b", "valor": "{contratos}"},
            {"accion": "click_texto", "texto": "{contrato}"},
            {"accion": "tecla", "tecla": "Enter"},
            {"accion": "tecla", "tecla": "Tab", "selector": "#b"},
            {"accion": "esperar", "selector": "#c", "ms": 10},
        ]
        ejecutar_pasos(self.page, pasos, valores_de(trabajo(contratos=3), None), 100, "p")
        hechos = [(a, s, d) for _, a, s, d in self.pw.registro if a != "lanzar"]
        self.assertEqual(hechos, [
            ("click", "#a", None), ("escribir", "#b", "3"), ("click", "texto=MNQZ6", None),
            ("tecla", None, "Enter"), ("tecla", "#b", "Tab"), ("esperar", "#c", None)])

    def test_el_aborto_corta_entre_pasos(self):
        ejecutor.solicitar_aborto()
        with self.assertRaises(OperacionAbortada):
            ejecutar_pasos(self.page, [{"accion": "click", "selector": "#a"}], {}, 100, "p")
        self.assertEqual(self.pw.clics(), [])

    def test_la_limpieza_no_se_corta_por_el_aborto(self):
        ejecutor.solicitar_aborto()
        ejecutar_pasos(self.page, [{"accion": "tecla", "tecla": "Escape"}], {}, 100,
                       "limpiar", con_aborto=False)
        self.assertEqual(self.pw.acciones("tecla")[0][3], "Escape")


class TestAbrirPerfil(unittest.TestCase):

    def setUp(self):
        # abrir_perfil le habla al operador por consola; en la suite, callado.
        silencio = contextlib.redirect_stdout(io.StringIO())
        silencio.__enter__()
        self.addCleanup(silencio.__exit__, None, None, None)

    def test_se_niega_con_el_listener_corriendo(self):
        pw = PlaywrightFalso()
        codigo = abrir_perfil.main(["TV1"], config=config_de_prueba(),
                                   fabrica=fabrica(pw), corriendo=lambda: True)
        self.assertEqual(codigo, 1)
        self.assertEqual(pw.acciones("lanzar"), [])

    def test_cuenta_desconocida(self):
        self.assertEqual(abrir_perfil.main(["XX9"], config=config_de_prueba(),
                                           corriendo=lambda: False), 1)

    def test_abre_el_perfil_a_la_vista_y_espera_que_lo_cierren(self):
        pw = PlaywrightFalso()
        cfg = config_de_prueba()
        codigo = abrir_perfil.main(["tv1"], config=cfg, fabrica=fabrica(pw),
                                   corriendo=lambda: False)
        self.assertEqual(codigo, 0)
        opciones = pw.acciones("lanzar")[0][3]
        self.assertEqual(opciones["user_data_dir"], str(cfg.cuentas["TV1"].perfil))
        self.assertFalse(opciones["headless"])
        self.assertFalse(any("window-position" in a for a in opciones["args"]))
        self.assertEqual(pw.acciones("goto")[0][3], "https://tradovate.prueba/trader")
        self.assertTrue(pw.acciones("esperar_evento"))
        self.assertTrue(pw.detenido)


class TestNavegadorEnConfig(unittest.TestCase):

    def test_defaults_y_valores_raros(self):
        flota = dict(FLOTA_PRUEBA)
        flota["navegador"] = {"timeout_ms": "abc", "segundos_verificacion": 0,
                              "headless": "si"}
        nav = config_de_prueba(flota=flota).navegador
        self.assertEqual(nav["timeout_ms"], 10000)          # invalido -> default
        self.assertEqual(nav["segundos_verificacion"], 1)    # minimo 1
        self.assertFalse(nav["headless"])                    # solo true explicito
        self.assertIsNone(nav["canal"])

    def test_sin_seccion(self):
        c = Config(base_dir=Path(__file__).parent / "no-existe", entorno={})
        self.assertEqual(c.navegador["segundos_verificacion"], 10)


if __name__ == "__main__":
    unittest.main()
