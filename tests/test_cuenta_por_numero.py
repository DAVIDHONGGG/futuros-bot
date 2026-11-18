"""La cuenta de la orden por NUMERO completo, ademas del alias (18/11/2026).

El usuario lleva un Excel con el numero de cada cuenta. Si una cuenta muere
y la reemplaza otra, quiere mandar el numero nuevo sin tocar la PC. Lo que
se prueba:
  - el numero de una cuenta configurada lleva a esa cuenta (y no la duplica);
  - un numero NO configurado usa el molde "cuenta_nueva" de la maquina;
  - sin molde, o con un alias mal escrito, sigue siendo "cuenta desconocida";
  - el ejecutor elige y verifica la cuenta por ese numero antes del clic.
"""

import copy
import unittest

from tests.apoyo import FLOTA_PRUEBA, config_de_prueba, dir_temporal, local_con, trabajo
from tests.navegador_falso import PlaywrightFalso, fabrica

import ejecutor
from config import NUMERO_DE_CUENTA
from ejecutor_web import EjecutorWeb
from ordenes import expandir_orden, parse_orden, resolver_cuentas

CUENTAS = {
    "TV1": {"plataforma": "tradovate", "max_contratos": 3, "cuenta_plataforma": "LFE0508943329-0001"},
    "TV2": {"plataforma": "tradovate", "max_contratos": 1},
}
MOLDE = {"plataforma": "tradovate", "max_contratos": 2, "perfil": "perfiles/LOGIN"}


def cfg(molde=MOLDE, **extra):
    local = local_con(cuentas=CUENTAS, cuenta_por_defecto="TV1", **extra)
    if molde is not None:
        local["cuenta_nueva"] = molde
    return config_de_prueba(local=local)


class TestQueEsUnNumero(unittest.TestCase):

    def test_numeros(self):
        for n in ("668214-39660124", "LFE05089433290001", "FTDFYSLX50527729629", "12345"):
            self.assertTrue(NUMERO_DE_CUENTA.match(n), n)

    def test_alias_no(self):
        for a in ("TOP6", "TV1", "MFF12", "LUC1234", "ALL"):
            self.assertIsNone(NUMERO_DE_CUENTA.match(a), a)


class TestResolver(unittest.TestCase):

    def test_numero_configurado_es_esa_cuenta(self):
        c = cfg()
        self.assertEqual(resolver_cuentas("lfe0508943329-0001", c), ["TV1"])

    def test_alias_y_su_numero_cuentan_una_vez(self):
        self.assertEqual(resolver_cuentas("TV1,LFE0508943329-0001", cfg()), ["TV1"])

    def test_numero_nuevo_usa_el_molde(self):
        c = cfg()
        self.assertEqual(resolver_cuentas("LFE05089433290009", c), ["LFE05089433290009"])
        nueva = c.cuenta("LFE05089433290009")
        self.assertEqual((nueva.plataforma, nueva.max_contratos, nueva.cuenta_plataforma),
                         ("tradovate", 2, "LFE05089433290009"))
        self.assertEqual(nueva.perfil.name, "LOGIN")
        self.assertIs(c.cuenta("lfe05089433290009"), nueva, "la misma cuenta, no otra")

    def test_sin_molde_un_numero_nuevo_se_rechaza(self):
        with self.assertRaises(ValueError) as e:
            resolver_cuentas("LFE05089433290009", cfg(molde=None))
        self.assertIn("desconocida", str(e.exception))
        self.assertIn("cuenta_nueva", str(e.exception))

    def test_alias_mal_escrito_sigue_siendo_desconocido(self):
        with self.assertRaises(ValueError) as e:
            resolver_cuentas("TV9", cfg())
        self.assertIn("TV9", str(e.exception))

    def test_all_son_solo_las_configuradas(self):
        c = cfg()
        c.cuenta("LFE05089433290009")       # una nueva ya usada no se suma a "all"
        self.assertEqual(sorted(resolver_cuentas("all", c)), ["TV1", "TV2"])

    def test_el_tope_de_contratos_del_molde(self):
        c = cfg()
        with self.assertRaises(ValueError) as e:
            expandir_orden(parse_orden("MNQ buy 3 40 80 LFE05089433290009"), c)
        self.assertIn("maximo de esa cuenta (2)", str(e.exception))
        t = expandir_orden(parse_orden("MNQ buy 2 40 80 LFE05089433290009"), c)[0]
        self.assertEqual((t["cuenta"], t["plataforma"], t["contrato"]),
                         ("LFE05089433290009", "tradovate", "MNQZ6"))


class TestMoldeInvalido(unittest.TestCase):

    def test_se_descarta_con_advertencia(self):
        for molde in ({"plataforma": "tradovate", "max_contratos": 2},          # sin perfil
                      {"plataforma": "nada", "max_contratos": 2, "perfil": "p"},
                      {"plataforma": "tradovate", "max_contratos": 0, "perfil": "p"},
                      "tradovate"):
            with self.subTest(molde=molde):
                c = cfg(molde=molde)
                self.assertIsNone(c.cuenta_nueva)
                self.assertTrue(any("cuenta_nueva" in a for a in c.advertencias))
                self.assertIsNone(c.cuenta("LFE05089433290009"))

    def test_es_clave_local_sin_advertencia_de_flota(self):
        c = cfg()
        self.assertFalse(any("FLOTA" in a for a in c.advertencias), c.advertencias)


class TestEjecutorConNumero(unittest.TestCase):

    def test_elige_y_verifica_la_cuenta_por_el_numero(self):
        flota = copy.deepcopy(FLOTA_PRUEBA)
        flota["plataformas"]["tradovate"]["pasos_seleccionar_cuenta"] = [
            {"accion": "click_texto", "texto": "{cuenta_plataforma}"},
            {"accion": "verificar", "selector": "#cuenta", "contiene": "{cuenta_ultimos4}"},
        ]
        local = local_con(cuentas=CUENTAS, cuenta_por_defecto="TV1", dry_run=False)
        local["cuenta_nueva"] = MOLDE
        c = config_de_prueba(flota=flota, local=local)

        def preparar(p):
            p.textos["#cuenta"] = "(...0009)"

            def al_clic(pag, sel):
                if sel == "#comprar":
                    pag.posiciones["MNQZ6"] = "1"
            p.efecto_clic = al_clic
        pw = PlaywrightFalso(preparar_pagina=preparar)
        ej = EjecutorWeb(c, fabrica=fabrica(pw), dir_diagnosticos=dir_temporal())
        self.addCleanup(ej.cerrar)
        self.addCleanup(ejecutor.limpiar_aborto)

        t = expandir_orden(parse_orden("MNQ buy 1 40 80 LFE05089433290009"), c)[0]
        res = ej.colocar_lote([t])[0]

        self.assertIs(res.confirmado, True)
        self.assertEqual(pw.clics()[0], "texto=LFE05089433290009")
        self.assertEqual(pw.clics()[-1], "#comprar")
        lanzado = pw.acciones("lanzar")[0][3]
        self.assertTrue(lanzado["user_data_dir"].endswith("LOGIN"), "perfil del molde")

    def test_trabajo_de_la_flota_de_prueba_sigue_igual(self):
        # Lo de siempre (alias) no cambio.
        c = cfg()
        self.assertEqual(expandir_orden(parse_orden("MNQ buy 1 40 80 TV1"), c)[0]["cuenta"], "TV1")
        self.assertEqual(trabajo()["cuenta"], "TV1")


if __name__ == "__main__":
    unittest.main()
