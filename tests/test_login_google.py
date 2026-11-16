"""Autologin "sin_contrasena": el boton "Continue with Google" de TopstepX.

TopstepX (16/11/2026, version 2.4.38.2) manda a login.topstep.com cuando la
sesion vencio. Con la sesion de Google guardada en el perfil, un clic
deberia alcanzar. Es una variante del autologin de Tradovate (test_tradovate):
el mismo "login", pero sin campos que autocompletar. Lo que se prueba:
  - la config no puede mezclar sin_contrasena con campos_autorrelleno;
  - nunca se escribe ni se teclea nada;
  - si Google pide algo, la orden falla con captura y no toca el ticket.
"""

import copy
import json
import unittest
from pathlib import Path

from tests.apoyo import FLOTA_PRUEBA, config_de_prueba, dir_temporal, local_con, trabajo
from tests.navegador_falso import PlaywrightFalso, fabrica

import ejecutor
from ejecutor_web import EjecutorWeb
from pasos import validar_plataforma

RAIZ = Path(__file__).resolve().parent.parent
LOGIN_GOOGLE = {"sin_contrasena": True, "boton": "#google"}


def plataforma(**cambios):
    p = copy.deepcopy(FLOTA_PRUEBA["plataformas"]["tradovate"])
    p.update(copy.deepcopy(cambios))
    return p


def llenar_al_clic(pagina, selector):
    if selector in ("#comprar", "#vender"):
        actual = int(pagina.posiciones.get("MNQZ6", "0"))
        pagina.posiciones["MNQZ6"] = str(actual + (1 if selector == "#comprar" else -1))


class TestValidacion(unittest.TestCase):

    def problemas(self, login):
        return " | ".join(validar_plataforma("x", plataforma(login=login)))

    def test_sin_contrasena_y_boton_es_valido(self):
        self.assertEqual(self.problemas(LOGIN_GOOGLE), "")

    def test_no_mezcla_con_campos_autorrelleno(self):
        login = dict(LOGIN_GOOGLE, campos_autorrelleno=["#clave"])
        self.assertIn("no las dos", self.problemas(login))

    def test_boton_TODO_no_vale(self):
        login = dict(LOGIN_GOOGLE, boton="TODO: medir")
        self.assertIn("'login.boton' sin configurar", self.problemas(login))

    def test_solo_true_lo_activa(self):
        # "si" o 1 no saltean el chequeo de autorrelleno: vuelve a pedir campos.
        login = dict(LOGIN_GOOGLE, sin_contrasena="si")
        self.assertIn("campos_autorrelleno", self.problemas(login))


class TestAutologinConGoogle(unittest.TestCase):

    def crear(self, preparar, dry_run=False):
        flota = copy.deepcopy(FLOTA_PRUEBA)
        flota["plataformas"]["tradovate"]["login"] = copy.deepcopy(LOGIN_GOOGLE)
        cfg = config_de_prueba(flota=flota, local=local_con(dry_run=dry_run))
        self.diag = dir_temporal()
        self.pw = PlaywrightFalso(preparar_pagina=preparar)
        self.ej = EjecutorWeb(cfg, fabrica=fabrica(self.pw), dir_diagnosticos=self.diag)
        self.addCleanup(self.ej.cerrar)
        self.addCleanup(ejecutor.limpiar_aborto)

    @staticmethod
    def preparar(entra=True):
        def _preparar(p):
            p.sin_sesion = True

            def al_clic(pag, sel):
                if sel == "#google" and entra:
                    pag.sin_sesion = False      # Google devolvio a /trade
                llenar_al_clic(pag, sel)
            p.efecto_clic = al_clic
        return _preparar

    def test_un_clic_recupera_la_sesion_y_la_orden_sigue(self):
        self.crear(self.preparar())
        with self.assertLogs("bot_futuros.ejecutor_web", level="WARNING") as logs:
            res = self.ej.colocar_lote([trabajo()])[0]
        self.assertIsNone(res.error)
        self.assertIs(res.confirmado, True)
        clics = self.pw.clics()
        self.assertEqual(clics[0], "#google", "el login va antes que todo")
        self.assertEqual(clics[-1], "#comprar")
        texto = "\n".join(logs.output)
        self.assertIn("sin contrasena", texto)
        self.assertIn("sesion recuperada con autologin", texto)

    def test_no_lee_ningun_campo_de_autorrelleno(self):
        self.crear(self.preparar())
        self.ej.colocar_lote([trabajo()])
        self.assertEqual([r for r in self.pw.registro if r[1] == "leer" and r[2] == "#clave"], [])

    def test_si_google_pide_algo_falla_con_captura_y_no_toca_el_ticket(self):
        self.crear(self.preparar(entra=False))
        res = self.ej.colocar_lote([trabajo()])[0]
        self.assertIn("la sesion no aparecio", res.error)
        self.assertEqual(self.pw.clics(), ["#google"])
        for accion in ("escribir", "teclear", "tecla"):
            self.assertEqual(self.pw.acciones(accion), [], accion)
        self.assertTrue(list(self.diag.glob("*-TV1-login-fallido.png")))

    def test_con_sesion_no_toca_el_boton(self):
        self.crear(lambda p: setattr(p, "efecto_clic", llenar_al_clic))
        res = self.ej.colocar_lote([trabajo()])[0]
        self.assertIs(res.confirmado, True)
        self.assertNotIn("#google", self.pw.clics())


class TestConfigRealDeTopstepX(unittest.TestCase):

    def setUp(self):
        flota = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))
        self.tsx = flota["plataformas"]["topstepx"]

    def test_login_con_el_boton_de_google_medido(self):
        self.assertEqual(self.tsx["login"], {
            "sin_contrasena": True, "boton": "button:has-text('Continue with Google')"})

    def test_nunca_el_boton_de_email_ni_facebook(self):
        texto = json.dumps(self.tsx["login"]).lower()
        for prohibido in ("continue to topstepx", "facebook", "email", "password"):
            self.assertNotIn(prohibido, texto)

    def test_la_plataforma_sigue_valida(self):
        self.assertEqual(validar_plataforma("topstepx", self.tsx), [])


if __name__ == "__main__":
    unittest.main()
