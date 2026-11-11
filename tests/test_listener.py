"""El listener de punta a punta con el ejecutor de prueba: respuestas,
cierres, update, mensajes viejos y el arranque (main)."""

import threading
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.apoyo import (FLOTA_PRUEBA, TOKEN_PRUEBA, ListenerAislado, config_de_prueba,
                         contexto_falso, local_con, update_falso)

import ejecutor
import listener_futuros as lf
from ejecutor import ResultadoOrden


class TestOrdenes(ListenerAislado, unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.aislar()

    async def test_sin_cuenta_va_a_la_por_defecto(self):
        bot = await self.mandar("FUT1 MNQ buy 2 40 80")
        self.assertEqual(self.ej.colocadas[0]["cuenta"], "TV1")
        self.assertIn("Recibida(s) 1 orden(es). Procesando (DRY_RUN)", bot.textos())

    async def test_all_coloca_en_todas_las_cuentas(self):
        # TV2 admite 1 contrato: la orden de 1 entra en las tres.
        await self.mandar("FUT1 MNQ sell 1 40 80 all")
        self.assertEqual(sorted(t["cuenta"] for t in self.ej.colocadas), ["TS1", "TV1", "TV2"])

    async def test_decimales_responde_error_y_no_coloca(self):
        bot = await self.mandar("FUT1 MNQ buy 1.5 40 80")
        self.assertEqual(self.ej.intentadas, [])
        self.assertIn("CONTRATOS", bot.textos())
        self.assertIn("No se coloco ni programo nada", bot.textos())

    async def test_varias_lineas_con_una_mala_no_coloca_ninguna(self):
        bot = await self.mandar("FUT1 MNQ buy 1 40 80 TV1\nFUT1 MNQ buy 1 40 TV1")
        self.assertEqual(self.ej.intentadas, [])
        self.assertIn("linea 2", bot.textos())

    async def test_supera_el_maximo_no_coloca_en_ninguna(self):
        bot = await self.mandar("FUT1 MNQ buy 2 40 80 TV1,TV2")
        self.assertEqual(self.ej.intentadas, [])
        self.assertIn("TV2", bot.textos())

    async def test_simbolo_sin_configurar(self):
        bot = await self.mandar("FUT1 MES buy 1 40 80 TS1")
        self.assertEqual(self.ej.intentadas, [])
        self.assertIn("sin contrato en topstepx", bot.textos())

    async def test_el_resumen_dice_lo_omitido_por_cuenta_bloqueada(self):
        self.aislar(dry_run=False, no_confirma={"TV1"})
        bot = await self.mandar("FUT1 MNQ buy 1 40 80 TV1\nFUT1 MNQ buy 1 40 80 TS1\n"
                                "FUT1 MES buy 1 40 80 TV1")
        textos = bot.textos()
        self.assertIn("Listo (EN VIVO)", textos)
        self.assertIn("1. MNQ buy 1 @ TV1: NO confirmada", textos)
        self.assertIn("2. MNQ buy 1 @ TS1: confirmada", textos)
        self.assertIn("3. MES buy 1 @ TV1: NO se intento (cuenta bloqueada", textos)


class TestCierres(ListenerAislado, unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.aislar()

    async def test_close_a_secas_cierra_la_por_defecto(self):
        bot = await self.mandar("FUT1 close")
        self.assertEqual(self.ej.cerradas, ["TV1"])
        self.assertIn("TV1: 2 posicion(es)", bot.textos())

    async def test_close_cuenta_en_los_dos_ordenes(self):
        await self.mandar("FUT1 close TS1")
        await self.mandar("FUT1 TV2 close")
        self.assertEqual(self.ej.cerradas, ["TS1", "TV2"])

    async def test_close_all(self):
        await self.mandar("FUT1 close all accounts")
        self.assertEqual(sorted(self.ej.cerradas), ["TS1", "TV1", "TV2"])

    async def test_cuenta_desconocida(self):
        bot = await self.mandar("FUT1 close XX9")
        self.assertEqual(self.ej.cerradas, [])
        self.assertIn("desconocida", bot.textos())

    async def test_un_fallo_se_informa(self):
        self.aislar(falla={"TS1"})
        bot = await self.mandar("FUT1 close all")
        self.assertIn("TS1: ERROR", bot.textos())

    async def test_sin_defecto_close_a_secas_es_error(self):
        self.aislar(config_de_prueba(local=local_con(cuenta_por_defecto=None)))
        bot = await self.mandar("FUT1 close")
        self.assertEqual(self.ej.cerradas, [])
        self.assertIn("Indica la cuenta", bot.textos())


class TestAutorizacionYMensajesViejos(ListenerAislado, unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.aislar()

    async def test_sin_autorizados_le_dice_su_id(self):
        self.aislar(config_de_prueba(flota=dict(FLOTA_PRUEBA, telegram_allowed_user_id=[])))
        upd = update_falso("hola", user_id=555)
        await lf.on_message(upd, contexto_falso())
        upd.message.reply_text.assert_awaited_once()
        self.assertIn("555", upd.message.reply_text.call_args.args[0])

    async def test_un_mensaje_viejo_para_mi_se_descarta_y_avisa(self):
        bot = await self.mandar("FUT1 MNQ buy 1 40 80", hace=timedelta(hours=3))
        self.assertEqual(self.ej.intentadas, [])
        self.assertIn("Descartado", bot.textos())

    async def test_un_mensaje_viejo_ajeno_se_descarta_callado(self):
        bot = await self.mandar("FUT2 MNQ buy 1 40 80", hace=timedelta(hours=3))
        self.assertEqual(bot.enviados, [])

    def test_antiguedad_usa_utc(self):
        upd = SimpleNamespace(message=SimpleNamespace(
            date=datetime.now(timezone.utc) - timedelta(seconds=5)))
        self.assertLess(abs(lf._antiguedad_del_mensaje(upd).total_seconds() - 5), 60)

    def test_antiguedad_sin_fecha_o_rota_no_bloquea(self):
        self.assertIsNone(lf._antiguedad_del_mensaje(
            SimpleNamespace(message=SimpleNamespace(date=None))))
        self.assertIsNone(lf._antiguedad_del_mensaje(
            SimpleNamespace(message=SimpleNamespace(date="no es fecha"))))

    def test_el_limite_es_la_gracia(self):
        self.assertEqual(lf.EDAD_MAXIMA_MENSAJE, lf.GRACIA_HORA_PASADA)

    def test_quien(self):
        u = SimpleNamespace(effective_user=SimpleNamespace(id=7, username=None,
                                                           full_name="Ana", first_name=None))
        self.assertEqual(lf._quien(u), "Ana (id 7)")
        self.assertEqual(lf._quien(SimpleNamespace(effective_user=None)), "desconocido")


class TestUpdate(ListenerAislado, unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.aislar()

    def test_regex(self):
        for texto in ("update", "ACTUALIZAR", "  Update "):
            self.assertTrue(lf.REGEX_UPDATE.match(texto))
        for texto in ("update ya", "updater", "MNQ buy 1 2 3"):
            self.assertIsNone(lf.REGEX_UPDATE.match(texto))

    async def test_lanza_el_script(self):
        with patch.object(lf.subprocess, "Popen") as popen:
            bot = await self.mandar("FUT1 update")
        popen.assert_called_once()
        comando = popen.call_args.args[0]
        self.assertIn("actualizar.ps1", comando[comando.index("-File") + 1])
        self.assertEqual(comando[comando.index("-ChatId") + 1], "42")
        self.assertIn("Actualizando", bot.textos())

    async def test_se_niega_con_una_operacion_en_curso(self):
        tomado, soltar = threading.Event(), threading.Event()

        def ocupar():
            with ejecutor.operacion("prueba"):
                tomado.set()
                soltar.wait(5)
        hilo = threading.Thread(target=ocupar)
        hilo.start()
        tomado.wait(5)
        try:
            with patch.object(lf.subprocess, "Popen") as popen:
                bot = await self.mandar("FUT1 update")
        finally:
            soltar.set()
            hilo.join(5)
        popen.assert_not_called()
        self.assertIn("operacion en curso", bot.textos())

    async def test_se_niega_con_algo_programado_cerca(self):
        lf._escribir_programadas([{"cuando": (datetime.now() + timedelta(minutes=3))
                                   .isoformat(), "etiqueta": "x"}])
        with patch.object(lf.subprocess, "Popen") as popen:
            bot = await self.mandar("FUT1 update")
        popen.assert_not_called()
        self.assertIn("NO se actualiza", bot.textos())


class TestEstado(unittest.TestCase):

    def test_textos(self):
        casos = [
            (ResultadoOrden("e", "TV1", omitida=True, detalle="abortado"), "NO se intento"),
            (ResultadoOrden("e", "TV1", error="timeout"), "ERROR: timeout"),
            (ResultadoOrden("e", "TV1", confirmado=None, detalle="dry_run"), "no se hizo clic"),
            (ResultadoOrden("e", "TV1", confirmado=False), "NO confirmada"),
            (ResultadoOrden("e", "TV1", confirmado=True), "confirmada"),
        ]
        for r, esperado in casos:
            self.assertIn(esperado, lf._estado_de(r))


class TestMain(ListenerAislado, unittest.TestCase):
    """main() entero, con run_polling mockeado. En MT5 main() quedo usando
    funciones sin importar: NameError al arrancar, flota sin bot, y sin bot
    tampoco llegaba el update con el arreglo. Los 467 tests pasaban."""

    def setUp(self):
        self.aislar()

    def _correr_main(self, activar=None, latido=None):
        with patch.object(lf, "Application") as App, \
             patch.object(lf.avisos, "activar", activar or MagicMock(return_value=MagicMock())), \
             patch.object(lf.avisos, "Latido", latido or MagicMock()) as Latido:
            lf.main()
        App.builder.return_value.token.return_value.concurrent_updates.return_value \
            .post_init.return_value.build.return_value.run_polling.assert_called_once()
        return Latido

    def test_corre_entero(self):
        self._correr_main()

    def test_el_latido_tiene_sus_sondas_y_se_pueden_llamar(self):
        Latido = self._correr_main()
        sondas = Latido.call_args.args[1]
        self.assertEqual(set(sondas), {"loop", "recepcion", "estado"})
        for nombre, sonda in sondas.items():
            with self.subTest(sonda=nombre):
                sonda()
        self.assertIn("DRY_RUN", sondas["estado"]())

    def test_si_el_latido_o_los_avisos_fallan_arranca_igual(self):
        self._correr_main(latido=MagicMock(side_effect=RuntimeError("boom")))
        self._correr_main(activar=MagicMock(side_effect=RuntimeError("boom")))

    def test_sin_token_no_arranca(self):
        self.aislar(config_de_prueba(local=local_con(telegram_bot_token=None)))
        with patch.object(lf, "Application") as App, self.assertRaises(SystemExit):
            lf.main()
        App.builder.assert_not_called()

    def test_sin_prefijo_no_arranca(self):
        self.aislar(config_de_prueba(local=local_con(prefijo_telegram=None)))
        with patch.object(lf, "Application") as App, self.assertRaises(SystemExit) as e:
            lf.main()
        App.builder.assert_not_called()
        self.assertIn("prefijo_telegram", str(e.exception))

    def test_loguea_el_modo_y_las_advertencias(self):
        local = local_con(dry_run=False)
        local["plataformas"] = {}
        self.aislar(config_de_prueba(local=local), dry_run=False)
        with self.assertLogs("bot_futuros.listener", level="INFO") as logs:
            self._correr_main()
        textos = "\n".join(logs.output)
        self.assertIn("MODO: EN VIVO", textos)
        self.assertIn("CONFIG: 'plataformas'", textos)


if __name__ == "__main__":
    unittest.main()
