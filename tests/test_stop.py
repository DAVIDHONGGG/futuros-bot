"""El "stop": que se entienda, que llegue aunque haya un lote corriendo, y que
impida colocar lo que falta."""

import asyncio
import threading
import unittest
from unittest.mock import AsyncMock, patch

from tests.apoyo import (INTRUSO, EjecutorDePrueba, ListenerAislado, TOKEN_PRUEBA,
                         contexto_falso, trabajo, update_falso)

import ejecutor
import listener_futuros as lf


class TestRegexStop(unittest.TestCase):

    def test_reconoce_las_palabras(self):
        for texto in ("stop", "STOP", " parar ", "cancelar", "cancel", "Alto",
                      "FUT1 stop", "ALL parar", "fut1,fut2 stop"):
            self.assertTrue(lf.REGEX_STOP.match(texto), texto)

    def test_no_se_confunde(self):
        for texto in ("stopper", "stop 123", "MNQ buy 1 40 80", "close", "FUT1 close",
                      "parardo"):
            self.assertIsNone(lf.REGEX_STOP.match(texto), texto)

    def test_el_pelado_no_nombra_destino(self):
        self.assertIsNone(lf.REGEX_STOP.match("stop").group("destinos"))
        self.assertEqual(lf.REGEX_STOP.match("FUT2 stop").group("destinos"), "FUT2")


class TestOnStop(ListenerAislado, unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.aislar()

    async def test_el_stop_pelado_frena(self):
        with patch.object(lf, "_parar_todo", new=AsyncMock()) as p:
            await self.mandar("stop")
        p.assert_awaited_once()

    async def test_el_stop_para_mi_frena(self):
        with patch.object(lf, "_parar_todo", new=AsyncMock()) as p:
            await self.mandar("FUT1 stop")
            await self.mandar("ALL stop")
        self.assertEqual(p.await_count, 2)

    async def test_el_stop_para_otra_maquina_no_frena(self):
        with patch.object(lf, "_parar_todo", new=AsyncMock()) as p:
            await self.mandar("FUT2 stop")
        p.assert_not_awaited()

    async def test_el_stop_de_un_intruso_no_frena(self):
        with patch.object(lf, "_parar_todo", new=AsyncMock()) as p:
            await self.mandar("stop", user_id=INTRUSO)
        p.assert_not_awaited()

    async def test_on_message_no_procesa_el_stop(self):
        # Lo atiende on_stop; si on_message tambien lo tomara, se contestaria
        # "Formato invalido" encima del PARANDO.
        ctx = contexto_falso()
        await lf.on_message(update_falso("FUT1 stop"), ctx)
        self.assertEqual(ctx.bot.enviados, [])

    async def test_parar_todo_cancela_lo_programado_y_rehabilita(self):
        bot = await self.mandar("FUT1 MNQ buy 1 40 80 TV1 23:59")
        self.assertEqual(len(lf._leer_programadas()), 1)
        self.assertEqual(len(lf._TAREAS_PROGRAMADAS), 1)

        bot = await self.mandar("stop")

        self.assertEqual(lf._leer_programadas(), [])
        self.assertEqual(len(lf._TAREAS_PROGRAMADAS), 0)
        self.assertIn("Canceladas 1 operacion", bot.textos())
        self.assertIn("ya podes mandar ordenes", bot.textos())
        self.assertFalse(ejecutor.aborto_pedido())


class TestStopDuranteUnLote(ListenerAislado, unittest.IsolatedAsyncioTestCase):
    """El caso que importa: llega el stop MIENTRAS se coloca un lote."""

    async def test_el_stop_corta_las_ordenes_que_faltan(self):
        frenar = threading.Event()
        self.aislar(frenar=frenar)

        lote = asyncio.create_task(self.mandar(
            "FUT1 MNQ buy 1 40 80 TV1\nFUT1 MNQ buy 1 40 80 TS1\nFUT1 MES buy 1 40 80 TV1"))
        await asyncio.to_thread(self.ej.en_curso.wait, 5)   # orden #1 en curso
        self.assertTrue(ejecutor.hay_operacion_en_curso())

        parada = asyncio.create_task(self.mandar("stop"))
        for _ in range(100):
            if ejecutor.aborto_pedido():
                break
            await asyncio.sleep(0.01)
        self.assertTrue(ejecutor.aborto_pedido())
        # Mientras el lote siga corriendo, la bandera NO se baja.
        await asyncio.sleep(0.3)
        self.assertTrue(ejecutor.aborto_pedido())

        frenar.set()                                         # termina la #1
        await asyncio.wait_for(asyncio.gather(lote, parada), 10)

        self.assertEqual(len(self.ej.intentadas), 1, "se intento colocar despues del stop")
        self.assertFalse(ejecutor.aborto_pedido(), "la bandera quedo levantada")
        self.assertFalse(ejecutor.hay_operacion_en_curso())


class TestAbortoEnElEjecutor(unittest.TestCase):
    """La politica del lote ante un aborto (EjecutorBase)."""

    def tearDown(self):
        ejecutor.limpiar_aborto()

    def _ejecutor(self, **kw):
        return EjecutorDePrueba(**kw)

    def test_aborto_pedido_en_la_orden_1_no_coloca_las_siguientes(self):
        ej = self._ejecutor(abortar_en=1)
        res = ej.colocar_lote([trabajo("TV1"), trabajo("TS1"), trabajo("TV2")])
        self.assertEqual(len(ej.intentadas), 1)
        self.assertEqual([r.omitida for r in res], [False, True, True])

    def test_aborto_antes_del_clic_no_coloca_esa_ni_las_siguientes(self):
        # Asi lo va a usar el ejecutor web: chequear_aborto() justo antes del
        # clic de Comprar/Vender.
        ej = self._ejecutor(abortar_en=1, aborta_antes_del_clic=True)
        res = ej.colocar_lote([trabajo("TV1"), trabajo("TS1")])
        self.assertEqual(ej.colocadas, [])
        self.assertTrue(all(r.omitida for r in res))
        self.assertIn("No se hizo el clic final", res[0].detalle)

    def test_con_el_aborto_ya_levantado_no_se_intenta_nada(self):
        ejecutor.solicitar_aborto()
        ej = self._ejecutor()
        res = ej.colocar_lote([trabajo("TV1")])
        self.assertEqual(ej.intentadas, [])
        self.assertTrue(res[0].omitida)

    def test_limpiar_cuando_libre_espera_a_que_termine(self):
        frenar = threading.Event()
        ej = self._ejecutor(frenar=frenar)
        hilo = threading.Thread(target=ej.colocar_lote, args=([trabajo()],))
        hilo.start()
        ej.en_curso.wait(5)
        ejecutor.solicitar_aborto()
        resultado = []
        limpiador = threading.Thread(
            target=lambda: resultado.append(ejecutor.limpiar_aborto_cuando_libre(5)))
        limpiador.start()
        limpiador.join(0.5)
        self.assertTrue(limpiador.is_alive(), "bajo la bandera con la operacion corriendo")
        frenar.set()
        hilo.join(5)
        limpiador.join(5)
        self.assertEqual(resultado, [True])
        self.assertFalse(ejecutor.aborto_pedido())


class TestArranqueDelStop(unittest.TestCase):
    """Lo que hace que el stop no quede en cola detras del lote."""

    def test_concurrent_updates_y_block_false(self):
        with patch.object(lf, "Application") as App, \
             patch.object(lf, "MessageHandler") as MH, \
             patch.object(lf, "BOT_TOKEN", TOKEN_PRUEBA), \
             patch.object(lf, "PREFIJO", "FUT1"), \
             patch.object(lf.avisos, "activar", return_value=None):
            lf.main()
        App.builder.return_value.token.return_value.concurrent_updates \
            .assert_called_once_with(True)
        llamadas_stop = [c for c in MH.call_args_list if c.args[1] is lf.on_stop]
        self.assertEqual(len(llamadas_stop), 1)
        self.assertIs(llamadas_stop[0].kwargs.get("block"), False)


if __name__ == "__main__":
    unittest.main()
