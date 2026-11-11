"""Config efectiva: precedencia, origen de cada valor, y la trampa del local
que pisa a la flota."""

import json
import unittest
from pathlib import Path

from tests.apoyo import (FLOTA_PRUEBA, LOCAL_PRUEBA, TOKEN_PRUEBA, config_de_prueba,
                         dir_temporal, local_con)

from config import CLAVES_LOCALES, Config, ConfigIlegible, imprimir_efectiva

RAIZ = Path(__file__).resolve().parent.parent


class TestDryRun(unittest.TestCase):
    """dry_run es la proteccion de fondo: en la duda, en seco."""

    def test_sin_valor_en_ningun_lado_es_true(self):
        # En MT5 caia a False: una maquina sin config quedaba EN VIVO.
        flota = dict(FLOTA_PRUEBA)
        flota.pop("dry_run")
        c = config_de_prueba(flota=flota, local=local_con(dry_run=None))
        self.assertTrue(c.dry_run)

    def test_el_local_lo_puede_apagar(self):
        c = config_de_prueba(local=local_con(dry_run=False))
        self.assertFalse(c.dry_run)
        self.assertEqual(c.origen["dry_run"], "config.local.json")

    def test_un_valor_raro_NO_lo_apaga(self):
        for raro in ("", "talvez", "fals", None, "  "):
            with self.subTest(valor=raro):
                local = local_con()
                local["dry_run"] = raro
                c = config_de_prueba(local=local)
                self.assertTrue(c.dry_run, f"dry_run={raro!r} dejo la maquina EN VIVO")

    def test_false_como_texto_si_lo_apaga(self):
        for texto in ("false", "False", "0", "no", "off"):
            with self.subTest(valor=texto):
                c = config_de_prueba(local=local_con(dry_run=texto))
                self.assertFalse(c.dry_run)

    def test_la_variable_de_entorno_gana(self):
        c = config_de_prueba(local=local_con(dry_run=False),
                             entorno={"FUTBOT_DRY_RUN": "true"})
        self.assertTrue(c.dry_run)
        self.assertEqual(c.origen["dry_run"], "FUTBOT_DRY_RUN")


class TestPrecedenciaYOrigen(unittest.TestCase):

    def test_local_pisa_a_flota_y_entorno_pisa_a_local(self):
        c = config_de_prueba(entorno={"FUTBOT_PREFIJO_TELEGRAM": "fut9"})
        self.assertEqual(c.prefijo_telegram, "FUT9")
        self.assertEqual(c.origen["prefijo_telegram"], "FUTBOT_PREFIJO_TELEGRAM")

    def test_el_token_de_MT5_en_el_entorno_NO_se_toma(self):
        # Si una PC tiene TELEGRAM_BOT_TOKEN puesto para el bot de MT5, este
        # bot no puede tomarlo: se robarian los mensajes.
        c = config_de_prueba(entorno={"TELEGRAM_BOT_TOKEN": "999:" + "B" * 35})
        self.assertEqual(c.telegram_bot_token, TOKEN_PRUEBA)

    def test_los_autorizados_se_SUMAN(self):
        c = config_de_prueba(local=local_con(telegram_allowed_user_id=[222]))
        self.assertEqual(c.telegram_allowed_user_ids, frozenset({111, 222}))
        self.assertIn("config.json", c.origen["telegram_allowed_user_id"])
        self.assertIn("config.local.json", c.origen["telegram_allowed_user_id"])

    def test_los_comentarios_no_son_valores(self):
        local = local_con()
        local["_comentario"] = "hola"
        c = config_de_prueba(local=local)
        self.assertNotIn("_comentario", c.valores)
        self.assertEqual(c.advertencias, [])

    def test_efectiva_no_muestra_el_token(self):
        c = config_de_prueba()
        texto = repr(c.efectiva())
        self.assertNotIn(TOKEN_PRUEBA, texto)
        self.assertIn(TOKEN_PRUEBA[-6:], texto)

    def test_imprimir_efectiva_dice_de_donde_sale_cada_valor(self):
        import io
        salida = io.StringIO()
        imprimir_efectiva(config_de_prueba(local=local_con(dry_run=False)), salida)
        linea = [l for l in salida.getvalue().splitlines() if "dry_run" in l][0]
        self.assertIn("False", linea)
        self.assertIn("config.local.json", linea)


class TestLaTrampaDelLocal(unittest.TestCase):
    """config.local.json PISA a config.json. Una clave de flota copiada al
    local deja a esa maquina sin los cambios de la flota. En MT5 mordio dos
    veces sin un solo aviso: aca tiene que avisar."""

    def test_una_clave_de_flota_en_el_local_se_avisa(self):
        local = local_con()
        local["plataformas"] = {"tradovate": {"simbolos": {"MNQ": "MNQH7"}}}
        c = config_de_prueba(local=local)
        self.assertTrue(any("plataformas" in a and "FLOTA" in a for a in c.advertencias),
                        c.advertencias)

    def test_dice_si_ademas_el_valor_es_distinto(self):
        local = local_con()
        local["telegram_chat_errores"] = -100
        c = config_de_prueba(local=local)
        self.assertTrue(any("DISTINTO" in a for a in c.advertencias), c.advertencias)

    def test_las_claves_locales_no_se_avisan(self):
        self.assertEqual(config_de_prueba().advertencias, [])

    def test_config_example_solo_trae_claves_locales(self):
        # La plantilla es lo que se copia en cada maquina nueva: si trae una
        # clave de flota, termina sombreada en TODA la flota.
        ejemplo = json.loads((RAIZ / "config.example.json").read_text(encoding="utf-8"))
        de_flota = [k for k in ejemplo if not k.startswith("_") and k not in CLAVES_LOCALES]
        self.assertEqual(de_flota, [])

    def test_el_config_json_del_repo_esta_en_seco_y_sin_secretos(self):
        flota = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))
        self.assertIs(flota.get("dry_run"), True)
        for clave in ("telegram_bot_token", "prefijo_telegram", "cuentas", "machine_id"):
            self.assertNotIn(clave, flota)

    def test_el_config_json_del_repo_se_carga_sin_advertencias(self):
        d = dir_temporal()
        (d / "config.json").write_text((RAIZ / "config.json").read_text(encoding="utf-8"),
                                       encoding="utf-8")
        c = Config(base_dir=d, entorno={})
        self.assertTrue(c.dry_run)
        self.assertEqual(c.advertencias, [])
        self.assertEqual(set(c.plataformas), {"tradovate", "topstepx"})


class TestArchivosIlegibles(unittest.TestCase):

    def test_un_json_roto_aborta(self):
        d = dir_temporal()
        (d / "config.local.json").write_text("{roto", encoding="utf-8")
        with self.assertRaises(ConfigIlegible):
            Config(base_dir=d, entorno={})

    def test_el_BOM_se_tolera(self):
        d = dir_temporal()
        (d / "config.local.json").write_bytes(
            b"\xef\xbb\xbf" + json.dumps({"dry_run": False}).encode("utf-8"))
        self.assertFalse(Config(base_dir=d, entorno={}).dry_run)

    def test_sin_archivos_queda_en_seco(self):
        c = Config(base_dir=dir_temporal(), entorno={})
        self.assertTrue(c.dry_run)
        self.assertEqual(c.cuentas, {})


class TestCuentas(unittest.TestCase):

    def test_carga_las_validas_con_perfil_por_defecto(self):
        c = config_de_prueba()
        self.assertEqual(set(c.cuentas), {"TV1", "TV2", "TS1"})
        tv1 = c.cuentas["TV1"]
        self.assertEqual(tv1.plataforma, "tradovate")
        self.assertEqual(tv1.max_contratos, 3)
        self.assertEqual(tv1.perfil, c.base_dir / "perfiles" / "TV1")

    def test_el_alias_no_distingue_mayusculas(self):
        c = config_de_prueba(local=local_con(cuentas={"ts1": {"plataforma": "TopstepX",
                                                              "max_contratos": 1}}))
        self.assertIn("TS1", c.cuentas)
        self.assertEqual(c.cuentas["TS1"].plataforma, "topstepx")

    def test_descarta_las_invalidas_y_avisa(self):
        malas = {
            "OK1": {"plataforma": "tradovate", "max_contratos": 1},
            "ALL": {"plataforma": "tradovate", "max_contratos": 1},       # reservada
            "CON ESPACIO": {"plataforma": "tradovate", "max_contratos": 1},
            "X:1": {"plataforma": "tradovate", "max_contratos": 1},       # parece hora
            "NOPLAT": {"plataforma": "ninjatrader", "max_contratos": 1},
            "CERO": {"plataforma": "tradovate", "max_contratos": 0},
            "DECIMAL": {"plataforma": "tradovate", "max_contratos": 1.5},
            "TEXTO": {"plataforma": "tradovate", "max_contratos": "2"},
            "BOOL": {"plataforma": "tradovate", "max_contratos": True},
        }
        c = config_de_prueba(local=local_con(cuentas=malas, cuenta_por_defecto=None))
        self.assertEqual(set(c.cuentas), {"OK1"})
        self.assertEqual(len(c.advertencias), len(malas) - 1, c.advertencias)

    def test_cuenta_por_defecto(self):
        self.assertEqual(config_de_prueba().cuenta_por_defecto, "TV1")
        # Con una sola cuenta, es esa.
        una = {"TS1": {"plataforma": "topstepx", "max_contratos": 1}}
        self.assertEqual(config_de_prueba(local=local_con(
            cuentas=una, cuenta_por_defecto=None)).cuenta_por_defecto, "TS1")
        # Con varias y sin configurar, ninguna: no se adivina.
        self.assertIsNone(config_de_prueba(local=local_con(
            cuenta_por_defecto=None)).cuenta_por_defecto)
        # Configurada pero inexistente, ninguna.
        self.assertIsNone(config_de_prueba(local=local_con(
            cuenta_por_defecto="NOEXISTE")).cuenta_por_defecto)


class TestContrato(unittest.TestCase):

    def setUp(self):
        self.c = config_de_prueba()

    def test_traduce_la_raiz(self):
        self.assertEqual(self.c.contrato("tradovate", "mnq"), "MNQZ6")

    def test_simbolo_desconocido(self):
        with self.assertRaises(ValueError) as e:
            self.c.contrato("tradovate", "ES")
        self.assertIn("no configurado", str(e.exception))

    def test_simbolo_con_TODO_no_se_opera(self):
        with self.assertRaises(ValueError) as e:
            self.c.contrato("topstepx", "MES")
        self.assertIn("sin contrato", str(e.exception))

    def test_acepta_la_forma_con_vencimiento(self):
        flota = json.loads(json.dumps(FLOTA_PRUEBA))
        flota["plataformas"]["tradovate"]["simbolos"]["MNQ"] = {
            "contrato": "MNQZ6", "vence": "2026-12-18"}
        c = config_de_prueba(flota=flota)
        self.assertEqual(c.contrato("tradovate", "MNQ"), "MNQZ6")


class TestUsuarios(unittest.TestCase):

    def test_formas_aceptadas(self):
        for valor, esperado in [(7, {111, 7}), ([7, 8], {111, 7, 8}),
                                ("7,8", {111, 7, 8}), ([7, "x", 8], {111, 7, 8}),
                                (0, {111})]:
            with self.subTest(valor=valor):
                c = config_de_prueba(local=local_con(telegram_allowed_user_id=valor))
                self.assertEqual(c.telegram_allowed_user_ids, frozenset(esperado))

    def test_sin_nadie(self):
        flota = dict(FLOTA_PRUEBA, telegram_allowed_user_id=[])
        c = config_de_prueba(flota=flota)
        self.assertEqual(c.telegram_allowed_user_ids, frozenset())
        self.assertEqual(c.telegram_allowed_user_id, 0)


if __name__ == "__main__":
    unittest.main()
