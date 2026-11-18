"""La cuenta de la orden por NUMERO completo, y varias firmas por van
(18/11/2026).

El usuario lleva un Excel con el numero de cada cuenta; su codigo arma las
ordenes. Si una cuenta muere y la reemplaza otra, manda el numero nuevo sin
tocar la PC. Y una van puede tener sesion con varias firmas a la vez.

  - "firmas" (config.json, flota): de que firma es un numero, por como
    empieza (LFE = Lucid...), y en que plataforma se opera. Topstep no tiene
    prefijo: se queda con lo que no sea de otra.
  - "logins" (config.local.json): con que firmas tiene sesion esta van, en
    que perfil y con que tope de contratos.

Lo que se prueba:
  - el numero de una cuenta configurada lleva a esa cuenta (y no la duplica);
  - un numero nuevo va al login de SU firma, con el perfil de ese login;
  - un numero de una firma sin login en esta van se rechaza, diciendo por que;
  - un alias mal escrito sigue siendo "cuenta desconocida";
  - firmas que se pisan, o logins mal escritos, se descartan con advertencia;
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

FIRMAS = {
    "lucid": {"plataforma": "tradovate", "cuentas_empiezan_con": "LFE"},
    "tradeify": {"plataforma": "tradovate", "cuentas_empiezan_con": "FTDFY"},
    "mff": {"plataforma": "tradovate", "cuentas_empiezan_con": "MFF"},
    "topstep": {"plataforma": "topstepx", "cuentas_empiezan_con": None},
}
CUENTAS = {
    "TV1": {"plataforma": "tradovate", "max_contratos": 3, "cuenta_plataforma": "LFE0508943329-0001"},
    "TV2": {"plataforma": "tradovate", "max_contratos": 1},
}
LOGINS = {
    "lucid": {"perfil": "perfiles/LUCID", "max_contratos": 2},
    "tradeify": {"perfil": "perfiles/TRADEIFY", "max_contratos": 4},
}


def cfg(logins=LOGINS, firmas=FIRMAS, cuentas=CUENTAS, flota=None, **extra):
    flota = copy.deepcopy(flota or FLOTA_PRUEBA)
    if firmas is not None:
        flota["firmas"] = copy.deepcopy(firmas)
    local = local_con(cuentas=cuentas, cuenta_por_defecto="TV1" if cuentas else None, **extra)
    if logins is not None:
        local["logins"] = copy.deepcopy(logins)
    return config_de_prueba(flota=flota, local=local)


class TestQueEsUnNumero(unittest.TestCase):

    def test_numeros(self):
        for n in ("668214-39660124", "LFE05089433290001", "FTDFYSLX50527729629", "12345"):
            self.assertTrue(NUMERO_DE_CUENTA.match(n), n)

    def test_alias_no(self):
        for a in ("TOP6", "TV1", "MFF12", "LUC1234", "ALL"):
            self.assertIsNone(NUMERO_DE_CUENTA.match(a), a)


class TestFirmaDelNumero(unittest.TestCase):

    def test_por_como_empieza(self):
        c = cfg()
        self.assertEqual(c.firma_de("LFE05089433290009"), "lucid")
        self.assertEqual(c.firma_de("ftdfyslx50527729629"), "tradeify")
        self.assertEqual(c.firma_de("MFFUSFBLDR700298009"), "mff")

    def test_lo_que_no_es_de_otra_es_topstep(self):
        self.assertEqual(cfg().firma_de("668214-39660124"), "topstep")

    def test_sin_firma_sin_prefijo_no_se_sabe(self):
        firmas = {k: v for k, v in FIRMAS.items() if k != "topstep"}
        self.assertIsNone(cfg(firmas=firmas).firma_de("668214-39660124"))


class TestResolver(unittest.TestCase):

    def test_numero_configurado_es_esa_cuenta(self):
        self.assertEqual(resolver_cuentas("lfe0508943329-0001", cfg()), ["TV1"])

    def test_alias_y_su_numero_cuentan_una_vez(self):
        self.assertEqual(resolver_cuentas("TV1,LFE0508943329-0001", cfg()), ["TV1"])

    def test_numero_nuevo_va_al_login_de_su_firma(self):
        c = cfg()
        self.assertEqual(resolver_cuentas("LFE05089433290009,FTDFYSLX50527729629", c),
                         ["LFE05089433290009", "FTDFYSLX50527729629"])
        lucid, tradeify = c.cuenta("LFE05089433290009"), c.cuenta("FTDFYSLX50527729629")
        self.assertEqual((lucid.plataforma, lucid.max_contratos, lucid.perfil.name),
                         ("tradovate", 2, "LUCID"))
        self.assertEqual((tradeify.max_contratos, tradeify.perfil.name), (4, "TRADEIFY"))
        self.assertEqual(lucid.cuenta_plataforma, "LFE05089433290009")
        self.assertIs(c.cuenta("lfe05089433290009"), lucid, "la misma cuenta, no otra")

    def test_sin_alias_configurados_alcanza_con_los_logins(self):
        c = cfg(cuentas={})
        self.assertEqual(resolver_cuentas("LFE05089433290009", c), ["LFE05089433290009"])

    def test_firma_sin_login_en_esta_van_se_rechaza_diciendo_por_que(self):
        with self.assertRaises(ValueError) as e:
            resolver_cuentas("MFFUSFBLDR700298009", cfg())
        self.assertIn("es de mff y esta van no tiene login de mff", str(e.exception))
        with self.assertRaises(ValueError) as e:
            resolver_cuentas("668214-39660124", cfg())
        self.assertIn("es de topstep", str(e.exception))

    def test_alias_mal_escrito_sigue_siendo_desconocido(self):
        with self.assertRaises(ValueError) as e:
            resolver_cuentas("TV9", cfg())
        self.assertIn("TV9: no es un alias configurado", str(e.exception))

    def test_all_son_solo_las_configuradas(self):
        c = cfg()
        c.cuenta("LFE05089433290009")       # una nueva ya usada no se suma a "all"
        self.assertEqual(sorted(resolver_cuentas("all", c)), ["TV1", "TV2"])

    def test_el_tope_de_contratos_del_login(self):
        c = cfg()
        with self.assertRaises(ValueError) as e:
            expandir_orden(parse_orden("MNQ buy 3 40 80 LFE05089433290009"), c)
        self.assertIn("maximo de esa cuenta (2)", str(e.exception))
        t = expandir_orden(parse_orden("MNQ buy 2 40 80 LFE05089433290009"), c)[0]
        self.assertEqual((t["cuenta"], t["plataforma"], t["contrato"]),
                         ("LFE05089433290009", "tradovate", "MNQZ6"))


class TestConfigInvalida(unittest.TestCase):

    def test_firmas_que_se_pisan_se_descarta_la_segunda(self):
        firmas = dict(FIRMAS, mffu={"plataforma": "tradovate", "cuentas_empiezan_con": "MFFU"})
        c = cfg(firmas=firmas)
        self.assertNotIn("mffu", c.firmas)
        self.assertTrue(any("se pisa con el de mff" in a for a in c.advertencias), c.advertencias)

    def test_una_sola_firma_sin_prefijo(self):
        firmas = dict(FIRMAS, otra={"plataforma": "topstepx", "cuentas_empiezan_con": None})
        c = cfg(firmas=firmas)
        self.assertNotIn("otra", c.firmas)
        self.assertTrue(any("puede haber una sola" in a for a in c.advertencias))

    def test_firma_con_plataforma_desconocida(self):
        c = cfg(firmas=dict(FIRMAS, rara={"plataforma": "nada", "cuentas_empiezan_con": "RR"}))
        self.assertNotIn("rara", c.firmas)

    def test_logins_mal_escritos_se_descartan(self):
        for login in ({"lucid": {"max_contratos": 2}},                              # sin perfil
                      {"lucid": {"perfil": "p", "max_contratos": 0}},
                      {"apex": {"perfil": "p", "max_contratos": 1}},               # firma desconocida
                      {"lucid": "perfiles/LUCID"}):
            with self.subTest(login=login):
                c = cfg(logins=login)
                self.assertEqual(c.logins, {})
                self.assertTrue(any("Login" in a and "descartado" in a for a in c.advertencias))
                self.assertIsNone(c.cuenta("LFE05089433290009"))

    def test_logins_es_clave_local_sin_advertencia_de_flota(self):
        self.assertFalse(any("FLOTA" in a for a in cfg().advertencias), cfg().advertencias)

    def test_cuenta_nueva_vieja_avisa_que_se_saque(self):
        c = cfg(cuenta_nueva={"plataforma": "tradovate", "max_contratos": 1, "perfil": "p"})
        self.assertTrue(any("'cuenta_nueva' ya no se usa" in a for a in c.advertencias))
        self.assertFalse(any("FLOTA" in a for a in c.advertencias))


class TestEjecutorConNumero(unittest.TestCase):

    def test_elige_y_verifica_la_cuenta_por_el_numero_en_su_login(self):
        flota = copy.deepcopy(FLOTA_PRUEBA)
        flota["plataformas"]["tradovate"]["pasos_seleccionar_cuenta"] = [
            {"accion": "click_texto", "texto": "{cuenta_plataforma}"},
            {"accion": "verificar", "selector": "#cuenta", "contiene": "{cuenta_ultimos4}"},
        ]
        c = cfg(flota=flota, dry_run=False)

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
        self.assertTrue(lanzado["user_data_dir"].endswith("LUCID"), "perfil del login de Lucid")

    def test_dos_firmas_abren_dos_navegadores(self):
        c = cfg(cuentas={})
        pw = PlaywrightFalso()
        ej = EjecutorWeb(c, fabrica=fabrica(pw), dir_diagnosticos=dir_temporal())
        self.addCleanup(ej.cerrar)
        self.addCleanup(ejecutor.limpiar_aborto)
        trabajos = expandir_orden(
            parse_orden("MNQ buy 1 40 80 LFE05089433290009,FTDFYSLX50527729629"), c)
        ej.colocar_lote(trabajos)
        perfiles = sorted(a[3]["user_data_dir"][-8:] for a in pw.acciones("lanzar"))
        self.assertEqual(len(perfiles), 2)
        self.assertTrue(perfiles[0].endswith("LUCID") or perfiles[1].endswith("LUCID"))
        self.assertTrue(any(p.endswith("TRADEIFY") for p in perfiles))

    def test_lo_de_siempre_con_alias_sigue_igual(self):
        c = cfg()
        self.assertEqual(expandir_orden(parse_orden("MNQ buy 1 40 80 TV1"), c)[0]["cuenta"], "TV1")
        self.assertEqual(trabajo()["cuenta"], "TV1")


class TestAbrirPerfilDeUnLogin(unittest.TestCase):

    def test_por_el_nombre_de_la_firma(self):
        import contextlib
        import io
        import abrir_perfil
        pw = PlaywrightFalso()
        c = cfg(cuentas={})
        with contextlib.redirect_stdout(io.StringIO()):
            codigo = abrir_perfil.main(["LUCID"], config=c, fabrica=fabrica(pw),
                                       corriendo=lambda: False)
        self.assertEqual(codigo, 0)
        self.assertTrue(pw.acciones("lanzar")[0][3]["user_data_dir"].endswith("LUCID"))


class TestFirmasRealesDeLaFlota(unittest.TestCase):

    def test_config_json(self):
        import json
        from pathlib import Path
        raiz = Path(__file__).resolve().parent.parent
        flota = json.loads((raiz / "config.json").read_text(encoding="utf-8"))
        c = config_de_prueba(flota=flota, local=local_con(cuentas={}, cuenta_por_defecto=None))
        self.assertEqual(set(c.firmas), {"lucid", "tradeify", "mff", "topstep"})
        self.assertFalse(any("Firma" in a for a in c.advertencias), c.advertencias)
        # Los numeros medidos en cada van van a su firma.
        self.assertEqual(c.firma_de("LFE05089433290001"), "lucid")       # FUTVAN4
        self.assertEqual(c.firma_de("FTDFYSLX50527729629"), "tradeify")  # FUTVAN8
        self.assertEqual(c.firma_de("MFFUSFBLDR700298009"), "mff")       # FUTVAN5
        self.assertEqual(c.firma_de("668214-39660124"), "topstep")       # FUTVAN7
        self.assertEqual(c.firmas["topstep"]["plataforma"], "topstepx")


if __name__ == "__main__":
    unittest.main()
