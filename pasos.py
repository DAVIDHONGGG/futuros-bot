"""
Interprete de los pasos que describen una plataforma en config.json, y su
validacion.

Cada plataforma dice COMO capturar una orden con una lista de pasos:

    {"accion": "click",       "selector": "<selector>"}
    {"accion": "escribir",    "selector": "<selector>", "valor": "{contratos}"}
    {"accion": "click_texto", "texto": "{contrato}"}
    {"accion": "tecla",       "tecla": "Enter"}                 (a la pagina)
    {"accion": "tecla",       "tecla": "Enter", "selector": "<selector>"}
    {"accion": "esperar",     "ms": 300}
    {"accion": "esperar",     "selector": "<selector>"}         (hasta que se vea)

Los selectores son de Playwright tal cual: CSS ("#qty", "[data-testid=buy]"),
"text=Buy Mkt", "role=button[name=\"Buy\"]", "xpath=...". Se sacan con
`playwright codegen <url>` (ver CLAUDE.md / el reporte de la Etapa 2).

Marcadores que se reemplazan en "valor" y "texto":
    {simbolo} {contrato} {contratos} {sl_ticks} {tp_ticks} {accion}
    {cuenta_plataforma}

Todo lo de aca corre en el hilo del navegador (ver ejecutor_web.py). Antes de
CADA paso se revisa el aborto: un "stop" a mitad de la captura corta ahi.
"""

import re

from config import es_sin_configurar
from ejecutor import chequear_aborto

ACCIONES = {"click", "escribir", "click_texto", "tecla", "esperar"}

MARCADORES = {"simbolo", "contrato", "contratos", "sl_ticks", "tp_ticks", "accion",
              "cuenta_plataforma"}
_PATRON_MARCADOR = re.compile(r"\{([a-z_]+)\}")

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
        }[accion]
        for campo in requeridos:
            if campo not in paso:
                problemas.append(f"{donde}: falta '{campo}'")
            elif es_sin_configurar(paso[campo]):
                problemas.append(f"{donde}: '{campo}' sin configurar ({paso[campo]!r})")
        if accion == "esperar" and "ms" not in paso and "selector" not in paso:
            problemas.append(f"{donde}: esperar necesita 'ms' o 'selector'")
        if "selector" in paso and accion in ("tecla", "esperar") and es_sin_configurar(paso["selector"]):
            problemas.append(f"{donde}: 'selector' sin configurar ({paso['selector']!r})")
        for campo in ("valor", "texto"):
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
    return [f"{nombre}: {p}" for p in problemas]


def valores_de(trabajo: dict, cuenta) -> dict:
    """Los valores con los que se rellenan los marcadores de un trabajo."""
    return {
        "simbolo": trabajo["simbolo"],
        "contrato": trabajo["contrato"],
        "contratos": str(trabajo["contratos"]),
        "sl_ticks": str(trabajo["sl_ticks"]),
        "tp_ticks": str(trabajo["tp_ticks"]),
        "accion": trabajo["accion"],
        "cuenta_plataforma": getattr(cuenta, "cuenta_plataforma", None),
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
            raise ValueError(f"Falta el valor de {{{nombre}}} (para {{cuenta_plataforma}}: "
                             f"configura 'cuenta_plataforma' en la cuenta).")
        return str(valor)
    return _PATRON_MARCADOR.sub(_uno, str(texto))


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
            page.locator(paso["selector"]).fill(rellenar(paso["valor"], valores),
                                                timeout=timeout_ms)
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
        else:   # validar_plataforma ya lo impide; por las dudas
            raise ValueError(f"{donde}: accion desconocida")


def leer_posiciones(page, conf: dict, contrato: str, timeout_ms: int) -> tuple:
    """Lo que dice el panel de posiciones sobre ESE contrato: la tupla
    ordenada de las cantidades de las filas que lo mencionan.

    Se compara la CANTIDAD y no la fila entera: la fila trae el P&L, que
    cambia solo con el precio y daria una confirmacion falsa.

    Levanta excepcion si el panel no esta a la vista: sin poder leerlo no hay
    forma de verificar, y quien llama decide que hacer (no hacer clic).
    """
    page.locator(conf["panel"]).wait_for(state="visible", timeout=timeout_ms)
    cantidades = []
    buscado = contrato.upper()
    for fila in page.locator(conf["filas"]).all():
        texto = fila.inner_text(timeout=timeout_ms)
        if buscado in texto.upper():
            cantidad = fila.locator(conf["cantidad"]).first.inner_text(timeout=timeout_ms)
            cantidades.append(cantidad.strip())
    return tuple(sorted(cantidades))
