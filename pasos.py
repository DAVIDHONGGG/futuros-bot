"""
Interprete de los pasos que describen una plataforma en config.json, y su
validacion.

Cada plataforma dice COMO capturar una orden con una lista de pasos:

    {"accion": "click",       "selector": "<selector>"}
    {"accion": "escribir",    "selector": "<selector>", "valor": "{contratos}"}
    {"accion": "escribir",    "selector": "<selector>", "valor": "{contrato}", "teclear": true}
                              (tecla por tecla, para autocompletados que no filtran con fill)
    {"accion": "click_texto", "texto": "{contrato}"}
    {"accion": "tecla",       "tecla": "Enter"}                 (a la pagina)
    {"accion": "tecla",       "tecla": "Enter", "selector": "<selector>"}
    {"accion": "esperar",     "ms": 300}
    {"accion": "esperar",     "selector": "<selector>"}         (hasta que se vea)
    {"accion": "repetir",     "selector": "<selector>", "veces": "{contratos_menos_1}"}
                              (N clics; para cantidades que solo tienen +/-)
    {"accion": "marcar",      "selector": "<checkbox>"}
                              (lo deja MARCADO; un click lo desmarcaria si ya lo estaba)
    {"accion": "verificar",   "selector": "<selector>", "contiene": "+{contratos} MARKET"}
    {"accion": "verificar",   "selector": "<input>", "igual": "{riesgo_usd}", "numerico": true}
                              (lee el valor de un input o el texto de otro elemento
                              y CORTA la orden si no coincide: sin clic final)

Los selectores son de Playwright tal cual: CSS ("#qty", "[data-testid=buy]"),
"text=Buy Mkt", "role=button[name=\"Buy\"]", "xpath=...". Se sacan con
`playwright codegen <url>` (ver CLAUDE.md / el reporte de la Etapa 2). Los
SELECTORES no llevan marcadores: lo variable va en "valor", "texto", "veces",
"igual" o "contiene".

Marcadores:
    {simbolo} {contrato} {contratos} {sl_ticks} {tp_ticks} {accion}
    {cuenta_plataforma}   el nombre de la cuenta en la web (config.local.json)
    {cuenta_ultimos4}     sus ultimos 4 caracteres (TopstepX muestra "(...2304)")
    {contratos_menos_1}   para "repetir" el + despues de elegir 1
    {riesgo_usd}          SL_TICKS x valor del tick x CONTRATOS ("80", "12.5")
    {ganancia_usd}        lo mismo con TP_TICKS

Todo lo de aca corre en el hilo del navegador (ver ejecutor_web.py). Antes de
CADA paso se revisa el aborto: un "stop" a mitad de la captura corta ahi.
"""

import re

from config import es_sin_configurar
from ejecutor import chequear_aborto

ACCIONES = {"click", "escribir", "click_texto", "tecla", "esperar", "repetir",
            "marcar", "verificar"}

MARCADORES = {"simbolo", "contrato", "contratos", "sl_ticks", "tp_ticks", "accion",
              "cuenta_plataforma", "cuenta_ultimos4", "contratos_menos_1",
              "riesgo_usd", "ganancia_usd"}
_PATRON_MARCADOR = re.compile(r"\{([a-z0-9_]+)\}")

# Campos de un paso donde se reemplazan marcadores.
CAMPOS_CON_MARCADORES = ("valor", "texto", "veces", "igual", "contiene")

# Tope de "repetir": un numero absurdo es un error de config o de orden, y
# cien clics en un "+" no se deshacen solos.
MAXIMO_REPETICIONES = 100


class VerificacionFallida(Exception):
    """Un paso "verificar" no encontro lo esperado. Se levanta ANTES del clic
    final: la orden se corta sin colocarse."""

# Listas de pasos que puede tener una plataforma. Las obligatorias tienen que
# estar y no vacias.
LISTAS_DE_PASOS = ("pasos_orden", "pasos_seleccionar_cuenta", "pasos_confirmar",
                   "pasos_limpiar")
OBLIGATORIAS = ("url", "sesion_iniciada", "boton_comprar", "boton_vender")


def _problemas_de_pasos(nombre: str, pasos) -> list:
    if not isinstance(pasos, list):
        return [f"'{nombre}' tiene que ser una lista de pasos"]
    problemas = []
    for n, paso in enumerate(pasos, start=1):
        donde = f"{nombre} paso {n}"
        if not isinstance(paso, dict):
            problemas.append(f"{donde}: tiene que ser un objeto")
            continue
        accion = paso.get("accion")
        if accion not in ACCIONES:
            problemas.append(f"{donde}: accion {accion!r} desconocida "
                             f"(validas: {', '.join(sorted(ACCIONES))})")
            continue
        requeridos = {
            "click": ["selector"],
            "escribir": ["selector", "valor"],
            "click_texto": ["texto"],
            "tecla": ["tecla"],
            "esperar": [],
            "repetir": ["selector", "veces"],
            "marcar": ["selector"],
            "verificar": ["selector"],
        }[accion]
        if accion == "verificar" and ("igual" in paso) == ("contiene" in paso):
            problemas.append(f"{donde}: verificar necesita 'igual' o 'contiene' (uno solo)")
        for campo in requeridos:
            if campo not in paso:
                problemas.append(f"{donde}: falta '{campo}'")
            elif es_sin_configurar(paso[campo]):
                problemas.append(f"{donde}: '{campo}' sin configurar ({paso[campo]!r})")
        if accion == "esperar" and "ms" not in paso and "selector" not in paso:
            problemas.append(f"{donde}: esperar necesita 'ms' o 'selector'")
        if "selector" in paso and accion in ("tecla", "esperar") and es_sin_configurar(paso["selector"]):
            problemas.append(f"{donde}: 'selector' sin configurar ({paso['selector']!r})")
        for campo in CAMPOS_CON_MARCADORES:
            for marcador in _PATRON_MARCADOR.findall(str(paso.get(campo, ""))):
                if marcador not in MARCADORES:
                    problemas.append(f"{donde}: marcador {{{marcador}}} desconocido")
    return problemas


def validar_plataforma(nombre: str, datos: dict) -> list:
    """Lista de problemas que impiden operar en esa plataforma ([] = lista).

    Un "TODO" en cualquier dato que se use cuenta como problema: el
    ejecutor se niega a operar ahi en vez de adivinar un selector.
    """
    if not isinstance(datos, dict):
        return [f"{nombre}: no es un objeto"]
    problemas = []
    for clave in OBLIGATORIAS:
        if es_sin_configurar(datos.get(clave)):
            problemas.append(f"'{clave}' sin configurar")
    pasos_orden = datos.get("pasos_orden")
    if not pasos_orden:
        problemas.append("'pasos_orden' vacio o sin configurar")
    for lista in LISTAS_DE_PASOS:
        if lista in datos:
            problemas.extend(_problemas_de_pasos(lista, datos[lista]))
    posiciones = datos.get("posiciones")
    if not isinstance(posiciones, dict):
        problemas.append("'posiciones' sin configurar (hace falta para verificar la orden)")
    else:
        for clave in ("panel", "filas", "cantidad"):
            if es_sin_configurar(posiciones.get(clave)):
                problemas.append(f"'posiciones.{clave}' sin configurar")
        if "texto_fila" in posiciones:
            if es_sin_configurar(posiciones["texto_fila"]):
                problemas.append("'posiciones.texto_fila' sin configurar")
            for marcador in _PATRON_MARCADOR.findall(str(posiciones["texto_fila"])):
                if marcador not in MARCADORES:
                    problemas.append(f"posiciones.texto_fila: marcador {{{marcador}}} desconocido")
    return [f"{nombre}: {p}" for p in problemas]


def valores_de(trabajo: dict, cuenta) -> dict:
    """Los valores con los que se rellenan los marcadores de un trabajo."""
    cuenta_plataforma = getattr(cuenta, "cuenta_plataforma", None)
    return {
        "simbolo": trabajo["simbolo"],
        "contrato": trabajo["contrato"],
        "contratos": str(trabajo["contratos"]),
        "contratos_menos_1": str(int(trabajo["contratos"]) - 1),
        "sl_ticks": str(trabajo["sl_ticks"]),
        "tp_ticks": str(trabajo["tp_ticks"]),
        "accion": trabajo["accion"],
        "cuenta_plataforma": cuenta_plataforma,
        "cuenta_ultimos4": cuenta_plataforma[-4:] if cuenta_plataforma else None,
        "riesgo_usd": trabajo.get("riesgo_usd"),
        "ganancia_usd": trabajo.get("ganancia_usd"),
    }


_AYUDA_FALTANTE = {
    "cuenta_plataforma": "configura 'cuenta_plataforma' en la cuenta (config.local.json)",
    "cuenta_ultimos4": "configura 'cuenta_plataforma' en la cuenta (config.local.json)",
    "riesgo_usd": "falta el valor del tick del simbolo en 'valor_tick' (config.json)",
    "ganancia_usd": "falta el valor del tick del simbolo en 'valor_tick' (config.json)",
}


def rellenar(texto: str, valores: dict) -> str:
    """Reemplaza los {marcadores}. Uno sin valor es un error: escribir
    "{cuenta_plataforma}" literal en la web no puede pasar en silencio."""
    def _uno(m):
        nombre = m.group(1)
        if nombre not in valores:
            raise ValueError(f"Marcador {{{nombre}}} desconocido.")
        valor = valores[nombre]
        if valor is None:
            ayuda = _AYUDA_FALTANTE.get(nombre, "no tiene valor para esta orden")
            raise ValueError(f"Falta el valor de {{{nombre}}}: {ayuda}.")
        return str(valor)
    return _PATRON_MARCADOR.sub(_uno, str(texto))


def _numero(texto: str):
    """'$1,234.50' -> 1234.5 ; None si no es un numero."""
    limpio = re.sub(r"[\s$,]", "", str(texto))
    try:
        return float(limpio)
    except ValueError:
        return None


def _leer(page, selector: str, timeout_ms: int) -> str:
    """El valor de un input/textarea/select, o el texto visible de otro
    elemento. Si el selector encuentra mas de uno, Playwright falla (modo
    estricto): verificar contra "alguno de varios" no verifica nada."""
    return page.locator(selector).evaluate(
        "e => ('value' in e && /^(INPUT|TEXTAREA|SELECT)$/.test(e.tagName))"
        " ? e.value : e.innerText", timeout=timeout_ms)


def verificar(page, paso: dict, valores: dict, timeout_ms: int, donde: str):
    leido = (_leer(page, paso["selector"], timeout_ms) or "").strip()
    if "igual" in paso:
        esperado = rellenar(paso["igual"], valores).strip()
        if paso.get("numerico"):
            a, b = _numero(leido), _numero(esperado)
            ok = a is not None and b is not None and abs(a - b) < 0.005
        else:
            ok = leido == esperado
        como = "igual a"
    else:
        esperado = rellenar(paso["contiene"], valores)
        ok = esperado in leido
        como = "que contenga"
    if not ok:
        raise VerificacionFallida(
            f"{donde}: {paso['selector']} dice {leido!r} y se esperaba {como} "
            f"{esperado!r}. NO se hace clic.")


def _veces(paso: dict, valores: dict, donde: str) -> int:
    texto = rellenar(paso["veces"], valores).strip()
    if not texto.isdigit():
        raise ValueError(f"{donde}: 'veces' tiene que ser un entero >= 0, no {texto!r}")
    veces = int(texto)
    if veces > MAXIMO_REPETICIONES:
        raise ValueError(f"{donde}: {veces} repeticiones supera el maximo "
                         f"({MAXIMO_REPETICIONES})")
    return veces


def esperar_con_aborto(page, ms: int, donde: str):
    """Espera en tramos cortos para que un stop no tenga que esperar entero."""
    restante = max(0, int(ms))
    while restante > 0:
        chequear_aborto(donde)
        tramo = min(100, restante)
        page.wait_for_timeout(tramo)
        restante -= tramo


def ejecutar_pasos(page, pasos: list, valores: dict, timeout_ms: int, nombre: str,
                   con_aborto: bool = True):
    """Corre los pasos en orden. Levanta OperacionAbortada si se pide parar
    entre pasos, o la excepcion de Playwright si un paso falla.

    con_aborto=False es para los pasos de LIMPIEZA: justamente se corren
    despues de un stop, y no pueden cortarse por el mismo stop.
    """
    for n, paso in enumerate(pasos, start=1):
        donde = f"{nombre} paso {n} ({paso.get('accion')})"
        if con_aborto:
            chequear_aborto(donde)
        accion = paso["accion"]
        if accion == "click":
            page.locator(paso["selector"]).click(timeout=timeout_ms)
        elif accion == "escribir":
            campo = page.locator(paso["selector"])
            texto = rellenar(paso["valor"], valores)
            if paso.get("teclear"):
                # Como un operador: clic, seleccionar todo y teclear. Medido
                # en TopstepX (11/11/2026): con fill() el autocompletado no
                # filtra (mostraba los 51 contratos), y fill("") no vacia un
                # campo controlado por React (quedaba "M6EZ26NQZ26").
                campo.click(timeout=timeout_ms)
                campo.press("Control+a", timeout=timeout_ms)
                campo.press_sequentially(texto, delay=60, timeout=timeout_ms)
            else:
                campo.fill(texto, timeout=timeout_ms)
        elif accion == "click_texto":
            page.get_by_text(rellenar(paso["texto"], valores),
                             exact=bool(paso.get("exacto", True))).click(timeout=timeout_ms)
        elif accion == "tecla":
            if paso.get("selector"):
                page.locator(paso["selector"]).press(paso["tecla"], timeout=timeout_ms)
            else:
                page.keyboard.press(paso["tecla"])
        elif accion == "esperar":
            if paso.get("selector"):
                page.locator(paso["selector"]).wait_for(state="visible", timeout=timeout_ms)
            if paso.get("ms"):
                if con_aborto:
                    esperar_con_aborto(page, paso["ms"], donde)
                else:
                    page.wait_for_timeout(int(paso["ms"]))
        elif accion == "repetir":
            for i in range(_veces(paso, valores, donde)):
                if con_aborto:
                    chequear_aborto(f"{donde}, clic {i + 1}")
                page.locator(paso["selector"]).click(timeout=timeout_ms)
        elif accion == "marcar":
            page.locator(paso["selector"]).check(timeout=timeout_ms)
        elif accion == "verificar":
            verificar(page, paso, valores, timeout_ms, donde)
        else:   # validar_plataforma ya lo impide; por las dudas
            raise ValueError(f"{donde}: accion desconocida")


def texto_de_fila(conf: dict, valores: dict) -> str:
    """Lo que identifica al contrato en una fila del panel de posiciones.

    Por defecto el contrato ("MNQZ6"), pero no todas las plataformas lo
    muestran asi: TopstepX pone la raiz con barra ("/M6E", visto en una
    posicion real el 11/11/2026). Se configura en posiciones.texto_fila con
    marcadores, por ejemplo "/{simbolo}".
    """
    return rellenar(conf.get("texto_fila") or "{contrato}", valores)


def leer_posiciones(page, conf: dict, buscado: str, timeout_ms: int) -> tuple:
    """Lo que dice el panel de posiciones sobre ESE contrato: la tupla
    ordenada de las cantidades de las filas que contienen `buscado` (ver
    texto_de_fila).

    Se compara la CANTIDAD y no la fila entera: la fila trae el P&L, que
    cambia solo con el precio y daria una confirmacion falsa.

    Levanta excepcion si el panel no esta a la vista: sin poder leerlo no hay
    forma de verificar, y quien llama decide que hacer (no hacer clic).
    """
    page.locator(conf["panel"]).wait_for(state="visible", timeout=timeout_ms)
    cantidades = []
    buscado = buscado.upper()
    for fila in page.locator(conf["filas"]).all():
        texto = fila.inner_text(timeout=timeout_ms)
        if buscado in texto.upper():
            cantidad = fila.locator(conf["cantidad"]).first.inner_text(timeout=timeout_ms)
            cantidades.append(cantidad.strip())
    return tuple(sorted(cantidades))
