"""
Formato de las ordenes de futuros y su validacion.

Sin Telegram ni navegador: todo lo de aca se prueba con datos.

Una linea de orden (ya sin el prefijo de la maquina):

    SIMBOLO buy|sell CONTRATOS SL_TICKS TP_TICKS [CUENTA] [HH:MM]

    MNQ buy 1 40 80                -> cuenta por defecto, ya
    MNQ buy 1 40 80 TS1            -> esa cuenta, ya
    MNQ buy 1 40 80 TS1,TV2        -> esas dos (sin espacios)
    MNQ buy 1 40 80 all            -> todas las cuentas de esta maquina
    MNQ sell 2 30 60 TS1 14:30     -> esa cuenta, a las 14:30 (hora local)

CONTRATOS, SL_TICKS y TP_TICKS son enteros estrictos: "1.0" o "1,5" se
rechazan en vez de redondearse. Un contrato de futuros no se fracciona, y
redondear en silencio es operar una cantidad que nadie escribio.

SIMBOLO es la raiz (MNQ). El contrato concreto (MNQZ6) sale de la tabla
"simbolos" de cada plataforma en config.json.

Cierres:

    close | cerrar                       -> cuenta por defecto
    close TS1 | TS1 close                -> esa cuenta
    close all | close all accounts       -> todas las de esta maquina
    cerrar todas | cerrar todas las cuentas
    ... con HH:MM opcional al final
"""

import re
from datetime import datetime, timedelta
from decimal import Decimal

ACCIONES = {"buy", "sell"}

# Hora en formato 24h: 9:30, 09:30, 14:30, 23:59. Una cuenta nunca lleva ":".
HORA_PATTERN = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

ENTERO_PATTERN = re.compile(r"^\d+$")
SIMBOLO_PATTERN = re.compile(r"^[A-Za-z0-9]{1,10}$")
CUENTA_SPEC_PATTERN = re.compile(r"^[A-Za-z0-9_-]+(?:,[A-Za-z0-9_-]+)*$")

# En el campo CUENTA: "todas las de esta maquina".
PALABRAS_TODAS = {"all", "todas", "todo"}

CIERRE_PALABRAS = {"close", "cerrar"}
CIERRE_TODAS = {"close all", "close all accounts", "cerrar todas",
                "cerrar todas las cuentas"}

# Margen para una hora que "ya paso": mandar 14:30 a las 14:32 significa
# "ya", no "mañana". Mismo valor que en MT5, donde 60 s resulto poco.
GRACIA_HORA_PASADA = timedelta(minutes=10)

FORMATO_AYUDA = (
    "Formato: SIMBOLO buy|sell CONTRATOS SL_TICKS TP_TICKS [CUENTA] [HH:MM] "
    "(ejemplo: MNQ buy 1 40 80 | ... TS1 | ... all | ... TS1,TV2 | ... TS1 14:30). "
    "CONTRATOS, SL y TP son enteros, sin decimales."
)


def _entero_positivo(texto: str, nombre: str) -> int:
    if not ENTERO_PATTERN.match(texto):
        raise ValueError(
            f"{nombre} tiene que ser un numero entero, sin decimales ni signo: "
            f"{texto!r}. {FORMATO_AYUDA}")
    numero = int(texto)
    if numero <= 0:
        raise ValueError(f"{nombre} tiene que ser mayor a 0: {texto!r}.")
    return numero


def parse_orden(texto: str) -> dict:
    """Convierte una linea en un dict de orden SERIALIZABLE (va a disco si se
    programa). Levanta ValueError con un mensaje para el operador."""
    partes = texto.strip().split()
    if not 5 <= len(partes) <= 7:
        raise ValueError(f"Formato invalido. {FORMATO_AYUDA}")

    hora = None
    if HORA_PATTERN.match(partes[-1]):
        hora = partes[-1]
        partes = partes[:-1]
    if len(partes) not in (5, 6):
        raise ValueError(f"Formato invalido. {FORMATO_AYUDA}")

    simbolo, accion, contratos, sl, tp = partes[:5]
    if not SIMBOLO_PATTERN.match(simbolo):
        raise ValueError(f"Simbolo invalido: {simbolo!r}. {FORMATO_AYUDA}")
    accion = accion.lower()
    if accion not in ACCIONES:
        raise ValueError(f"La accion tiene que ser buy o sell, no {accion!r}.")

    cuenta = None
    if len(partes) == 6:
        cuenta = partes[5]
        if not CUENTA_SPEC_PATTERN.match(cuenta):
            # Tambien cae aca una hora invalida como "25:00": tiene ":".
            raise ValueError(
                f"CUENTA invalida: {cuenta!r} (alias de cuenta, varios separados "
                f"por coma sin espacios, o all). Si era una hora, va en formato "
                f"24h HH:MM. {FORMATO_AYUDA}")

    return {
        "simbolo": simbolo.upper(),
        "accion": accion,
        "contratos": _entero_positivo(contratos, "CONTRATOS"),
        "sl_ticks": _entero_positivo(sl, "SL_TICKS"),
        "tp_ticks": _entero_positivo(tp, "TP_TICKS"),
        "cuenta": cuenta,
        "at": hora,
    }


def parse_cierre(texto: str) -> dict | None:
    """Interpreta un comando de cierre, o devuelve None si no lo es.

        close                 -> {"modo": "actual", "cuenta": None, "at": None}
        close TS1 / TS1 close -> {"modo": "cuenta", "cuenta": "TS1", ...}
        close all accounts    -> {"modo": "todas", ...}
        ... 15:30             -> con "at": "15:30"

    Los dos ordenes valen ("close TS1" y "TS1 close"): en MT5 el operador
    escribio la cuenta primero dos veces, el cierre no se entendio y las
    posiciones quedaron abiertas creyendo que estaba programado.
    """
    partes = texto.strip().split()
    if not partes:
        return None

    hora = None
    if HORA_PATTERN.match(partes[-1]):
        hora = partes[-1]
        partes = partes[:-1]
        if not partes:
            return None

    resto = " ".join(p.lower() for p in partes)
    if resto in CIERRE_TODAS:
        return {"modo": "todas", "cuenta": None, "at": hora}
    if resto in CIERRE_PALABRAS:
        return {"modo": "actual", "cuenta": None, "at": hora}
    if len(partes) == 2:
        a, b = partes
        if a.lower() in CIERRE_PALABRAS and CUENTA_SPEC_PATTERN.match(b):
            return {"modo": "cuenta", "cuenta": b.upper(), "at": hora}
        if b.lower() in CIERRE_PALABRAS and CUENTA_SPEC_PATTERN.match(a):
            return {"modo": "cuenta", "cuenta": a.upper(), "at": hora}
    return None


def hora_objetivo(hora: str, ahora: datetime | None = None) -> datetime:
    """"HH:MM" -> el proximo datetime local que corresponda.

    Si ya paso hoy, mañana; salvo que haya pasado hace menos de
    GRACIA_HORA_PASADA, medido desde el FIN del minuto pedido.
    """
    ahora = ahora or datetime.now()
    h, m = (int(x) for x in hora.split(":"))
    objetivo = ahora.replace(hour=h, minute=m, second=0, microsecond=0)
    if objetivo + timedelta(minutes=1) + GRACIA_HORA_PASADA <= ahora:
        objetivo += timedelta(days=1)
    return objetivo


def agrupar_por_hora(ordenes: list) -> dict:
    """{None|"HH:MM": [ordenes]} preservando el orden de aparicion."""
    grupos: dict = {}
    for o in ordenes:
        grupos.setdefault(o.get("at"), []).append(o)
    return grupos


def resolver_cuentas(spec: str | None, config) -> list:
    """El campo CUENTA -> lista de alias de cuentas de ESTA maquina.

    None -> la cuenta por defecto; "all" -> todas; "TS1,TV2" -> esas. Un
    alias desconocido es un error: operar en "la que este" no es una opcion.
    """
    cuentas = config.cuentas
    if not spec:
        defecto = config.cuenta_por_defecto
        if not defecto:
            raise ValueError(
                f"La orden no nombra CUENTA y esta maquina tiene {len(cuentas)} "
                f"cuenta(s) sin 'cuenta_por_defecto'. Indica la cuenta "
                f"({', '.join(sorted(cuentas)) or 'ninguna configurada'}).")
        return [defecto]
    if spec.strip().lower() in PALABRAS_TODAS:
        if not cuentas:
            raise ValueError("Esta maquina no tiene cuentas configuradas.")
        return list(cuentas)
    aliases = []
    for a in spec.split(","):
        a = a.strip().upper()
        if a and a not in aliases:
            aliases.append(a)
    desconocidas = [a for a in aliases if a not in cuentas]
    if desconocidas:
        raise ValueError(
            f"Cuenta(s) desconocida(s) en esta maquina: {', '.join(desconocidas)} "
            f"(configuradas: {', '.join(sorted(cuentas)) or 'ninguna'}).")
    return aliases


def usd(valor) -> str:
    """Decimal -> texto para escribir en la web y en los mensajes: sin ceros
    de mas ("80", "12.5", "48.75"). Nunca notacion cientifica."""
    texto = f"{Decimal(valor):.2f}"
    return texto.rstrip("0").rstrip(".") if "." in texto else texto


def riesgo_y_ganancia(orden: dict, config) -> tuple:
    """(riesgo_usd, ganancia_usd) de la orden en UNA cuenta, como Decimal:
    ticks x valor del tick x contratos. (None, None) si el simbolo no tiene
    valor del tick y la maquina no tiene tope de riesgo (no hace falta)."""
    try:
        tick = config.valor_tick(orden["simbolo"])
    except ValueError:
        if config.max_riesgo_usd is not None:
            raise          # con tope, sin el valor del tick no se puede controlar
        return None, None
    contratos = Decimal(orden["contratos"])
    return (Decimal(orden["sl_ticks"]) * tick * contratos,
            Decimal(orden["tp_ticks"]) * tick * contratos)


def expandir_orden(orden: dict, config) -> list:
    """Una orden -> un trabajo por cuenta, con el contrato ya traducido.

    Valida todo lo que depende de la config: cuenta conocida, tope de
    contratos de la cuenta, simbolo configurado en su plataforma, y el tope
    de riesgo en USD (max_riesgo_usd), que vale para CADA cuenta: una orden a
    "all" con 5 cuentas arriesga el tope en cada una.
    """
    riesgo, ganancia = riesgo_y_ganancia(orden, config)
    tope = config.max_riesgo_usd
    if tope is not None and riesgo > tope:
        raise ValueError(
            f"Riesgo {usd(riesgo)} USD por cuenta ({orden['sl_ticks']} ticks x "
            f"{usd(config.valor_tick(orden['simbolo']))} USD x {orden['contratos']} "
            f"contrato(s)) supera el maximo de {usd(tope)} USD.")
    trabajos = []
    for alias in resolver_cuentas(orden.get("cuenta"), config):
        cuenta = config.cuentas[alias]
        if orden["contratos"] > cuenta.max_contratos:
            raise ValueError(
                f"{alias}: {orden['contratos']} contrato(s) supera el maximo de "
                f"esa cuenta ({cuenta.max_contratos}).")
        contrato = config.contrato(cuenta.plataforma, orden["simbolo"])
        trabajos.append({
            **orden,
            "cuenta": alias,
            "plataforma": cuenta.plataforma,
            "contrato": contrato,
            # Texto y no Decimal: el trabajo tiene que poder ir a disco (JSON).
            "riesgo_usd": None if riesgo is None else usd(riesgo),
            "ganancia_usd": None if ganancia is None else usd(ganancia),
        })
    return trabajos


def expandir_lote(ordenes: list, config) -> list:
    """Varias ordenes -> lista plana de trabajos. Si UNA no es valida, se
    rechaza todo el lote: colocar la mitad de lo pedido es peor que nada."""
    trabajos = []
    for n, orden in enumerate(ordenes, start=1):
        try:
            trabajos.extend(expandir_orden(orden, config))
        except ValueError as e:
            if len(ordenes) > 1:
                raise ValueError(f"linea {n}: {e}") from e
            raise
    return trabajos


def etiqueta_de(n: int, trabajo: dict) -> str:
    return (f"{n}. {trabajo['simbolo']} {trabajo['accion']} {trabajo['contratos']} "
            f"@ {trabajo['cuenta']}")
