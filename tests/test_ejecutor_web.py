"""Ejecutor web con un Playwright FALSO: ningun test abre un navegador.

Lo que se prueba es lo que protege la plata: que todo pase en UN hilo, que
dry_run no haga clic, que el stop corte antes del clic, que una orden no
verificada no se de por buena y bloquee la cuenta en el lote.
"""

import asyncio
import copy
import threading
import unittest

from tests.apoyo import FLOTA_PRUEBA, config_de_prueba, dir_temporal, local_con, trabajo
from tests.navegador_falso import PlaywrightFalso, fabrica

import ejecutor
from ejecutor_web import ARGS_NO_ROBAR_FOCO, ARGS_SIN_FRENOS, EjecutorWeb


def llenar_al_clic(pagina, selector):
    """Simula una plataforma que ejecuta la orden: la cantidad cambia."""
    if selector in ("#comprar", "#vender"):
        actual = int(pagina.posiciones.get("MNQZ6", "0"))
        paso = 1 if selector == "#comprar" else -1
        pagina.posiciones["MNQZ6"] = str(actual + paso)


class BaseWeb(unittest.TestCase):

    def crear(self, dry_run=True, preparar=None, config=None):
        cfg = config or config_de_prueba(local=local_con(dry_run=dry_run))
        self.pw = PlaywrightFalso(preparar_pagina=preparar)
        self.diag = dir_temporal()
        self.ej = EjecutorWeb(cfg, fabrica=fabrica(self.pw), dir_diagnosticos=self.diag)
        self.addCleanup(self.ej.cerrar)
        self.addCleanup(ejecutor.limpiar_aborto)
        return self.ej


class TestHiloUnico(BaseWeb):
    """Playwright sync solo funciona en el hilo que lo creo, y to_thread usa
    un hilo distinto en cada llamada."""

    def test_crear_el_ejecutor_no_abre_nada(self):
        self.crear()
        self.assertIsNone(self.pw.hilo_que_arranco)
        self.assertFalse(self.ej._hilo.vivo())

    def test_todo_corre_en_un_solo_hilo_aunque_llamen_hilos_distintos(self):
        self.crear()
        llamadores = []

        def llamar():
            llamadores.append(threading.get_ident())
            self.ej.colocar_lote([trabajo("TV1")])
        for _ in range(3):
            h = threading.Thread(target=llamar)
            h.start()
            h.join(10)

        self.assertEqual(len(set(llamadores)), 3)
        self.assertEqual(self.pw.hilos(), {self.pw.hilo_que_arranco})
        self.assertNotIn(self.pw.hilo_que_arranco, llamadores)
        self.assertEqual(len(self.pw.acciones("lanzar")), 1, "relanzo el navegador")

    def test_igual_desde_asyncio_to_thread(self):
        self.crear()

        async def dos_llamadas():
            await asyncio.to_thread(self.ej.colocar_lote, [trabajo("TV1")])
            await asyncio.to_thread(self.ej.colocar_lote, [trabajo("TV1")])
        asyncio.run(dos_llamadas())
        self.assertEqual(len(self.pw.hilos()), 1)

    def test_la_operacion_cuenta_como_en_curso_mientras_corre(self):
        frenar, adentro = threading.Event(), threading.Event()

        def preparar(pagina):
            def esperar(sel, valor):
                adentro.set()
                frenar.wait(5)
            pagina.al_escribir = esperar
        self.crear(preparar=preparar)
        h = threading.Thread(target=self.ej.colocar_lote, args=([trabajo("TV1")],))
        h.start()
        adentro.wait(5)
        try:
            self.assertTrue(ejecutor.hay_operacion_en_curso())
        finally:
            frenar.set()
            h.join(10)
        self.assertFalse(ejecutor.hay_operacion_en_curso())

    def test_cerrar_cierra_los_navegadores_y_el_hilo(self):
        self.crear()
        self.ej.colocar_lote([trabajo("TV1")])
        self.ej.cerrar()
        self.assertTrue(self.pw.contextos[0].cerrado)
        self.assertTrue(self.pw.detenido)
        self.assertFalse(self.ej._hilo.vivo())


class TestDryRun(BaseWeb):

    def test_llena_todo_y_NO_hace_clic_en_comprar(self):
        self.crear(dry_run=True, preparar=lambda p: setattr(p, "efecto_clic", llenar_al_clic))
        with self.assertLogs("bot_futuros.ejecutor_web", level="INFO") as logs:
            res = self.ej.colocar_lote([trabajo("TV1", contratos=2)])

        escritos = [(r[2], r[3]) for r in self.pw.acciones("escribir")]
        self.assertEqual(escritos, [("#simbolo", "MNQZ6"), ("#cantidad", "2"),
                                    ("#sl", "40"), ("#tp", "80")])
        self.assertNotIn("#comprar", self.pw.clics())
        self.assertNotIn("#vender", self.pw.clics())
        self.assertIsNone(res[0].confirmado)
        self.assertIn("DRY_RUN activo: NO se hizo clic en Comprar", "\n".join(logs.output))

    def test_guarda_la_captura_en_diagnosticos(self):
        self.crear(dry_run=True)
        self.ej.colocar_lote([trabajo("TV1")])
        capturas = list(self.diag.glob("*-TV1-dryrun.png"))
        self.assertEqual(len(capturas), 1)

    def test_limpia_el_ticket_al_final(self):
        # Si no, la orden real siguiente heredaria los valores.
        self.crear(dry_run=True)
        self.ej.colocar_lote([trabajo("TV1")])
        ultima = self.pw.registro[-1]
        self.assertEqual((ultima[1], ultima[3]), ("tecla", "Escape"))

    def test_igual_lee_el_panel_de_posiciones(self):
        # El dry_run sirve para validar TODO el flujo, panel incluido.
        self.crear(dry_run=True, preparar=lambda p: setattr(p, "sin_panel", True))
        res = self.ej.colocar_lote([trabajo("TV1")])
        self.assertIn("panel de posiciones", res[0].error)


class TestEnVivo(BaseWeb):

    def test_compra_y_confirma(self):
        self.crear(dry_run=False, preparar=lambda p: setattr(p, "efecto_clic", llenar_al_clic))
        res = self.ej.colocar_lote([trabajo("TV1")])
        self.assertEqual(self.pw.clics().count("#comprar"), 1)
        self.assertIs(res[0].confirmado, True)
        self.assertIn("[] -> ['1']", res[0].detalle)

    def test_vender_usa_el_boton_de_vender(self):
        self.crear(dry_run=False, preparar=lambda p: setattr(p, "efecto_clic", llenar_al_clic))
        res = self.ej.colocar_lote([trabajo("TV1", accion="sell")])
        self.assertIn("#vender", self.pw.clics())
        self.assertNotIn("#comprar", self.pw.clics())
        self.assertIs(res[0].confirmado, True)

    def test_el_PnL_que_cambia_solo_NO_confirma(self):
        # La fila cambia de texto en cada lectura (P&L), la cantidad no.
        def preparar(p):
            p.posiciones = {"MNQZ6": "1"}
        self.crear(dry_run=False, preparar=preparar)
        res = self.ej.colocar_lote([trabajo("TV1")])
        self.assertIs(res[0].confirmado, False)

    def test_no_confirmada_bloquea_esa_cuenta_en_el_lote(self):
        cfg = config_de_prueba(local=local_con(dry_run=False, cuentas={
            "TV1": {"plataforma": "tradovate", "max_contratos": 3},
            "TV2": {"plataforma": "tradovate", "max_contratos": 3, "perfil": "perfiles/otro"},
        }))
        llenar_solo_en_el_segundo = []

        def preparar(p):
            llenar_solo_en_el_segundo.append(p)
            if len(llenar_solo_en_el_segundo) == 2:
                p.efecto_clic = llenar_al_clic
        self.crear(config=cfg, preparar=preparar)
        res = self.ej.colocar_lote([trabajo("TV1"), trabajo("TV2"), trabajo("TV1")])

        self.assertIs(res[0].confirmado, False)
        self.assertIs(res[1].confirmado, True)
        self.assertTrue(res[2].omitida, "se siguio operando en la cuenta no confirmada")
        self.assertEqual(self.pw.clics().count("#comprar"), 2)
        self.assertTrue(list(self.diag.glob("*-TV1-no-confirmada.png")))

    def test_sin_panel_legible_NO_hace_clic(self):
        self.crear(dry_run=False, preparar=lambda p: setattr(p, "sin_panel", True))
        res = self.ej.colocar_lote([trabajo("TV1")])
        self.assertNotIn("#comprar", self.pw.clics())
        self.assertIn("NO se hace clic", res[0].error)

    def test_un_paso_que_falla_no_hace_clic_y_deja_foto(self):
        self.crear(dry_run=False, preparar=lambda p: p.falla_en.add("#cantidad"))
        res = self.ej.colocar_lote([trabajo("TV1"), trabajo("TV1")])
        self.assertNotIn("#comprar", self.pw.clics())
        self.assertIn("Timeout", res[0].error)
        self.assertTrue(res[1].omitida)
        self.assertTrue(list(self.diag.glob("*-TV1-error.png")))

    def test_si_el_clic_falla_es_error(self):
        self.crear(dry_run=False, preparar=lambda p: p.falla_al_clic.add("#comprar"))
        res = self.ej.colocar_lote([trabajo("TV1"), trabajo("TV1")])
        self.assertIn("no se pudo hacer clic", res[0].error)
        self.assertTrue(res[1].omitida)
        self.assertTrue(list(self.diag.glob("*-TV1-clic-fallo.png")))

    def test_boton_que_no_aparece_no_se_clickea(self):
        self.crear(dry_run=False, preparar=lambda p: p.falla_en.add("#comprar"))
        res = self.ej.colocar_lote([trabajo("TV1")])
        self.assertNotIn("#comprar", self.pw.clics())
        self.assertIn("Timeout", res[0].error)

    def test_si_falla_la_lectura_DESPUES_del_clic_no_es_confirmada(self):
        def preparar(p):
            def clic(pagina, sel):
                if sel == "#comprar":
                    pagina.sin_panel = True
            p.efecto_clic = clic
        self.crear(dry_run=False, preparar=preparar)
        res = self.ej.colocar_lote([trabajo("TV1")])
        self.assertIs(res[0].confirmado, False)
        self.assertIn("se hizo clic pero no se pudo verificar", res[0].detalle)


class TestStop(BaseWeb):

    def test_un_stop_mientras_se_llena_corta_ANTES_del_clic(self):
        def preparar(p):
            p.efecto_clic = llenar_al_clic

            def pedir_stop(sel, valor):
                if sel == "#sl":
                    ejecutor.solicitar_aborto()
            p.al_escribir = pedir_stop
        self.crear(dry_run=False, preparar=preparar)
        res = self.ej.colocar_lote([trabajo("TV1"), trabajo("TV1")])

        self.assertNotIn("#comprar", self.pw.clics())
        self.assertNotIn(("#tp", "80"), [(r[2], r[3]) for r in self.pw.acciones("escribir")])
        self.assertTrue(all(r.omitida for r in res))
        self.assertIn("No se hizo el clic final", res[0].detalle)
        self.assertTrue(list(self.diag.glob("*-TV1-aborto.png")))

    def test_el_stop_justo_antes_del_clic(self):
        # El ultimo punto de corte: despues de llenar todo.
        def preparar(p):
            def pedir_stop(sel, valor):
                if sel == "#tp":
                    ejecutor.solicitar_aborto()
            p.al_escribir = pedir_stop
        self.crear(dry_run=False, preparar=preparar)
        res = self.ej.colocar_lote([trabajo("TV1")])
        self.assertNotIn("#comprar", self.pw.clics())
        self.assertIn("antes del clic de Comprar", res[0].detalle)

    def test_stop_antes_del_clic_de_confirmar(self):
        flota = copy.deepcopy(FLOTA_PRUEBA)
        flota["plataformas"]["tradovate"]["pasos_confirmar"] = [
            {"accion": "click", "selector": "#confirmar"}]
        cfg = config_de_prueba(flota=flota, local=local_con(dry_run=False))

        def preparar(p):
            def clic(pagina, sel):
                if sel == "#comprar":
                    ejecutor.solicitar_aborto()   # llega entre Comprar y Confirmar
            p.efecto_clic = clic
        self.crear(config=cfg, preparar=preparar)
        res = self.ej.colocar_lote([trabajo("TV1")])
        self.assertIn("#comprar", self.pw.clics())
        self.assertNotIn("#confirmar", self.pw.clics())
        self.assertTrue(res[0].omitida)


class TestPlataformaYSesion(BaseWeb):

    def test_plataforma_con_TODO_no_abre_ni_opera(self):
        self.crear(dry_run=False)
        res = self.ej.colocar_lote([dict(trabajo("TS1"), plataforma="topstepx")])
        self.assertIn("plataforma sin configurar", res[0].error)
        self.assertEqual(self.pw.acciones("lanzar"), [])

    def test_sin_sesion_pide_el_login_a_mano(self):
        self.crear(dry_run=False, preparar=lambda p: setattr(p, "sin_sesion", True))
        res = self.ej.colocar_lote([trabajo("TV1")])
        self.assertIn("abrir_perfil.py TV1", res[0].error)
        self.assertEqual(self.pw.acciones("escribir"), [])
        self.assertTrue(list(self.diag.glob("*-TV1-sin-sesion.png")))

    def test_abre_la_url_una_sola_vez(self):
        self.crear()
        self.ej.colocar_lote([trabajo("TV1"), trabajo("TV1")])
        self.assertEqual(len(self.pw.acciones("goto")), 1)

    def test_dos_cuentas_con_el_mismo_perfil_comparten_navegador(self):
        cfg = config_de_prueba(local=local_con(cuentas={
            "TV1": {"plataforma": "tradovate", "max_contratos": 3, "perfil": "perfiles/comun"},
            "TV2": {"plataforma": "tradovate", "max_contratos": 3, "perfil": "perfiles/comun"},
        }))
        self.crear(config=cfg)
        self.ej.colocar_lote([trabajo("TV1"), trabajo("TV2")])
        self.assertEqual(len(self.pw.acciones("lanzar")), 1)

    def test_si_cierran_la_ventana_la_reabre(self):
        self.crear()
        self.ej.colocar_lote([trabajo("TV1")])
        self.pw.contextos[0].close()
        self.ej.colocar_lote([trabajo("TV1")])
        self.assertEqual(len(self.pw.acciones("lanzar")), 2)


class TestVentana(BaseWeb):

    def _opciones_de_lanzamiento(self, **local):
        cfg = config_de_prueba(local=local_con(**local))
        self.crear(config=cfg)
        self.ej.colocar_lote([trabajo("TV1")])
        return self.pw.acciones("lanzar")[0][3]

    def test_no_robar_foco_abre_fuera_de_pantalla_y_sin_frenos(self):
        opciones = self._opciones_de_lanzamiento()
        for arg in ARGS_NO_ROBAR_FOCO + ARGS_SIN_FRENOS:
            self.assertIn(arg, opciones["args"])
        self.assertFalse(opciones["headless"])

    def test_se_puede_apagar_por_maquina(self):
        opciones = self._opciones_de_lanzamiento(no_robar_foco=False)
        for arg in ARGS_NO_ROBAR_FOCO:
            self.assertNotIn(arg, opciones["args"])
        for arg in ARGS_SIN_FRENOS:
            self.assertIn(arg, opciones["args"])

    def test_usa_el_perfil_de_la_cuenta(self):
        opciones = self._opciones_de_lanzamiento()
        self.assertTrue(opciones["user_data_dir"].endswith("perfiles\\TV1")
                        or opciones["user_data_dir"].endswith("perfiles/TV1"))

    def test_nunca_trae_la_ventana_al_frente(self):
        # PaginaFalsa.bring_to_front levanta: si se llamara, la orden fallaria.
        self.crear(dry_run=False, preparar=lambda p: setattr(p, "efecto_clic", llenar_al_clic))
        self.assertIs(self.ej.colocar_lote([trabajo("TV1")])[0].confirmado, True)


class TestDesdeElListener(unittest.TestCase):

    def test_crear_ejecutor_da_el_web(self):
        self.assertIsInstance(ejecutor.crear_ejecutor(config_de_prueba()), EjecutorWeb)

    def test_la_red_de_seguridad_impide_un_navegador_real(self):
        ej = EjecutorWeb(config_de_prueba(), dir_diagnosticos=dir_temporal())
        self.addCleanup(ej.cerrar)
        res = ej.colocar_lote([trabajo("TV1")])
        self.assertIn("navegador REAL", res[0].error)

    def test_salud_no_toca_el_navegador(self):
        ej = EjecutorWeb(config_de_prueba())
        self.assertIn("ejecutor web", ej.salud()[0])
        self.assertFalse(ej._hilo.vivo())

    def test_los_cierres_todavia_no(self):
        ej = EjecutorWeb(config_de_prueba())
        self.assertEqual(ej.cerrar_cuentas(["TV1"]), {"TV1": None})


if __name__ == "__main__":
    unittest.main()
