"""Avisos a un grupo de Telegram APARTE: solo errores, y un latido diario.

Copiado del bot de MT5 (avisos.py), donde ya se probo en la flota. Lo unico
que cambia es el latido: en vez de mirar MT5 y la pantalla, recibe lineas de
estado que arma el listener (modo, ejecutor, navegador).

POR QUE EXISTE. El grupo de ordenes tiene el trafico normal -ordenes y
confirmaciones- y los errores se pierden ahi adentro. Un segundo grupo que
solo habla cuando algo falla se lee en dos segundos.

Y la otra mitad, que es la que de verdad protege: el LATIDO. Un canal que
solo habla cuando falla no distingue "todo bien" de "la maquina esta
muerta": el silencio significa las dos cosas.

REGLA QUE MANDA SOBRE TODO LO DEMAS: esto NUNCA puede demorar ni romper el
hilo que esta colocando o cerrando una posicion. Un aviso que no sale es un
problema menor; una orden que se traba con la terminal web a medio llenar,
no. De ahi salen casi todas las decisiones raras de este archivo.
"""

import json
import logging
import queue
import re
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger("bot_futuros.avisos")

# --- Lo que NUNCA se manda ------------------------------------------------

# Loggers cuyos ERROR no son del bot sino de la plomeria. El de telegram es
# el caso grave: medido en estos logs, 261 de los 317 ERROR del historico
# son fallos de red contra api.telegram.org (148 "getaddrinfo failed", o sea
# la PC sin internet). Mandarlos por Telegram seria avisar por Telegram que
# Telegram no anda: 73 de esos cayeron en SEIS minutos el 22/09, y por 6
# maquinas serian ~438 mensajes al mismo grupo, contra un limite de ~20 por
# minuto. La cola se llenaria de eso y los errores de trading -los unicos
# que importan- se descartarian.
#
# Los cortes de red no se pierden: se cuentan y el numero sale en el latido,
# que es donde sirve ("17 cortes de red hoy").
LOGGERS_IGNORADOS = frozenset({
    "telegram", "httpx", "httpcore", "urllib3", "asyncio", "apscheduler",
})

# El token no puede salir NUNCA, ni siquiera a un grupo del dueno.
#
# No es paranoia: python-telegram-bot construye el mensaje de InvalidToken
# como f"The token `{self._token}` was rejected by the server." (_bot.py) y
# lo loguea con .exception(), o sea un record ERROR con el token adentro. Y
# PTB mapea 404 a InvalidToken, asi que no hace falta que el token este mal:
# un portal cautivo o un DNS que conteste 404 al arrancar -el escenario que
# motivo bootstrap_retries=-1- genera ese record con el token BUENO.
#
# Ademas el propio envio de aca pone el token en la URL, asi que cualquier
# traceback de urllib lo arrastra.
# Sin \b al principio: en "/bot8841225053:AAF..." no hay frontera de palabra
# entre la "t" y el digito, y esa es justamente la forma en la que el token
# aparece en los tracebacks de urllib (va dentro de la URL de la API).
_PATRON_TOKEN = re.compile(r"(?<!\d)\d{8,12}:[A-Za-z0-9_-]{30,}")


def sanear(texto: str, token: str = "") -> str:
    """El texto sin el token, pase por donde pase."""
    if not texto:
        return ""
    limpio = str(texto)
    if token:
        limpio = limpio.replace(token, "***")
    return _PATRON_TOKEN.sub("***", limpio)


# --- Cuanto se manda ------------------------------------------------------

LARGO_MAXIMO = 3500          # Telegram corta en 4096; un traceback pesa ~2800.
VENTANA_REPETIDO = 300       # segundos: el mismo error dentro de esto se agrupa
MAXIMO_POR_MINUTO = 10
TAMANO_COLA = 200
TIMEOUT_ENVIO = 15           # segundos


def _recortar(texto: str) -> str:
    if len(texto) <= LARGO_MAXIMO:
        return texto
    sobran = len(texto) - LARGO_MAXIMO
    return texto[:LARGO_MAXIMO] + f"\n... (+{sobran} caracteres; completo en el log)"


class AvisoDeErrores(logging.Handler):
    """Encola los ERROR para que otro hilo los mande. NADA MAS.

    CRITICO: logging.Handler.handle() NO envuelve a emit() en try/except -ni
    Logger.callHandlers tampoco-, asi que cualquier excepcion aca sale por el
    log.error() del hilo que la emitio. Y varios de esos log.error estan en
    lugares donde eso seria catastrofico: dentro del for de un lote (ver
    EjecutorBase.colocar_lote) saldria del bucle y las cuentas restantes no
    se intentarian ni se reportarian.

    Por eso: todo adentro de try/except BaseException, y el cuerpo es lo mas
    corto posible. Nada de I/O, nada de locks propios, nada de formatear el
    traceback completo. El estado de agrupado y de tope por minuto vive SOLO
    en el hilo que manda: compartirlo bajo un lock permitiria que el hilo que
    opera se quede esperando a un POST, y socket.getaddrinfo NO respeta el
    timeout de urlopen -son decenas de segundos en Windows sin red-.
    """

    def __init__(self, cola: queue.Queue):
        super().__init__(level=logging.ERROR)
        self.cola = cola

    def emit(self, record):
        try:
            if record.levelno < logging.ERROR:
                return
            if record.name.split(".")[0] in LOGGERS_IGNORADOS:
                return
            texto = f"{record.getMessage()}"
            if record.exc_info and record.exc_info[1] is not None:
                e = record.exc_info[1]
                texto = f"{texto}\n{type(e).__name__}: {e}"
            self.cola.put_nowait((record.name, texto))
        except BaseException:
            # Ni siquiera self.handleError(record): escribe a stderr y con
            # raiseExceptions puede volver a tirar.
            pass


class Enviador(threading.Thread):
    """Vacia la cola y manda al grupo de errores. Agrupa repetidos y frena.

    Corre como daemon: si el proceso termina, no lo demora. Lo que quede en
    la cola se pierde, y esta bien -el log de la maquina lo conserva igual-.
    """

    def __init__(self, token: str, chat_id, maquina: str, cola: queue.Queue):
        super().__init__(daemon=True, name="avisos")
        self.token, self.chat_id, self.maquina = token, str(chat_id), maquina
        self.cola = cola
        self._vistos = {}          # texto -> (primera_vez, repeticiones)
        self._enviados = []        # marcas de tiempo, para el tope por minuto
        self._suprimidos = 0
        self.cortes_de_red = 0     # lo lee el latido

    # -- envio ------------------------------------------------------------

    def _postear(self, texto: str) -> bool:
        cuerpo = json.dumps({
            "chat_id": self.chat_id,
            "text": f"[{self.maquina}] " + _recortar(sanear(texto, self.token)),
            "disable_notification": False,
        }).encode("utf-8")
        pedido = urllib.request.Request(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            data=cuerpo, headers={"Content-Type": "application/json"},
        )
        try:
            # El timeout NO es opcional: socket.getdefaulttimeout() es None,
            # asi que sin esto un urlopen puede colgar el hilo para siempre.
            # Sin parse_mode a proposito: los mensajes traen rutas de Windows
            # y nombres con "_", que con Markdown dan 400 y se pierden.
            with urllib.request.urlopen(pedido, timeout=TIMEOUT_ENVIO) as r:
                return 200 <= r.status < 300
        except BaseException:
            # NUNCA loguear aca con el logger del bot: seria un bucle.
            self.cortes_de_red += 1
            return False

    # -- politica ---------------------------------------------------------

    def _hay_lugar(self) -> bool:
        ahora = time.monotonic()
        self._enviados = [t for t in self._enviados if ahora - t < 60]
        return len(self._enviados) < MAXIMO_POR_MINUTO

    def _agrupado(self, texto: str):
        """(texto_a_mandar, mandar) aplicando la ventana de repetidos."""
        ahora = time.monotonic()
        clave = texto[:200]
        primera, repes = self._vistos.get(clave, (0.0, 0))
        if primera and ahora - primera < VENTANA_REPETIDO:
            self._vistos[clave] = (primera, repes + 1)
            return "", False
        # Se vencio la ventana: si hubo repeticiones, se dicen.
        self._vistos[clave] = (ahora, 0)
        if repes:
            return f"{texto}\n(se repitio {repes} vez/veces mas)", True
        return texto, True

    def run(self):
        while True:
            try:
                # Con timeout y no get() pelado: si no, un agrupado se
                # quedaria esperando al proximo error para salir.
                nombre, texto = self.cola.get(timeout=30)
            except queue.Empty:
                self._vaciar_suprimidos()
                continue
            except BaseException:
                time.sleep(1)
                continue
            try:
                mensaje, mandar = self._agrupado(texto)
                if not mandar:
                    continue
                if not self._hay_lugar():
                    self._suprimidos += 1
                    continue
                self._enviados.append(time.monotonic())
                self._postear(mensaje)
            except BaseException:
                pass

    def _vaciar_suprimidos(self):
        if self._suprimidos and self._hay_lugar():
            n, self._suprimidos = self._suprimidos, 0
            self._enviados.append(time.monotonic())
            self._postear(f"({n} aviso(s) mas suprimidos por el tope de "
                          f"{MAXIMO_POR_MINUTO} por minuto)")




# --- Latido ---------------------------------------------------------------

HORAS_ENTRE_LATIDOS = 24
LOOP_FRESCO = 180        # segundos: mas viejo que esto, el loop esta trabado


class Latido(threading.Thread):
    """Un mensaje por dia diciendo que esta maquina sigue sirviendo.

    POR QUE NO ALCANZA CON LOS ERRORES: si el grupo esta callado, puede ser
    que todo ande o que la maquina este muerta. El silencio significa las
    dos cosas (en la flota de MT5: una maquina 6.5 h caida, y una tarde con
    codigo viejo, sin un solo error que mandar).

    QUE MIDE. Un latido que sobrevive a la falla que tiene que detectar es
    peor que no tenerlo. Por eso NO basta con "el proceso vive":

    - loop: lo sella el propio event loop de Telegram. Si esta trabado, la
      maquina no recibe ordenes aunque el proceso este vivo.
    - recepcion: se sella al recibir cualquier mensaje, antes de los filtros.
      Mandar y recibir son caminos distintos: con dos listeners robandose los
      mensajes, el envio anda y las ordenes se pierden.
    - estado: lineas que arma el listener (modo, ejecutor, navegador...).

    Las sondas se INYECTAN y ninguna puede tener efectos secundarios: las
    llama este hilo mientras otro puede estar colocando una orden.
    """

    def __init__(self, enviador: "Enviador", sondas: dict,
                 horas: float = HORAS_ENTRE_LATIDOS):
        super().__init__(daemon=True, name="latido")
        self.enviador, self.sondas, self.horas = enviador, sondas, horas

    def _edad(self, nombre):
        try:
            sello = self.sondas.get(nombre, lambda: None)()
        except BaseException:
            return None
        if not sello:
            return None
        return time.monotonic() - sello

    def texto(self) -> str:
        partes = []
        edad_loop = self._edad("loop")
        if edad_loop is None:
            partes.append("loop: sin datos")
        elif edad_loop > LOOP_FRESCO:
            partes.append(f"LOOP TRABADO hace {int(edad_loop / 60)} min")
        else:
            partes.append("loop ok")

        edad_rx = self._edad("recepcion")
        if edad_rx is None:
            partes.append("sin mensajes recibidos aun")
        else:
            partes.append(f"ultimo mensaje hace {int(edad_rx / 60)} min")

        try:
            estado = self.sondas.get("estado", lambda: [])() or []
            partes.extend(str(e) for e in estado)
        except BaseException as e:
            partes.append(f"ESTADO ILEGIBLE ({type(e).__name__})")

        cortes = getattr(self.enviador, "cortes_de_red", 0)
        if cortes:
            partes.append(f"{cortes} corte(s) de red")
        return "vivo | " + " | ".join(partes)

    def run(self):
        espera = max(60.0, self.horas * 3600)
        while True:
            time.sleep(espera)
            try:
                self.enviador._postear(self.texto())
            except BaseException:
                pass


# --- Arranque -------------------------------------------------------------

_enviador = None


def activar(token: str, chat_id, maquina: str):
    """Engancha el handler al logger raiz. Devuelve el Enviador, o None.

    Sin chat_id configurado no se hace nada: la maquina funciona igual.
    """
    global _enviador
    if not token or not chat_id:
        return None
    if _enviador is not None:
        return _enviador
    cola = queue.Queue(maxsize=TAMANO_COLA)
    _enviador = Enviador(token, chat_id, maquina, cola)
    _enviador.start()
    logging.getLogger().addHandler(AvisoDeErrores(cola))
    log.info("Avisos de error activados hacia el chat %s.", chat_id)
    return _enviador


def enviador():
    return _enviador
