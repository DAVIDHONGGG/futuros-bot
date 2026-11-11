"""Cada maquina ejecuta SOLO las lineas dirigidas a ella.

Se prueba en dos niveles: las funciones de ruteo, y de punta a punta por
on_message con el ejecutor de prueba (que es lo que de verdad importa: que
una orden ajena o sin prefijo no llegue nunca al ejecutor).
"""

import unittest

from tests.apoyo import INTRUSO, ListenerAislado, config_de_prueba, local_con

import listener_futuros as lf


class TestRuteo(ListenerAislado, unittest.TestCase):

    def setUp(self):
        self.aislar()

    def test_separa_destinatarios(self):
        self.assertEqual(lf.destinatarios("FUT1 MNQ buy 1 40 80"),
                         ({"FUT1"}, "MNQ buy 1 40 80"))
        self.assertEqual(lf.destinatarios("fut1,fut3 close"), ({"FUT1", "FUT3"}, "close"))

    def test_acepta_su_prefijo_y_ALL(self):
        self.assertEqual(lf.lineas_para_mi("FUT1 MNQ buy 1 40 80"), ["MNQ buy 1 40 80"])
        self.assertEqual(lf.lineas_para_mi("fut1 MNQ buy 1 40 80"), ["MNQ buy 1 40 80"])
        self.assertEqual(lf.lineas_para_mi("ALL close"), ["close"])
        self.assertEqual(lf.lineas_para_mi("FUT2,FUT1 close"), ["close"])

    def test_ignora_otro_prefijo(self):
        self.assertEqual(lf.lineas_para_mi("FUT2 MNQ buy 1 40 80"), [])

    def test_ignora_la_orden_sin_prefijo(self):
        # Si no, la ejecutarian TODAS las maquinas del grupo.
        self.assertEqual(lf.lineas_para_mi("MNQ buy 1 40 80"), [])

    def test_toma_solo_sus_lineas_de_un_mensaje_repartido(self):
        mensaje = ("FUT1 MNQ buy 1 40 80\n"
                   "FUT2 MES sell 1 40 80\n"
                   "MNQ buy 1 40 80\n"
                   "FUT1 MES buy 1 20 40 TV1")
        self.assertEqual(lf.lineas_para_mi(mensaje),
                         ["MNQ buy 1 40 80", "MES buy 1 20 40 TV1"])

    def test_el_prefijo_solo_no_es_una_orden(self):
        self.assertEqual(lf.lineas_para_mi("FUT1"), [])

    def test_sin_prefijo_configurado_no_acepta_NADA(self):
        # En MT5, sin prefijo se aceptaba todo. Aca es obligatorio.
        self.aislar(config_de_prueba(local=local_con(prefijo_telegram=None)))
        self.assertIsNone(lf.PREFIJO)
        self.assertEqual(lf.lineas_para_mi("MNQ buy 1 40 80"), [])
        self.assertEqual(lf.lineas_para_mi("FUT1 MNQ buy 1 40 80"), [])
        self.assertEqual(lf.lineas_para_mi("ALL MNQ buy 1 40 80"), [])


class TestPrefijoDePuntaAPunta(ListenerAislado, unittest.IsolatedAsyncioTestCase):
    """Por on_message, hasta el ejecutor."""

    def setUp(self):
        self.aislar()

    async def test_con_su_prefijo_llega_al_ejecutor(self):
        bot = await self.mandar("FUT1 MNQ buy 1 40 80 TV1")
        self.assertEqual(len(self.ej.colocadas), 1)
        self.assertEqual(self.ej.colocadas[0]["contrato"], "MNQZ6")
        self.assertIn("Listo (DRY_RUN)", bot.textos())

    async def test_con_ALL_tambien(self):
        await self.mandar("ALL MNQ buy 1 40 80")
        self.assertEqual(len(self.ej.colocadas), 1)

    async def test_con_otro_prefijo_no_llega_y_no_contesta(self):
        bot = await self.mandar("FUT2 MNQ buy 1 40 80 TV1")
        self.assertEqual(self.ej.intentadas, [])
        self.assertEqual(bot.enviados, [])

    async def test_sin_prefijo_no_llega_y_no_contesta(self):
        bot = await self.mandar("MNQ buy 1 40 80 TV1")
        self.assertEqual(self.ej.intentadas, [])
        self.assertEqual(bot.enviados, [])

    async def test_de_un_mensaje_repartido_coloca_solo_lo_suyo(self):
        await self.mandar("FUT2 MNQ buy 1 40 80\nFUT1 MES sell 1 20 40 TV1\nMNQ buy 1 40 80")
        self.assertEqual([t["simbolo"] for t in self.ej.colocadas], ["MES"])

    async def test_un_usuario_no_autorizado_no_llega(self):
        bot = await self.mandar("FUT1 MNQ buy 1 40 80", user_id=INTRUSO)
        self.assertEqual(self.ej.intentadas, [])
        self.assertEqual(bot.enviados, [])

    async def test_update_y_chatid_sin_prefijo_no_los_toma_nadie(self):
        bot = await self.mandar("update")
        self.assertEqual(bot.enviados, [])
        bot = await self.mandar("chatid")
        self.assertEqual(bot.enviados, [])

    async def test_chatid_con_prefijo(self):
        bot = await self.mandar("FUT1 chatid", chat_id=-100123)
        self.assertIn("-100123", bot.textos())


if __name__ == "__main__":
    unittest.main()
