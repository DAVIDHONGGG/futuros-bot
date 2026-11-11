"""Avisos de error a un grupo aparte (copiado de MT5, con el latido nuevo).

Lo que se prueba aca no es "manda el mensaje": es que NO pueda hacerle dano
al hilo que esta colocando una orden, y que el token no salga nunca.
"""

import logging
import queue
import time
import unittest
from unittest.mock import patch

from tests.apoyo import TOKEN_PRUEBA  # noqa: F401  (activa la red de seguridad)

import avisos


class TestEmitNoPuedeHacerDano(unittest.TestCase):
    """logging.Handler.handle() NO envuelve emit() en try/except, asi que una
    excepcion aca sale por el log.error() del hilo que la emitio.

    Y varios de esos log.error estan en lugares donde eso es catastrofico:
    dentro del for de un lote de ordenes (EjecutorBase.colocar_lote)."""

    def _record(self, msg="algo fallo", nivel=logging.ERROR, nombre="bot_futuros.ejecutor"):
        return logging.LogRecord(nombre, nivel, "f.py", 1, msg, None, None)

    def test_no_tira_aunque_la_cola_este_llena(self):
        cola = queue.Queue(maxsize=1)
        cola.put(("x", "ya esta llena"))
        h = avisos.AvisoDeErrores(cola)
        h.emit(self._record())          # no debe tirar
        self.assertEqual(cola.qsize(), 1)

    def test_no_tira_con_un_record_roto(self):
        h = avisos.AvisoDeErrores(queue.Queue())
        roto = self._record("%s %s", )
        roto.args = ("solo-uno",)       # getMessage() va a tirar
        h.emit(roto)                    # no debe propagar

    def test_no_tira_si_la_cola_explota(self):
        class ColaRota:
            def put_nowait(self, _):
                raise MemoryError("sin memoria")
        avisos.AvisoDeErrores(ColaRota()).emit(self._record())

    def test_no_bloquea_nunca(self):
        # Si usara put() con timeout en vez de put_nowait, el hilo que opera
        # esperaria. Con la cola llena tiene que volver de inmediato.
        cola = queue.Queue(maxsize=1)
        cola.put(("x", "llena"))
        h = avisos.AvisoDeErrores(cola)
        t0 = time.monotonic()
        for _ in range(500):
            h.emit(self._record())
        self.assertLess(time.monotonic() - t0, 0.5)


class TestQueSeEncola(unittest.TestCase):

    def _emitir(self, nombre, nivel=logging.ERROR, msg="falla"):
        cola = queue.Queue()
        avisos.AvisoDeErrores(cola).emit(
            logging.LogRecord(nombre, nivel, "f.py", 1, msg, None, None))
        return cola

    def test_los_warning_no_entran(self):
        # 45 warnings en el codigo y la mayoria son recuperables
        # ("reintentando"). Si entran, el grupo deja de estar limpio.
        self.assertTrue(self._emitir("bot_futuros.ejecutor", logging.WARNING).empty())

    def test_los_error_del_bot_si_entran(self):
        self.assertFalse(self._emitir("bot_futuros.ejecutor").empty())
        self.assertFalse(self._emitir("bot_futuros.listener").empty())

    def test_los_fallos_de_RED_de_telegram_NO_entran(self):
        # Medido: 261 de los 317 ERROR del historico son fallos de red contra
        # api.telegram.org, 73 de ellos en SEIS minutos. Mandarlos por
        # Telegram seria avisar por Telegram que Telegram no anda, y la cola
        # se llenaria de eso justo cuando los errores de trading importan.
        for nombre in ("telegram", "telegram.ext._application", "httpx",
                       "httpcore", "urllib3", "asyncio"):
            with self.subTest(logger=nombre):
                self.assertTrue(self._emitir(nombre).empty())


class TestElTokenNoSaleNunca(unittest.TestCase):
    """python-telegram-bot construye el mensaje de InvalidToken como
    f"The token `{self._token}` was rejected by the server." y lo loguea con
    .exception(). Y mapea 404 a InvalidToken, asi que un portal cautivo al
    arrancar genera ese record con el token BUENO adentro."""

    TOKEN = "8841225053:AAFejemplo_de_token_largo_para_probar12"

    def test_lo_saca_aunque_venga_en_el_texto(self):
        texto = f"The token `{self.TOKEN}` was rejected by the server."
        limpio = avisos.sanear(texto, self.TOKEN)
        self.assertNotIn(self.TOKEN, limpio)
        self.assertIn("***", limpio)

    def test_lo_saca_aunque_NO_sea_el_propio(self):
        # Por forma, no solo por igualdad: el traceback de urllib trae la URL
        # con el token adentro, y podria ser el de otra maquina.
        ajeno = "1234567890:BBHotroTokenCompletamenteDistinto_xyz"
        limpio = avisos.sanear(f"POST /bot{ajeno}/sendMessage fallo", "")
        self.assertNotIn(ajeno, limpio)

    def test_no_rompe_con_texto_sin_token(self):
        self.assertEqual(avisos.sanear("todo bien", self.TOKEN), "todo bien")

    def test_tolera_vacio(self):
        self.assertEqual(avisos.sanear("", self.TOKEN), "")
        self.assertEqual(avisos.sanear(None, self.TOKEN), "")


class TestPolitica(unittest.TestCase):

    def _enviador(self):
        e = avisos.Enviador("t", 1, "FUT1", queue.Queue())
        self.enviados = []
        e._postear = lambda texto: (self.enviados.append(texto), True)[1]
        return e

    def test_agrupa_el_mismo_error_repetido(self):
        # El 15/10 hubo 6 abortos identicos en 40 minutos: serian 6 mensajes
        # casi iguales.
        e = self._enviador()
        for _ in range(6):
            texto, mandar = e._agrupado("ABORTO: el dialogo tiene AUDCAD.pro")
            if mandar:
                self.enviados.append(texto)
        self.assertEqual(len(self.enviados), 1)

    def test_pasada_la_ventana_vuelve_a_avisar_y_dice_cuantos(self):
        e = self._enviador()
        e._agrupado("mismo error")
        for _ in range(3):
            e._agrupado("mismo error")
        with patch.object(avisos, "VENTANA_REPETIDO", 0):
            texto, mandar = e._agrupado("mismo error")
        self.assertTrue(mandar)
        self.assertIn("3", texto)

    def test_el_tope_por_minuto_frena(self):
        e = self._enviador()
        for _ in range(avisos.MAXIMO_POR_MINUTO):
            self.assertTrue(e._hay_lugar())
            e._enviados.append(time.monotonic())
        self.assertFalse(e._hay_lugar())

    def test_un_traceback_largo_se_recorta(self):
        # Medido: cada traceback de estos logs pesa 2306-2833 caracteres, y
        # un mensaje de mas de 4096 devuelve 400 y se pierde EN SILENCIO.
        largo = "x" * 9000
        self.assertLessEqual(len(avisos._recortar(largo)), avisos.LARGO_MAXIMO + 120)
        self.assertIn("completo en el log", avisos._recortar(largo))


class TestLatido(unittest.TestCase):
    """Un latido que sobrevive a la falla que debe detectar es PEOR que no
    tenerlo: certifica como sana una maquina que no puede operar."""

    def _latido(self, **sondas):
        base = {"loop": lambda: time.monotonic(),
                "recepcion": lambda: time.monotonic(),
                "estado": lambda: ["DRY_RUN", "3 cuenta(s)"]}
        base.update(sondas)
        enviador = avisos.Enviador("t", 1, "FUT1", queue.Queue())
        return avisos.Latido(enviador, base)

    def test_todo_bien(self):
        texto = self._latido().texto()
        self.assertIn("loop ok", texto)
        self.assertIn("DRY_RUN", texto)
        self.assertIn("3 cuenta(s)", texto)

    def test_DENUNCIA_el_loop_trabado(self):
        # Proceso vivo, loop trabado: la maquina no recibe ordenes.
        viejo = time.monotonic() - 3600
        self.assertIn("LOOP TRABADO", self._latido(loop=lambda: viejo).texto())

    def test_dice_cuando_fue_el_ultimo_mensaje_RECIBIDO(self):
        viejo = time.monotonic() - 7200
        self.assertIn("ultimo mensaje hace 120 min",
                      self._latido(recepcion=lambda: viejo).texto())

    def test_una_sonda_que_explota_no_tumba_el_latido(self):
        def revienta():
            raise RuntimeError("navegador colgado")
        texto = self._latido(estado=revienta, loop=revienta).texto()
        self.assertIn("ESTADO ILEGIBLE", texto)
        self.assertIn("loop: sin datos", texto)

    def test_informa_los_cortes_de_red(self):
        l = self._latido()
        l.enviador.cortes_de_red = 17
        self.assertIn("17 corte(s) de red", l.texto())


class TestActivar(unittest.TestCase):

    def tearDown(self):
        avisos._enviador = None

    def test_sin_chat_configurado_no_hace_nada(self):
        # Una maquina sin el grupo tiene que seguir funcionando igual.
        self.assertIsNone(avisos.activar("token", None, "FUT1"))
        self.assertIsNone(avisos.activar("token", "", "FUT1"))

    def test_sin_token_tampoco(self):
        self.assertIsNone(avisos.activar("", 123, "FUT1"))


if __name__ == "__main__":
    unittest.main()
