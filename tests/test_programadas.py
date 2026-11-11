"""Ordenes y cierres programados HH:MM: se guardan en disco, se rearman al
reiniciar, lo vencido no se ejecuta."""

import asyncio
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from tests.apoyo import BotFalso, ListenerAislado

import listener_futuros as lf


def _a_las(delta: timedelta) -> str:
    """HH:MM de ahora + delta (sirve para cualquier hora del dia salvo justo
    antes de medianoche, donde 'mas tarde' cae mañana: igual es futuro)."""
    return (datetime.now() + delta).strftime("%H:%M")


class TestDisco(ListenerAislado, unittest.TestCase):

    def setUp(self):
        self.aislar()

    def test_guardar_y_olvidar(self):
        self.assertEqual(lf._leer_programadas(), [])
        lf._guardar_programada({"id": "abc", "etiqueta": "cierre"})
        lf._guardar_programada({"id": "xyz", "etiqueta": "orden"})
        self.assertEqual([e["id"] for e in lf._leer_programadas()], ["abc", "xyz"])
        lf._olvidar_programada("abc")
        self.assertEqual([e["id"] for e in lf._leer_programadas()], ["xyz"])

    def test_un_archivo_corrupto_no_tumba_el_arranque(self):
        lf.ARCHIVO_PROGRAMADAS.write_text("{esto no es json", encoding="utf-8")
        self.assertEqual(lf._leer_programadas(), [])

    def test_programadas_cerca(self):
        ahora = datetime.now()
        lf._escribir_programadas([
            {"cuando": (ahora + timedelta(minutes=3)).isoformat(), "etiqueta": "una"},
            {"cuando": (ahora + timedelta(hours=4)).isoformat(), "etiqueta": "otra"},
            {"cuando": "no-es-fecha"}, {},
        ])
        cerca = lf._programadas_cerca(10)
        self.assertEqual(len(cerca), 1)
        self.assertIn("una", cerca[0])


class TestRearmar(ListenerAislado, unittest.TestCase):

    def setUp(self):
        self.aislar()

    def _rearmar_con(self, cuando):
        lf._guardar_programada({
            "id": "abc",
            "cuando": cuando.isoformat(timespec="seconds"),
            "etiqueta": "MNQ buy 1 en TV1",
            "chat_id": 42,
            "spec": {"tipo": "lote", "ordenes": []},
        })
        app = MagicMock()
        app.bot = BotFalso()
        armadas = []
        with patch.object(lf, "_armar", side_effect=lambda *a, **k: armadas.append(a)), \
             patch.object(lf, "_sellar_loop", new=MagicMock(return_value=asyncio.sleep(0))):
            asyncio.run(lf._rearmar_programadas(app))
        return armadas, app.bot.enviados

    def test_lo_vencido_avisa_y_no_se_ejecuta(self):
        armadas, enviados = self._rearmar_con(datetime.now() - timedelta(hours=2))
        self.assertEqual(armadas, [])
        self.assertEqual(enviados[0][0], 42)
        self.assertIn("NO se ejecuto", enviados[0][1])
        self.assertEqual(lf._leer_programadas(), [])

    def test_lo_futuro_se_rearma_y_queda_anotado(self):
        armadas, enviados = self._rearmar_con(datetime.now() + timedelta(hours=2))
        self.assertEqual(len(armadas), 1)
        self.assertIn("Retomado tras reiniciar", enviados[0][1])
        self.assertEqual([e["id"] for e in lf._leer_programadas()], ["abc"])

    def test_una_entrada_ilegible_se_descarta(self):
        lf._escribir_programadas([{"id": "x"}])
        app = MagicMock()
        app.bot = BotFalso()
        with patch.object(lf, "_sellar_loop", new=MagicMock(return_value=asyncio.sleep(0))):
            asyncio.run(lf._rearmar_programadas(app))
        self.assertEqual(lf._leer_programadas(), [])


class TestProgramarPorTelegram(ListenerAislado, unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.aislar()

    async def test_una_orden_con_hora_se_programa_y_no_se_coloca_ya(self):
        bot = await self.mandar(f"FUT1 MNQ buy 1 40 80 TV1 {_a_las(timedelta(hours=2))}")
        self.assertEqual(self.ej.intentadas, [])
        self.assertIn("Programado: MNQ buy 1 en TV1", bot.textos())
        entradas = lf._leer_programadas()
        self.assertEqual(len(entradas), 1)
        self.assertEqual(entradas[0]["spec"]["tipo"], "lote")
        self.assertEqual(entradas[0]["chat_id"], 42)

    async def test_un_cierre_con_hora_se_programa(self):
        bot = await self.mandar(f"FUT1 close TS1 {_a_las(timedelta(hours=2))}")
        self.assertEqual(self.ej.cerradas, [])
        self.assertIn("Programado: cierre de TS1", bot.textos())
        self.assertEqual(lf._leer_programadas()[0]["spec"]["cierre"]["cuenta"], "TS1")

    async def test_mezcla_inmediatas_y_programadas(self):
        hora = _a_las(timedelta(hours=2))
        await self.mandar(f"FUT1 MNQ buy 1 40 80 TV1\n"
                          f"FUT1 MES sell 1 40 80 TV1 {hora}\n"
                          f"FUT1 MNQ sell 1 40 80 TS1 {hora}")
        self.assertEqual([t["simbolo"] for t in self.ej.colocadas], ["MNQ"])
        entradas = lf._leer_programadas()
        self.assertEqual(len(entradas), 1)
        self.assertEqual(len(entradas[0]["spec"]["ordenes"]), 2)

    async def test_una_orden_invalida_no_se_programa(self):
        bot = await self.mandar(f"FUT1 MNQ buy 9 40 80 TV2 {_a_las(timedelta(hours=2))}")
        self.assertEqual(lf._leer_programadas(), [])
        self.assertIn("maximo", bot.textos())

    async def test_un_cierre_de_cuenta_desconocida_no_se_programa(self):
        bot = await self.mandar(f"FUT1 close XX9 {_a_las(timedelta(hours=2))}")
        self.assertEqual(lf._leer_programadas(), [])
        self.assertIn("desconocida", bot.textos())

    async def test_al_vencer_se_ejecuta_y_se_borra_del_disco(self):
        bot = BotFalso()
        responder = lf._responder_a(bot, 42)
        spec = {"tipo": "lote", "ordenes": [
            {"simbolo": "MNQ", "accion": "buy", "contratos": 1, "sl_ticks": 40,
             "tp_ticks": 80, "cuenta": "TV1", "at": "10:00"}]}
        lf._guardar_programada({"id": "t1", "cuando": datetime.now().isoformat(),
                                "etiqueta": "x", "chat_id": 42, "spec": spec})
        lf._armar(responder, datetime.now() - timedelta(seconds=1), spec, "x", "t1")
        for _ in range(100):
            if self.ej.colocadas and not lf._TAREAS_PROGRAMADAS:
                break
            await asyncio.sleep(0.02)
        self.assertEqual(len(self.ej.colocadas), 1)
        self.assertEqual(lf._leer_programadas(), [])
        self.assertIn("ejecutando x", bot.textos())

    async def test_al_vencer_se_revalida_contra_la_config(self):
        # Una programada pudo sobrevivir a un update que saco la cuenta.
        bot = BotFalso()
        spec = {"tipo": "lote", "ordenes": [
            {"simbolo": "MNQ", "accion": "buy", "contratos": 1, "sl_ticks": 40,
             "tp_ticks": 80, "cuenta": "YA_NO_EXISTE", "at": "10:00"}]}
        await lf._ejecutar_spec(lf._responder_a(bot, 42), spec)
        self.assertEqual(self.ej.intentadas, [])
        self.assertIn("desconocida", bot.textos())


if __name__ == "__main__":
    unittest.main()
