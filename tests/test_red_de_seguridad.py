"""La red de seguridad de tests/__init__.py tiene que estar activa SIEMPRE.

Medido: con `unittest discover -s tests` (sin -t) el __init__ del paquete no
se importa. Estos tests fallan si alguien rompe el mecanismo que lo garantiza
(cada archivo importa tests.apoyo).
"""

import re
import unittest
import urllib.request
from pathlib import Path

from tests.apoyo import TOKEN_PRUEBA  # noqa: F401  (activa la red)

import listener_futuros as lf
import tests

CARPETA = Path(__file__).parent


class TestRedActiva(unittest.TestCase):

    def test_el_paquete_se_importo(self):
        self.assertTrue(getattr(tests, "RED_ACTIVA", False))

    def test_no_se_puede_salir_a_la_red(self):
        with self.assertRaises(RuntimeError):
            urllib.request.urlopen("https://api.telegram.org")

    def test_programadas_va_a_un_temporal(self):
        self.assertTrue(str(lf.ARCHIVO_PROGRAMADAS).startswith(str(tests.DIR_TEMPORAL)))
        self.assertNotEqual(lf.ARCHIVO_PROGRAMADAS, lf.BASE_DIR / "programadas.json")

    def test_no_se_puede_lanzar_el_actualizador(self):
        with self.assertRaises(RuntimeError):
            lf.subprocess.Popen(["powershell.exe", "-File", "actualizar.ps1"])

    def test_la_config_es_la_neutra_y_no_la_de_esta_maquina(self):
        self.assertEqual(lf.CONFIG.machine_id, "PRUEBA")
        self.assertNotEqual(lf.CONFIG.base_dir, lf.BASE_DIR)

    def test_todo_archivo_de_test_importa_tests_apoyo(self):
        # Un test nuevo sin este import correria SIN la red si la suite se
        # lanza sin -t. Mejor que falle aca que en una maquina con cuentas.
        for archivo in sorted(CARPETA.glob("test_*.py")):
            with self.subTest(archivo=archivo.name):
                texto = archivo.read_text(encoding="utf-8")
                self.assertTrue(re.search(r"^from tests\.apoyo import", texto, re.M),
                                f"{archivo.name} no importa tests.apoyo")


if __name__ == "__main__":
    unittest.main()
