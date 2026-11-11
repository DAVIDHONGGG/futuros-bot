"""Politica de lote comun a todos los ejecutores (EjecutorBase), candado y
marcas en el log que usa actualizar.ps1."""

import threading
import unittest

from tests.apoyo import EjecutorDePrueba, config_de_prueba, trabajo

import ejecutor
from ejecutor_falso import EjecutorFalso


class TestCuentaBloqueadaEnElLote(unittest.TestCase):
    """Si una orden de una cuenta no se confirma, no se coloca NINGUNA mas en
    esa cuenta en este lote. Las otras cuentas siguen."""

    def test_no_confirmada_bloquea_solo_esa_cuenta(self):
        ej = EjecutorDePrueba(dry_run=False, no_confirma={"TV1"})
        res = ej.colocar_lote([trabajo("TV1"), trabajo("TS1"), trabajo("TV1"),
                               trabajo("TS1")])
        self.assertEqual([t["cuenta"] for t in ej.intentadas], ["TV1", "TS1", "TS1"])
        self.assertIs(res[0].confirmado, False)
        self.assertTrue(res[2].omitida)
        self.assertIn("bloqueada", res[2].detalle)
        self.assertIs(res[3].confirmado, True)

    def test_un_fallo_tambien_bloquea_la_cuenta(self):
        ej = EjecutorDePrueba(falla={"TS1"})
        res = ej.colocar_lote([trabajo("TS1"), trabajo("TS1"), trabajo("TV1")])
        self.assertIn("fallo simulado", res[0].error)
        self.assertTrue(res[1].omitida)
        self.assertFalse(res[2].omitida)

    def test_el_bloqueo_dura_SOLO_ese_lote(self):
        ej = EjecutorDePrueba(dry_run=False, no_confirma={"TV1"})
        ej.colocar_lote([trabajo("TV1")])
        ej.no_confirma.clear()
        res = ej.colocar_lote([trabajo("TV1")])
        self.assertIs(res[0].confirmado, True)

    def test_hay_un_resultado_por_trabajo(self):
        ej = EjecutorDePrueba(abortar_en=2)
        trabajos = [trabajo("TV1"), trabajo("TS1"), trabajo("TV2"), trabajo("TV1")]
        res = ej.colocar_lote(trabajos)
        ejecutor.limpiar_aborto()
        self.assertEqual(len(res), len(trabajos))
        self.assertEqual([r.etiqueta[:2] for r in res], ["1.", "2.", "3.", "4."])


class TestCierres(unittest.TestCase):

    def tearDown(self):
        ejecutor.limpiar_aborto()

    def test_un_fallo_no_corta_las_demas(self):
        ej = EjecutorDePrueba(falla={"TS1"})
        self.assertEqual(ej.cerrar_cuentas(["TV1", "TS1", "TV2"]),
                         {"TV1": 2, "TS1": None, "TV2": 2})

    def test_un_aborto_si(self):
        ejecutor.solicitar_aborto()
        ej = EjecutorDePrueba()
        self.assertEqual(ej.cerrar_cuentas(["TV1", "TS1"]), {})
        self.assertEqual(ej.cerradas, [])


class TestCandadoYMarcas(unittest.TestCase):

    def test_libre_y_ocupado(self):
        self.assertFalse(ejecutor.hay_operacion_en_curso())
        frenar = threading.Event()
        ej = EjecutorDePrueba(frenar=frenar)
        hilo = threading.Thread(target=ej.colocar_lote, args=([trabajo()],))
        hilo.start()
        ej.en_curso.wait(5)
        try:
            self.assertTrue(ejecutor.hay_operacion_en_curso())
        finally:
            frenar.set()
            hilo.join(5)
        self.assertFalse(ejecutor.hay_operacion_en_curso())

    def test_dos_lotes_no_se_pisan(self):
        frenar = threading.Event()
        ej = EjecutorDePrueba(frenar=frenar)
        h1 = threading.Thread(target=ej.colocar_lote, args=([trabajo("TV1")],))
        h1.start()
        ej.en_curso.wait(5)
        h2 = threading.Thread(target=ej.colocar_lote, args=([trabajo("TS1")],))
        h2.start()
        h2.join(0.3)
        self.assertEqual(len(ej.intentadas), 1, "el segundo lote no espero al primero")
        frenar.set()
        h1.join(5)
        h2.join(5)
        self.assertEqual([t["cuenta"] for t in ej.intentadas], ["TV1", "TS1"])

    def test_marca_de_fin_aunque_falle_o_se_aborte(self):
        # actualizar.ps1 se niega a actualizar si hay un INICIO sin FIN. En
        # MT5 el fin solo se escribia en el camino feliz y una orden abortada
        # dejo la maquina "en vuelo" toda una tarde.
        for opciones in ({"falla": {"TV1"}}, {"abortar_en": 1, "aborta_antes_del_clic": True}):
            with self.subTest(**{k: str(v) for k, v in opciones.items()}):
                ej = EjecutorDePrueba(**opciones)
                with self.assertLogs("bot_futuros.ejecutor", level="INFO") as logs:
                    ej.colocar_lote([trabajo("TV1")])
                ejecutor.limpiar_aborto()
                textos = "\n".join(logs.output)
                self.assertIn(ejecutor.MARCA_INICIO, textos)
                self.assertIn(ejecutor.MARCA_FIN, textos)
                self.assertGreater(textos.rindex(ejecutor.MARCA_FIN),
                                   textos.rindex(ejecutor.MARCA_INICIO))

    def test_las_marcas_coinciden_con_actualizar_ps1(self):
        from pathlib import Path
        ps1 = (Path(__file__).resolve().parent.parent / "actualizar.ps1").read_text(
            encoding="utf-8")
        self.assertIn(f'"{ejecutor.MARCA_INICIO}"', ps1)
        self.assertIn(f'"{ejecutor.MARCA_FIN}"', ps1)


class TestEjecutorFalso(unittest.TestCase):

    def test_no_hace_clic_y_lo_dice(self):
        ej = EjecutorFalso(dry_run=False)
        with self.assertLogs("bot_futuros.ejecutor_falso", level="INFO") as logs:
            res = ej.colocar_lote([trabajo()])
        self.assertIsNone(res[0].confirmado)
        self.assertIn("EJECUTOR FALSO", logs.output[0])
        self.assertEqual(ej.cerrar_cuentas(["TV1"]), {"TV1": 0})

    def test_es_el_de_la_etapa_1(self):
        self.assertIsInstance(ejecutor.crear_ejecutor(config_de_prueba()), EjecutorFalso)

    def test_hereda_el_dry_run_de_la_config(self):
        self.assertTrue(ejecutor.crear_ejecutor(config_de_prueba()).dry_run)


if __name__ == "__main__":
    unittest.main()
