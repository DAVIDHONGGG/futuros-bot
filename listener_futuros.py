"""
Listener de Telegram del bot de futuros (terminales WEB de firmas de futuros:
TopstepX, Tradovate...).

Adaptado de telegram_listener.py del bot de MT5. El nombre del archivo es
DISTINTO a proposito: el actualizar.ps1 de MT5 mata todo python.exe cuya
linea de comando contenga "telegram_listener.py", y en una PC con los dos bots
se llevaria puesto a este.

Cada linea del grupo lleva delante el prefijo de la maquina (OBLIGATORIO en
este bot) o ALL:

    FUT1 MNQ buy 1 40 80                 -> cuenta por defecto, ya
    FUT1 MNQ buy 1 40 80 TS1             -> esa cuenta
    FUT1 MNQ sell 2 30 60 TS1,TV2 14:30  -> esas dos, a las 14:30
    FUT1 MNQ buy 1 40 80 all             -> todas las de esta maquina

Formato completo y validacion: ver ordenes.py. Varias lineas en un mensaje
son ordenes independientes; las que comparten hora se colocan juntas.

    FUT1 close [CUENTA|all] [HH:MM]      -> cierra posiciones
    FUT1 update / ALL update             -> actualiza desde el repo
    FUT1 chatid                          -> dice el chat_id de este chat
    stop / parar / cancelar              -> SIN prefijo frena a todas

Una linea SIN prefijo no la ejecuta nadie. El "stop" es la excepcion: sin
prefijo frena a todas (equivocarse frenando de mas no cuesta nada,
equivocarse operando de mas si).

Lo programado se anota en programadas.json y se rearma al reiniciar. Lo que
vencio con el listener caido NO se ejecuta: se avisa y se descarta.
"""

import asyncio
import json
import logging
import re
import subprocess
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from telegram import Update
from telegram import error as telegram_error
from telegram.ext import Application, ContextTypes, MessageHandler, filters

import avisos
import ejecutor
from config import CONFIG as _CONFIG_INICIAL, PREFIJO_TODAS
from ordenes import (
    GRACIA_HORA_PASADA,
    agrupar_por_hora,
    expandir_lote,
    hora_objetivo,
    parse_cierre,
    parse_orden,
    resolver_cuentas,
)

log = logging.getLogger("bot_futuros.listener")

BASE_DIR = Path(__file__).parent

STOP_COMMANDS = {"stop", "parar", "cancelar", "cancel", "alto"}
UPDATE_COMMANDS = {"update", "actualizar"}
CHATID_COMMANDS = {"chatid", "chat id", "id"}

# Margen antes de algo programado dentro del cual NO se actualiza: reiniciar
# el listener descartaria por vencido lo que caiga justo en ese hueco.
MINUTOS_DE_GUARDA_UPDATE = 10

# Lo programado que todavia no se ejecuto. Sin esto un reinicio lo perdia en
# silencio, y un cierre que no se dispara deja la posicion abierta.
ARCHIVO_PROGRAMADAS = BASE_DIR / "programadas.json"

# Telegram guarda los updates 24 h y los entrega TODOS al reconectar: una
# orden escrita hace horas se colocaria al precio de AHORA. Mismo criterio
# que la gracia de la hora pasada: un solo "esto todavia es reciente".
EDAD_MAXIMA_MENSAJE = GRACIA_HORA_PASADA

REINTENTOS_RESPUESTA = 4
SEGUNDOS_ENTRE_SELLOS = 30

# Sellos de salud que lee el latido (ver avisos.Latido).
_VISTO_LOOP = None
_ULTIMO_UPDATE = None

# Solo para que asyncio no recolecte las tareas mientras duermen.
_TAREAS_PROGRAMADAS: set = set()

# --- Configuracion efectiva --------------------------------------------------
# Viven como globales del modulo para que los tests las cambien con
# aplicar_config() sin depender del config.local.json de la maquina.
CONFIG = None
PREFIJO = None
BOT_TOKEN = ""
ALLOWED_USER_IDS = frozenset()
ALLOWED_USER_ID = 0
MACHINE_ID = ""
EJECUTOR = None


def aplicar_config(cfg, nuevo_ejecutor=None):
    """Deja `cfg` como la config efectiva del listener (y su ejecutor)."""
    global CONFIG, PREFIJO, BOT_TOKEN, ALLOWED_USER_IDS, ALLOWED_USER_ID
    global MACHINE_ID, EJECUTOR
    CONFIG = cfg
    PREFIJO = cfg.prefijo_telegram
    BOT_TOKEN = cfg.telegram_bot_token
    ALLOWED_USER_IDS = cfg.telegram_allowed_user_ids
    ALLOWED_USER_ID = cfg.telegram_allowed_user_id
    MACHINE_ID = cfg.machine_id
    EJECUTOR = nuevo_ejecutor if nuevo_ejecutor is not None else ejecutor.crear_ejecutor(cfg)


aplicar_config(_CONFIG_INICIAL)


# --- Ruteo por prefijo ---------------------------------------------------------

def destinatarios(linea: str):
    """Parte una linea en (destinatarios, resto).

        "FUT1 MNQ buy 1 40 80"  -> ({"FUT1"}, "MNQ buy 1 40 80")
        "FUT1,FUT3 close"       -> ({"FUT1", "FUT3"}, "close")
        "MNQ buy 1 40 80"       -> ({"MNQ"}, "buy 1 40 80")

    El ultimo caso es a proposito: "MNQ" no es el prefijo de nadie, asi que
    cae en "no es para mi" y ninguna maquina necesita conocer a las otras.
    """
    partes = linea.strip().split(None, 1)
    if not partes:
        return None, ""
    cabeza = partes[0].upper()
    resto = partes[1] if len(partes) > 1 else ""
    if re.fullmatch(r"[A-Z0-9]+(?:,[A-Z0-9]+)*", cabeza):
        return {p for p in cabeza.split(",") if p}, resto
    return None, linea.strip()


def es_para_mi(destinos) -> bool:
    """Sin prefijo configurado NO se acepta nada (en MT5 se aceptaba todo; aca
    el prefijo es obligatorio y main() no arranca sin el)."""
    if not PREFIJO or not destinos:
        return False
    return PREFIJO in destinos or PREFIJO_TODAS in destinos


def lineas_para_mi(texto: str) -> list:
    """Las lineas dirigidas a esta maquina, ya sin el prefijo. El filtro es
    por LINEA: un mensaje puede repartir trabajo a varias maquinas."""
    salida = []
    for linea in texto.strip().splitlines():
        linea = linea.strip()
        if not linea:
            continue
        destinos, resto = destinatarios(linea)
        if es_para_mi(destinos) and resto.strip():
            salida.append(resto.strip())
    return salida


# El "stop" tiene su propio handler, que no hace cola detras de las ordenes.
# Acepta prefijo opcional; el pelado matchea a proposito (boton de panico).
REGEX_STOP = re.compile(
    r"^\s*(?:(?P<destinos>[A-Za-z0-9]+(?:,[A-Za-z0-9]+)*)\s+)?"
    r"(?:" + "|".join(sorted(STOP_COMMANDS)) + r")\s*$",
    re.IGNORECASE)

REGEX_UPDATE = re.compile(
    r"^\s*(?:" + "|".join(sorted(UPDATE_COMMANDS)) + r")\s*$", re.IGNORECASE)


# --- Utilidades ----------------------------------------------------------------

def _quien(update) -> str:
    """Quien mando el mensaje, para el log: nombre (puede cambiar) e id (no)."""
    u = getattr(update, "effective_user", None)
    if u is None:
        return "desconocido"
    nombre = (getattr(u, "username", None)
              or getattr(u, "full_name", None)
              or getattr(u, "first_name", None)
              or "sin nombre")
    return f"{nombre} (id {u.id})"


def _modo() -> str:
    return "DRY_RUN" if EJECUTOR.dry_run else "EN VIVO"


def _estado_de(r) -> str:
    """Texto corto del estado de un ResultadoOrden."""
    if r.omitida:
        return f"NO se intento ({r.detalle})"
    if r.error:
        return f"ERROR: {r.error} - revisa la plataforma"
    if r.confirmado is None:
        return f"no se hizo clic ({r.detalle or 'dry_run'})"
    if not r.confirmado:
        return "NO confirmada - revisa la plataforma"
    if r.sin_proteccion:
        return (f"confirmada PERO SIN SL/TP CONFIRMADO ({r.sin_proteccion}) - "
                f"PONELO A MANO YA")
    return "confirmada"


def _resumen(resultados: list) -> str:
    return "\n".join(f"{r.etiqueta}: {_estado_de(r)} ({r.elapsed:.1f}s)"
                     for r in resultados)


def _falta(segundos: float) -> str:
    minutos = int(segundos // 60)
    if minutos < 60:
        return f"{minutos} min"
    return f"{minutos // 60}h {minutos % 60}min"


def _etiqueta_de_lote(ordenes: list) -> str:
    if len(ordenes) == 1:
        o = ordenes[0]
        cuenta = f" en {o['cuenta']}" if o.get("cuenta") else ""
        return f"{o['simbolo']} {o['accion']} {o['contratos']}{cuenta}"
    return f"{len(ordenes)} orden(es)"


def _etiqueta_de_cierre(cierre: dict) -> str:
    if cierre["modo"] == "todas":
        return "cierre de TODAS las cuentas"
    if cierre["modo"] == "cuenta":
        return f"cierre de {cierre['cuenta']}"
    return "cierre de la cuenta por defecto"


def _cuentas_del_cierre(cierre: dict) -> list:
    """Alias que toca un cierre. Levanta ValueError si no se puede saber."""
    if cierre["modo"] == "todas":
        return resolver_cuentas("all", CONFIG)
    if cierre["modo"] == "cuenta":
        return resolver_cuentas(cierre["cuenta"], CONFIG)
    return resolver_cuentas(None, CONFIG)


def _antiguedad_del_mensaje(update):
    """Cuanto hace que se ESCRIBIO el mensaje, o None si no se puede saber.

    `message.date` viene en UTC con tzinfo: se compara contra un ahora en
    UTC (mezclar con la hora local daria un desfase igual al huso y se
    descartaria todo). Ante cualquier duda, None: es una red de seguridad, no
    puede ser el motivo por el que el bot deja de operar.
    """
    try:
        fecha = getattr(update.message, "date", None)
        if fecha is None:
            return None
        ahora = datetime.now(fecha.tzinfo) if fecha.tzinfo else datetime.now()
        return ahora - fecha
    except Exception:
        log.exception("No se pudo calcular la antiguedad del mensaje; se ignora.")
        return None


def _responder_a(bot_tg, chat_id: int):
    """Funcion async para contestarle a ese chat, con reintentos.

    Se usa en vez de update.message.reply_text porque una tarea rearmada del
    disco tras reiniciar solo tiene el chat_id. Se reintenta porque suele ser
    la UNICA forma de enterarse de como salio una orden.
    """
    async def responder(texto: str):
        for intento in range(REINTENTOS_RESPUESTA):
            try:
                await bot_tg.send_message(chat_id=chat_id, text=texto)
                if intento:
                    log.info(f"Respuesta enviada tras {intento} reintento(s).")
                return
            except Exception:
                if intento == REINTENTOS_RESPUESTA - 1:
                    log.exception(
                        f"No se pudo responder por Telegram tras "
                        f"{REINTENTOS_RESPUESTA} intentos. Mensaje perdido: {texto!r}")
                else:
                    await asyncio.sleep(2 ** intento)
    return responder


# --- Salud ---------------------------------------------------------------------

async def _sellar_loop(app):
    """Deja constancia, desde ADENTRO del loop, de que el loop gira."""
    global _VISTO_LOOP
    while True:
        _VISTO_LOOP = time.monotonic()
        await asyncio.sleep(SEGUNDOS_ENTRE_SELLOS)


def _estado_para_latido() -> list:
    """Lineas de estado para el latido. Sin efectos: corre en otro hilo."""
    lineas = [_modo(), f"{len(CONFIG.cuentas)} cuenta(s)"]
    try:
        lineas.extend(EJECUTOR.salud())
    except Exception as e:
        lineas.append(f"salud del ejecutor ilegible ({type(e).__name__})")
    if CONFIG.advertencias:
        lineas.append(f"{len(CONFIG.advertencias)} advertencia(s) de config")
    return lineas


async def _error_de_telegram(update, context):
    """Clasifica los errores de PTB: un corte de red no es un problema del bot
    (se cuenta y sale en el latido); lo demas si es un error.

    `update` llega en None en los fallos de polling: no se le toca nada.
    """
    error = getattr(context, "error", None)
    if isinstance(error, (telegram_error.NetworkError, telegram_error.TimedOut)):
        env = avisos.enviador()
        if env is not None:
            env.cortes_de_red += 1
        log.warning("Telegram no responde (%s): %s", type(error).__name__, error)
        return
    log.error("Error en el manejo de un update de Telegram: %s", error, exc_info=error)


# --- Programadas ---------------------------------------------------------------

def _leer_programadas() -> list:
    """Lo que haya en disco, o [] si no hay o esta roto (un archivo corrupto
    no puede tumbar el arranque)."""
    if not ARCHIVO_PROGRAMADAS.exists():
        return []
    try:
        datos = json.loads(ARCHIVO_PROGRAMADAS.read_text(encoding="utf-8"))
        return datos if isinstance(datos, list) else []
    except (OSError, ValueError):
        log.exception(f"No se pudo leer {ARCHIVO_PROGRAMADAS.name}; se ignora.")
        return []


def _escribir_programadas(entradas: list):
    try:
        ARCHIVO_PROGRAMADAS.write_text(
            json.dumps(entradas, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        log.exception(f"No se pudo escribir {ARCHIVO_PROGRAMADAS.name}")


def _guardar_programada(entrada: dict):
    _escribir_programadas(_leer_programadas() + [entrada])


def _olvidar_programada(id_tarea: str):
    _escribir_programadas([e for e in _leer_programadas() if e.get("id") != id_tarea])


def _programadas_cerca(minutos: int) -> list:
    cerca = []
    limite = datetime.now() + timedelta(minutes=minutos)
    for entrada in _leer_programadas():
        try:
            cuando = datetime.fromisoformat(entrada["cuando"])
        except (KeyError, ValueError, TypeError):
            continue
        if cuando <= limite:
            cerca.append(f"{entrada.get('etiqueta', '?')} a las {cuando:%H:%M}")
    return cerca


async def _programar(responder, chat_id: int, hora: str, spec: dict, etiqueta: str):
    """Confirma de inmediato y deja una tarea que ejecuta `spec` a `hora`.

    Vive DENTRO del listener (y no en una tarea de Windows aparte) para que
    el candado de operacion la serialice contra lo que este corriendo.
    """
    objetivo = hora_objetivo(hora)
    id_tarea = uuid.uuid4().hex[:8]
    _guardar_programada({
        "id": id_tarea,
        "cuando": objetivo.isoformat(timespec="seconds"),
        "etiqueta": etiqueta,
        "chat_id": chat_id,
        "spec": spec,
    })
    espera = max(0.0, (objetivo - datetime.now()).total_seconds())
    log.info(f"Programado {etiqueta} para {objetivo:%d/%m %H:%M} (en {_falta(espera)}).")
    await responder(
        f"Programado: {etiqueta} a las {objetivo:%H:%M} del {objetivo:%d/%m} "
        f"(en {_falta(espera)}). Modo: {_modo()}.")
    _armar(responder, objetivo, spec, etiqueta, id_tarea)


def _armar(responder, objetivo: datetime, spec: dict, etiqueta: str, id_tarea: str):
    async def _tarea():
        try:
            await asyncio.sleep(max(0.0, (objetivo - datetime.now()).total_seconds()))
            await responder(f"Son las {objetivo:%H:%M}: ejecutando {etiqueta}...")
            await _ejecutar_spec(responder, spec)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception(f"Error en la operacion programada de las {objetivo:%H:%M}")
            await responder(f"Error al ejecutar: {e}")
        finally:
            # Ya no esta pendiente, pase lo que pase.
            _olvidar_programada(id_tarea)

    tarea = asyncio.create_task(_tarea())
    _TAREAS_PROGRAMADAS.add(tarea)
    tarea.add_done_callback(_TAREAS_PROGRAMADAS.discard)


async def _rearmar_programadas(app):
    """Al arrancar, rearma lo pendiente. Lo vencido NO se ejecuta: se avisa.

    Tambien arranca el sello del loop, que tiene que vivir DENTRO del loop.
    """
    asyncio.create_task(_sellar_loop(app))

    pendientes = _leer_programadas()
    if not pendientes:
        return
    ahora = datetime.now()
    siguen = []
    for entrada in pendientes:
        try:
            cuando = datetime.fromisoformat(entrada["cuando"])
            responder = _responder_a(app.bot, entrada["chat_id"])
            etiqueta = entrada["etiqueta"]
            spec = entrada["spec"]
            id_tarea = entrada["id"]
        except (KeyError, ValueError, TypeError):
            log.warning(f"Entrada programada ilegible, se descarta: {entrada!r}")
            continue

        if cuando <= ahora:
            log.warning(
                f"'{etiqueta}' estaba programado para {cuando:%d/%m %H:%M} y el "
                "listener no estaba corriendo a esa hora; NO se ejecuta.")
            await responder(
                f"AVISO: '{etiqueta}' estaba programado para las {cuando:%H:%M} "
                f"del {cuando:%d/%m}, pero el listener no estaba corriendo a esa "
                "hora. NO se ejecuto. Si todavia lo queres, mandalo de nuevo.")
            continue

        siguen.append(entrada)
        espera = (cuando - ahora).total_seconds()
        log.info(f"Rearmado '{etiqueta}' para {cuando:%d/%m %H:%M} (en {_falta(espera)}).")
        await responder(
            f"Retomado tras reiniciar: '{etiqueta}' sigue programado para las "
            f"{cuando:%H:%M} del {cuando:%d/%m} (en {_falta(espera)}).")
        _armar(responder, cuando, spec, etiqueta, id_tarea)

    _escribir_programadas(siguen)


# --- Ejecucion -----------------------------------------------------------------

async def _ejecutar_spec(responder, spec: dict):
    """Ejecuta una operacion descrita por un dict SERIALIZABLE (las
    programadas van a disco, asi que no pueden ser un callable)."""
    tipo = spec.get("tipo")
    if tipo == "cierre":
        await _ejecutar_cierre(responder, spec["cierre"])
    elif tipo == "lote":
        await _ejecutar_lote(responder, spec["ordenes"])
    else:
        await responder(f"Operacion programada desconocida: {tipo!r}")


async def _ejecutar_lote(responder, ordenes: list):
    """Valida OTRA VEZ contra la config (una programada pudo sobrevivir a un
    update que cambio cuentas o simbolos) y coloca."""
    try:
        trabajos = expandir_lote(ordenes, CONFIG)
    except ValueError as e:
        await responder(f"Error: {e}\nNo se coloco nada.")
        return
    try:
        resultados = await asyncio.to_thread(EJECUTOR.colocar_lote, trabajos)
        await responder(f"Listo ({_modo()}).\n{_resumen(resultados)}")
    except Exception as e:
        log.exception("Error al procesar el lote de ordenes")
        await responder(f"Error al ejecutar: {e}")


async def _ejecutar_cierre(responder, cierre: dict):
    try:
        aliases = _cuentas_del_cierre(cierre)
    except ValueError as e:
        await responder(f"Error: {e}\nNo se cerro nada.")
        return
    try:
        await responder(f"Cerrando posiciones en {', '.join(aliases)} ({_modo()})...")
        resultados = await asyncio.to_thread(EJECUTOR.cerrar_cuentas, aliases)
        lineas = []
        for alias in aliases:
            if alias not in resultados:
                lineas.append(f"{alias}: NO se intento (abortado con stop)")
            elif resultados[alias] is None:
                lineas.append(f"{alias}: ERROR - revisa la plataforma")
            else:
                lineas.append(f"{alias}: {resultados[alias]} posicion(es) cerrada(s)")
        await responder("Listo.\n" + "\n".join(lineas))
    except Exception as e:
        log.exception(f"Error al cerrar posiciones ({cierre})")
        await responder(f"Error al cerrar: {e}")


async def _parar_todo(responder):
    """Corta lo que este en curso y cancela lo agendado (memoria y disco)."""
    ejecutor.solicitar_aborto()

    agendadas = len(_leer_programadas())
    for tarea in list(_TAREAS_PROGRAMADAS):
        tarea.cancel()
    _TAREAS_PROGRAMADAS.clear()
    _escribir_programadas([])

    await responder(
        "PARANDO.\n"
        "- Lo que este en curso se corta en el proximo punto seguro: si "
        "todavia no se hizo el clic de Comprar/Vender, la orden NO se coloca.\n"
        "- Si mandaste una LISTA de ordenes, no se coloca ninguna de las que "
        "falten.\n"
        f"- Canceladas {agendadas} operacion(es) programada(s).\n"
        "Ojo: lo que YA se ejecuto no se deshace.")
    log.warning(f"STOP pedido por Telegram. Programadas canceladas: {agendadas}.")

    # La bandera se baja cuando de verdad no queda nada corriendo.
    await asyncio.to_thread(ejecutor.limpiar_aborto_cuando_libre)
    await responder("Listo, ya podes mandar ordenes de nuevo.")


async def _lanzar_actualizacion(responder, chat_id: int) -> None:
    """Dispara actualizar.ps1 y contesta. NO espera: el script mata a este
    proceso. El que avisa como salio es el propio script."""
    script = BASE_DIR / "actualizar.ps1"
    if not script.exists():
        await responder(f"No encuentro {script.name}; no se actualiza nada.")
        return

    if ejecutor.hay_operacion_en_curso():
        await responder(
            "Hay una operacion en curso. NO se actualiza (pararia el bot con "
            "una orden a medias). Proba de nuevo cuando termine.")
        return

    cerca = _programadas_cerca(MINUTOS_DE_GUARDA_UPDATE)
    if cerca:
        await responder(
            f"Hay {len(cerca)} operacion(es) programada(s) dentro de "
            f"{MINUTOS_DE_GUARDA_UPDATE} min ({', '.join(cerca)}). NO se "
            "actualiza: reiniciar ahora las descartaria por vencidas.")
        return

    # Mismas banderas que MT5, elegidas midiendo: CREATE_NO_WINDOW (con
    # DETACHED_PROCESS powershell no arrancaba), fuera del job de la tarea
    # programada (si no, el Stop-ScheduledTask del script lo mata a el) y
    # grupo nuevo (no comparte los Ctrl+C de este proceso).
    SIN_VENTANA, GRUPO_NUEVO, FUERA_DEL_JOB = 0x08000000, 0x00000200, 0x01000000
    comando = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", str(script), "-ChatId", str(chat_id)]
    try:
        subprocess.Popen(comando, cwd=str(BASE_DIR), close_fds=True,
                         creationflags=SIN_VENTANA | GRUPO_NUEVO | FUERA_DEL_JOB)
    except OSError:
        log.warning("El job no permite breakaway; se lanza sin esa bandera.")
        subprocess.Popen(comando, cwd=str(BASE_DIR), close_fds=True,
                         creationflags=SIN_VENTANA | GRUPO_NUEVO)
    log.info("Actualizacion lanzada (actualizar.ps1, desacoplada).")
    await responder(
        "Actualizando: paro el bot, traigo el codigo nuevo, corro los tests y "
        "vuelvo a levantarlo. Te aviso como salio en un minuto. Si los tests "
        "fallan, se revierte solo. Detalle en logs/actualizar-AAAA-MM-DD.log.")


# --- Handlers ------------------------------------------------------------------

async def on_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler EXCLUSIVO del "stop", registrado con block=False.

    Con un solo handler el "stop" hacia cola detras de la orden que se estaba
    colocando y llegaba cuando ya no quedaba nada que frenar.
    """
    user_id = update.effective_user.id if update.effective_user else None
    if user_id not in ALLOWED_USER_IDS:
        log.warning(f"STOP ignorado de usuario no autorizado: {_quien(update)}")
        return
    log.info(f"STOP pedido por {_quien(update)}.")

    m = REGEX_STOP.match(update.message.text or "")
    destinos = m.group("destinos") if m else None
    if destinos:
        pedidos = {d.upper() for d in destinos.split(",") if d}
        if not (PREFIJO in pedidos or PREFIJO_TODAS in pedidos):
            log.info(f"STOP dirigido a {sorted(pedidos)}; no es para {PREFIJO}.")
            return

    await _parar_todo(_responder_a(context.bot, update.effective_chat.id))


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Sello de RECEPCION antes de cualquier filtro: mide si el camino de
    # entrada funciona, no si el mensaje era para esta maquina.
    global _ULTIMO_UPDATE
    _ULTIMO_UPDATE = time.monotonic()

    user_id = update.effective_user.id if update.effective_user else None

    if ALLOWED_USER_ID == 0:
        await update.message.reply_text(
            f"Usuario autorizado no configurado. Tu user id es: {user_id}. "
            "Agregalo en \"telegram_allowed_user_id\" (config.json para toda la "
            "flota) y reinicia el listener.")
        return

    if user_id not in ALLOWED_USER_IDS:
        log.warning(f"Mensaje ignorado de usuario no autorizado: {_quien(update)}")
        return

    crudo = update.message.text or ""
    chat_id = update.effective_chat.id
    responder = _responder_a(context.bot, chat_id)

    if REGEX_STOP.match(crudo):
        return   # lo atiende on_stop, que no hace cola detras de las ordenes

    # Va DESPUES del stop a proposito: un stop viejo se sigue respetando.
    vieja = _antiguedad_del_mensaje(update)
    if vieja is not None and vieja > EDAD_MAXIMA_MENSAJE:
        minutos = int(vieja.total_seconds() // 60)
        if lineas_para_mi(crudo):
            log.warning(
                f"Mensaje de hace {minutos} min descartado (limite: "
                f"{int(EDAD_MAXIMA_MENSAJE.total_seconds() // 60)} min). "
                "Probablemente estuvo en la cola de Telegram con el listener caido.")
            await responder(
                f"Descartado: este mensaje tiene {minutos} min de antiguedad y "
                "estuvo esperando en la cola de Telegram (el listener estaba "
                "caido). NO se ejecuto nada. Si lo seguis queriendo, mandalo "
                "de nuevo.")
        return

    mias = lineas_para_mi(crudo)
    if not mias:
        log.info(f"Mensaje sin lineas para {PREFIJO}; ignorado.")
        return
    text = "\n".join(mias)
    log.info(f"Mensaje aceptado de {_quien(update)}: {text!r}")

    if text.strip().lower() in CHATID_COMMANDS:
        await responder(f"chat_id de este chat: {chat_id}")
        return

    if REGEX_UPDATE.match(text):
        await _lanzar_actualizacion(responder, chat_id)
        return

    cierre = parse_cierre(text) if len(mias) == 1 else None
    if cierre is not None:
        try:
            _cuentas_del_cierre(cierre)       # validar ya, no a la hora
        except ValueError as e:
            await responder(f"Error: {e}\nNo se cerro ni programo nada.")
            return
        etiqueta = _etiqueta_de_cierre(cierre)
        if cierre["at"]:
            await _programar(responder, chat_id, cierre["at"],
                             {"tipo": "cierre", "cierre": cierre}, etiqueta)
            return
        await _ejecutar_cierre(responder, cierre)
        return

    # Cada linea es una orden independiente. Si UNA no se entiende o no es
    # valida para esta maquina, no se coloca ni se programa NINGUNA.
    ordenes = []
    for n, linea in enumerate(mias, start=1):
        try:
            ordenes.append(parse_orden(linea))
        except ValueError as e:
            prefijo_error = f"linea {n}: " if len(mias) > 1 else ""
            await responder(f"Error: {prefijo_error}{e}\nNo se coloco ni programo nada.")
            return
    try:
        expandir_lote(ordenes, CONFIG)
    except ValueError as e:
        await responder(f"Error: {e}\nNo se coloco ni programo nada.")
        return

    # Primero se agenda y despues se ejecuta, para que el agendado no espere
    # detras de las inmediatas.
    grupos = agrupar_por_hora(ordenes)
    inmediatas = grupos.pop(None, [])
    for hora, grupo in grupos.items():
        await _programar(responder, chat_id, hora,
                         {"tipo": "lote", "ordenes": grupo}, _etiqueta_de_lote(grupo))
    if inmediatas:
        await responder(f"Recibida(s) {len(inmediatas)} orden(es). Procesando "
                        f"({_modo()})...")
        await _ejecutar_lote(responder, inmediatas)


# --- Arranque ------------------------------------------------------------------

def _configurar_logging():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    # httpx loguea en INFO cada URL, y en la API de Telegram el token va
    # DENTRO de la URL.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main():
    _configurar_logging()
    log.info(f"Arrancando listener de futuros: {CONFIG.resumen()}")
    for advertencia in CONFIG.advertencias:
        log.warning(f"CONFIG: {advertencia}")
    if EJECUTOR.dry_run:
        log.warning("MODO: DRY_RUN - llena las ordenes pero NO hace clic en "
                    "Comprar/Vender.")
    else:
        log.warning("MODO: EN VIVO - coloca ordenes REALES.")
    log.info(f"Ejecutor: {EJECUTOR.nombre}. Cuentas: "
             f"{', '.join(sorted(CONFIG.cuentas)) or 'ninguna'}. Por defecto: "
             f"{CONFIG.cuenta_por_defecto or '-'}.")

    if not BOT_TOKEN:
        raise SystemExit(
            "Falta el token del bot. Ponelo como \"telegram_bot_token\" en "
            "config.local.json (copia config.example.json o usa configurar.bat).")
    if not PREFIJO:
        raise SystemExit(
            "Falta \"prefijo_telegram\" en config.local.json. En este bot es "
            "OBLIGATORIO: sin prefijo no se ejecuta ninguna linea del grupo.")
    if not ALLOWED_USER_IDS:
        log.warning("No hay usuario autorizado: el bot va a rechazar todos los "
                    "mensajes y contestarle a cada uno su user id.")
    else:
        log.info(f"Usuarios autorizados ({len(ALLOWED_USER_IDS)}): "
                 + ", ".join(str(i) for i in sorted(ALLOWED_USER_IDS)))

    # concurrent_updates(True) NO es cosmetico: sin esto PTB despacha los
    # updates de a uno y el "stop" queda en cola detras del lote que se esta
    # colocando (medido en MT5: el aborto llegaba 0.6 s DESPUES de la ultima
    # orden). Es seguro para las ordenes: el candado de operacion las
    # serializa.
    app = (Application.builder()
           .token(BOT_TOKEN)
           .concurrent_updates(True)
           .post_init(_rearmar_programadas)
           .build())

    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(REGEX_STOP), on_stop, block=False))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_error_handler(_error_de_telegram)

    # Envuelto: un monitor que puede tumbar lo que monitorea es peor que no
    # tener monitor (en MT5, un NameError aca dejo sin bot a la flota).
    try:
        enviador = avisos.activar(BOT_TOKEN, CONFIG.telegram_chat_errores, MACHINE_ID)
    except Exception:
        log.exception("No se pudieron activar los avisos de error; el bot sigue igual.")
        enviador = None
    if enviador is not None:
        try:
            avisos.Latido(enviador, {
                "loop": lambda: _VISTO_LOOP,
                "recepcion": lambda: _ULTIMO_UPDATE,
                "estado": _estado_para_latido,
            }).start()
        except Exception:
            log.exception("No se pudo arrancar el latido; el bot sigue igual.")

    log.info(f"Bot de Telegram escuchando (maquina '{MACHINE_ID}', prefijo {PREFIJO})...")
    # bootstrap_retries=-1: una PC que enciende sola arranca la tarea antes
    # de que haya red; con el default, un timeout al arrancar mata el proceso.
    try:
        app.run_polling(bootstrap_retries=-1)
    finally:
        EJECUTOR.cerrar()


if __name__ == "__main__":
    main()
