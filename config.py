"""
Configuracion del bot de futuros, para poder copiar la MISMA carpeta a varias
PCs.

Orden de precedencia (gana el primero que define cada clave):

    1. Variables de entorno FUTBOT_*  -> para probar algo puntual
    2. config.local.json              -> config de ESTA maquina (no se versiona)
    3. config.json                    -> valores de la flota (viaja con el update)
    4. Defaults del codigo            -> ver las properties de Config

LA TRAMPA QUE ESTE ARCHIVO EXISTE PARA EVITAR (heredada del bot de MT5):
config.local.json PISA a config.json. Si una clave de flota termina copiada en
el local de una maquina, cambiarla en config.json -que es lo que viaja con el
update- no hace nada en esa maquina, sin un solo aviso. En MT5 mordio dos
veces (la lista de autorizados y sl_tp_iniciales_al_abrir).

Por eso aca:
  - Solo las claves de CLAVES_LOCALES tienen sentido en config.local.json.
    Cualquier otra que aparezca ahi queda anotada en `advertencias`, que el
    listener loguea en WARNING al arrancar y check_setup muestra.
  - Se guarda de DONDE salio cada valor (`origen`), y `python config.py`
    imprime la config EFECTIVA con su origen. Eso es lo que hay que mirar
    antes de dar por hecho un cambio de flota, no el archivo.

Las variables de entorno son FUTBOT_* a proposito, y NO TELEGRAM_BOT_TOKEN:
si una PC tiene esa variable puesta para el bot de MT5, este bot tomaria el
token del otro y los dos se robarian los mensajes (Conflict de getUpdates).

Uso:
    from config import CONFIG
    CONFIG.dry_run
    CONFIG.cuentas["TS1"].plataforma

    python config.py      -> imprime la config efectiva y las advertencias
"""

import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("bot_futuros.config")

BASE_DIR = Path(__file__).parent

# Palabra para dirigirse a TODAS las maquinas del grupo. Este bot tiene su
# propio grupo de Telegram (no comparte con los de MT5), asi que no hace
# falta una palabra distinta.
PREFIJO_TODAS = "ALL"

# Nombre -> variable de entorno equivalente.
_ENV = {
    "machine_id": "FUTBOT_MACHINE_ID",
    "prefijo_telegram": "FUTBOT_PREFIJO_TELEGRAM",
    "telegram_bot_token": "FUTBOT_TELEGRAM_BOT_TOKEN",
    "telegram_allowed_user_id": "FUTBOT_TELEGRAM_ALLOWED_USER_ID",
    "dry_run": "FUTBOT_DRY_RUN",
}

# Para tener la config local fuera de la carpeta del codigo.
VAR_CONFIG_EXTERNA = "FUTBOT_CONFIG"

_BOOLEANOS = {"dry_run", "no_robar_foco"}

# Claves que se SUMAN entre fuentes en vez de pisarse (ver config.py de MT5:
# la lista de autorizados vive en la flota y cada maquina puede sumar a
# alguien propio, sin sombrear la lista compartida).
_ACUMULATIVAS = {"telegram_allowed_user_id"}

# Lo UNICO que tiene sentido en config.local.json: lo que cambia de una
# maquina a otra. Todo lo demas es de flota y vive en config.json.
CLAVES_LOCALES = frozenset({
    "machine_id",
    "prefijo_telegram",
    "telegram_bot_token",
    "telegram_allowed_user_id",   # acumulativa: suma, no sombrea
    "dry_run",
    "cuentas",
    "cuenta_por_defecto",
    "no_robar_foco",
})

# Un alias de cuenta no puede ser una de estas palabras: se confundiria con
# un comando ("close all", "stop") o con la accion de la orden.
PALABRAS_RESERVADAS = frozenset({
    "ALL", "TODAS", "TODO", "CLOSE", "CERRAR", "ACCOUNTS", "CUENTAS",
    "BUY", "SELL", "STOP", "PARAR", "CANCELAR", "CANCEL", "ALTO",
    "UPDATE", "ACTUALIZAR", "CHATID", "ID",
})

# Letras, numeros, guion y guion bajo. Sin ":" (se confundiria con HH:MM) ni
# "," (separa varias cuentas en la orden).
ALIAS_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,29}$")


def es_sin_configurar(valor) -> bool:
    """True para los marcadores que quedan en config.json hasta que alguien
    complete el dato de verdad ("TODO ..."), o para un valor vacio."""
    if valor is None:
        return True
    texto = str(valor).strip()
    return not texto or texto.upper().startswith("TODO")


def _como_lista(valor):
    if isinstance(valor, (list, tuple, set, frozenset)):
        return list(valor)
    if isinstance(valor, (str, bytes)):
        return [p for p in str(valor).replace(";", ",").split(",") if p.strip()]
    return [valor]


class ConfigIlegible(Exception):
    """Un archivo de configuracion existe pero no se pudo leer/parsear.

    Se ABORTA a proposito en vez de ignorarlo (leccion de MT5): al descartar
    el archivo cada valor cae a su default, y una maquina que creias en seco
    podria quedar operando. Mejor no arrancar.
    """


def _leer_json(ruta: Path) -> dict:
    if not ruta.exists():
        return {}
    try:
        # utf-8-sig: tolera el BOM que escriben PowerShell 5.1 y varios
        # editores de Windows. En MT5 un BOM dejo una maquina "EN VIVO".
        with open(ruta, encoding="utf-8-sig") as f:
            datos = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ConfigIlegible(f"No se pudo leer {ruta.name}: {e}") from e
    if not isinstance(datos, dict):
        raise ConfigIlegible(f"{ruta.name} no contiene un objeto JSON.")
    return datos


@dataclass(frozen=True)
class Cuenta:
    """Una cuenta de una firma de futuros, tal como la ve ESTA maquina."""
    alias: str                 # como se la nombra en Telegram ("TS1")
    plataforma: str            # clave en "plataformas" de config.json
    max_contratos: int         # tope por orden; mas que esto se rechaza
    perfil: Path               # carpeta del perfil de navegador persistente
    cuenta_plataforma: str | None = None  # nombre en el selector de la web


class Config:
    """Configuracion resuelta de esta maquina."""

    def __init__(self, base_dir: Path = BASE_DIR, entorno=None):
        entorno = os.environ if entorno is None else entorno
        self.base_dir = Path(base_dir)
        self.fuentes = []
        self.origen = {}          # clave -> de donde salio el valor efectivo
        self.advertencias = []    # problemas que no impiden arrancar

        valores = {}

        # 3. Flota.
        flota_ruta = self.base_dir / "config.json"
        flota = _leer_json(flota_ruta)
        if flota_ruta.exists():
            self.fuentes.append(flota_ruta.name)
        self._fusionar(valores, flota, flota_ruta.name)

        # 2. Esta maquina.
        ruta_local = entorno.get(VAR_CONFIG_EXTERNA)
        local_ruta = Path(ruta_local) if ruta_local else self.base_dir / "config.local.json"
        local = _leer_json(local_ruta)
        nombre_local = local_ruta.name if not ruta_local else str(local_ruta)
        if local_ruta.exists():
            self.fuentes.append(nombre_local)
        self._revisar_sombras(flota, local, nombre_local)
        self._fusionar(valores, local, nombre_local)

        # 1. Variables de entorno.
        for clave, var in _ENV.items():
            if entorno.get(var):
                self._fusionar(valores, {clave: entorno[var]}, var)
                self.fuentes.append(var)

        for clave in _BOOLEANOS:
            if clave in valores:
                valores[clave] = self._a_bool_seguro(clave, valores[clave])

        self.valores = valores
        self._cuentas = self._validar_cuentas()

    # --- carga --------------------------------------------------------------

    def _fusionar(self, valores: dict, nuevos: dict, origen: str):
        for clave, valor in nuevos.items():
            if clave.startswith("_"):
                continue      # comentarios del JSON, no son valores
            if clave in _ACUMULATIVAS and clave in valores:
                valores[clave] = _como_lista(valores[clave]) + _como_lista(valor)
                self.origen[clave] = f"{self.origen[clave]} + {origen}"
            else:
                valores[clave] = valor
                self.origen[clave] = origen

    def _a_bool_seguro(self, clave: str, valor):
        """Booleano que, ante un valor que no se entiende, queda en None (o sea,
        el default del codigo, que para dry_run es True).

        Con _a_bool a secas, "dry_run": "" o "dry_run": "talvez" daban False:
        la maquina quedaba EN VIVO por un typo. Solo un false EXPLICITO apaga.
        """
        if isinstance(valor, bool):
            return valor
        texto = str(valor).strip().lower()
        if texto in {"1", "true", "yes", "y", "si", "on"}:
            return True
        if texto in {"0", "false", "no", "n", "off"}:
            return False
        self.advertencias.append(
            f"'{clave}' tiene un valor que no es true/false ({valor!r}); se usa "
            f"el default.")
        return None

    def _revisar_sombras(self, flota: dict, local: dict, nombre_local: str):
        """Anota cada clave de flota que aparezca en el local.

        No se ignora: puede que el operador la haya puesto a proposito para
        esta maquina. Pero tiene que quedar a la vista, porque es exactamente
        lo que en MT5 dejo maquinas sin recibir cambios de la flota.
        """
        for clave, valor in local.items():
            if clave.startswith("_") or clave in CLAVES_LOCALES:
                continue
            detalle = ""
            if clave in flota and flota[clave] != valor:
                detalle = " Ademas tiene un valor DISTINTO al de la flota."
            self.advertencias.append(
                f"'{clave}' esta en {nombre_local} pero es una clave de FLOTA: "
                f"ahi pisa a config.json y los cambios de la flota no le llegan "
                f"a esta maquina.{detalle} Sacala de {nombre_local}.")

    def _validar_cuentas(self) -> dict:
        """{alias: Cuenta} con las cuentas validas. Las invalidas se descartan
        con una advertencia: una cuenta mal escrita no puede dejar a las otras
        sin operar, y una orden a ella se rechaza por "cuenta desconocida"."""
        crudas = self.get("cuentas") or {}
        if not isinstance(crudas, dict):
            self.advertencias.append("'cuentas' no es un objeto JSON; no hay cuentas.")
            return {}
        plataformas = self.plataformas
        cuentas = {}
        for alias_crudo, datos in crudas.items():
            alias = str(alias_crudo).strip().upper()
            problema = None
            if not ALIAS_PATTERN.match(alias):
                problema = ("el alias solo puede tener letras, numeros, '-' y '_' "
                            "(sin espacios, ':' ni ',')")
            elif alias in PALABRAS_RESERVADAS:
                problema = "el alias es una palabra reservada de los comandos"
            elif alias in cuentas:
                problema = "alias repetido (no distingue mayusculas)"
            elif not isinstance(datos, dict):
                problema = "tiene que ser un objeto con plataforma y max_contratos"
            if problema is None:
                plataforma = str(datos.get("plataforma", "")).strip().lower()
                maximo = datos.get("max_contratos")
                if plataforma not in plataformas:
                    problema = (f"plataforma {plataforma!r} desconocida (las de "
                                f"config.json: {', '.join(sorted(plataformas)) or 'ninguna'})")
                elif isinstance(maximo, bool) or not isinstance(maximo, int) or maximo < 1:
                    problema = f"max_contratos tiene que ser un entero >= 1, no {maximo!r}"
            if problema:
                self.advertencias.append(f"Cuenta {alias_crudo!r} descartada: {problema}.")
                continue
            perfil = datos.get("perfil") or f"perfiles/{alias}"
            perfil = Path(perfil)
            if not perfil.is_absolute():
                perfil = self.base_dir / perfil
            cuenta_plataforma = datos.get("cuenta_plataforma")
            cuentas[alias] = Cuenta(
                alias=alias,
                plataforma=plataforma,
                max_contratos=maximo,
                perfil=perfil,
                cuenta_plataforma=None if es_sin_configurar(cuenta_plataforma)
                else str(cuenta_plataforma),
            )
        return cuentas

    # --- acceso -------------------------------------------------------------

    def get(self, clave, default=None):
        valor = self.valores.get(clave, default)
        return default if valor in ("", None) else valor

    @property
    def machine_id(self) -> str:
        return self.get("machine_id") or os.environ.get("COMPUTERNAME") or "sin-nombre"

    @property
    def telegram_bot_token(self) -> str:
        return self.get("telegram_bot_token", "")

    @property
    def telegram_allowed_user_ids(self) -> frozenset:
        """User ids autorizados. Acepta un numero, una lista o texto separado
        por comas. Un id invalido se descarta y los demas siguen valiendo."""
        valor = self.get("telegram_allowed_user_id")
        if valor is None:
            return frozenset()
        ids = set()
        for crudo in _como_lista(valor):
            texto = str(crudo).strip()
            if not texto:
                continue
            try:
                numero = int(texto)
            except (TypeError, ValueError):
                log.error(f"'{texto}' no es un user id de Telegram valido; se ignora.")
                continue
            if numero > 0:            # 0 = "sin configurar"
                ids.add(numero)
        return frozenset(ids)

    @property
    def telegram_allowed_user_id(self) -> int:
        """El menor id autorizado, o 0 si no hay ninguno (marcador de "sin
        configurar" que usa el listener para decirle su id a quien escribe)."""
        ids = self.telegram_allowed_user_ids
        return min(ids) if ids else 0

    @property
    def telegram_chat_errores(self):
        return self.get("telegram_chat_errores")

    @property
    def dry_run(self) -> bool:
        """True salvo que alguien diga EXPLICITAMENTE false.

        Al reves que en MT5, donde sin valor caia a False: una maquina recien
        desempaquetada, sin config, quedaba EN VIVO. Aca, en la duda, en seco.
        """
        valor = self.valores.get("dry_run")
        return True if valor is None else bool(valor)

    @property
    def prefijo_telegram(self):
        """Prefijo de ESTA maquina en el grupo (ej. "FUT1"), o None.

        En este bot es OBLIGATORIO: sin prefijo el listener no arranca, y una
        linea sin prefijo no la ejecuta nadie.
        """
        valor = self.get("prefijo_telegram")
        return str(valor).strip().upper() if valor else None

    @property
    def no_robar_foco(self) -> bool:
        """Que el navegador nunca se traiga al frente (default True). Ver
        ejecutor_web.py: en una PC con el bot de MT5, una ventana encima le
        tapa las plantillas y se lleva sus teclas."""
        valor = self.valores.get("no_robar_foco")
        return True if valor is None else bool(valor)

    @property
    def plataformas(self) -> dict:
        """{nombre_en_minusculas: dict} tal como esta en config.json."""
        valor = self.get("plataformas") or {}
        if not isinstance(valor, dict):
            return {}
        return {str(k).strip().lower(): v for k, v in valor.items()
                if isinstance(v, dict)}

    @property
    def cuentas(self) -> dict:
        return dict(self._cuentas)

    @property
    def cuenta_por_defecto(self):
        """La cuenta de una orden que no nombra ninguna.

        Si hay una sola cuenta, es esa. Con varias tiene que estar configurada
        en "cuenta_por_defecto": adivinar en cual operar no es una opcion.
        """
        valor = self.get("cuenta_por_defecto")
        if valor:
            alias = str(valor).strip().upper()
            return alias if alias in self._cuentas else None
        if len(self._cuentas) == 1:
            return next(iter(self._cuentas))
        return None

    def contrato(self, plataforma: str, simbolo: str) -> str:
        """El nombre del contrato en esa plataforma ("MNQ" -> "MNQZ6").

        Levanta ValueError si el simbolo no esta en la tabla o esta sin
        completar ("TODO"): adivinar el contrato -o mandar la raiz pelada- es
        operar un vencimiento que no elegiste.
        """
        datos = self.plataformas.get(plataforma.lower())
        if datos is None:
            raise ValueError(f"Plataforma {plataforma!r} desconocida.")
        tabla = datos.get("simbolos") or {}
        if not isinstance(tabla, dict):
            tabla = {}
        tabla = {str(k).strip().upper(): v for k, v in tabla.items()}
        entrada = tabla.get(simbolo.upper())
        if isinstance(entrada, dict):       # {"contrato": "MNQZ6", "vence": ...}
            entrada = entrada.get("contrato")
        if entrada is None:
            conocidos = ", ".join(sorted(tabla)) or "ninguno"
            raise ValueError(
                f"Simbolo {simbolo.upper()} no configurado en {plataforma} "
                f"(conocidos: {conocidos}).")
        if es_sin_configurar(entrada):
            raise ValueError(
                f"Simbolo {simbolo.upper()} sin contrato en {plataforma}: completa "
                f"la tabla 'simbolos' en config.json.")
        return str(entrada).strip()

    # --- diagnostico --------------------------------------------------------

    def resumen(self) -> str:
        fuentes = ", ".join(self.fuentes) if self.fuentes else "solo defaults"
        return f"maquina '{self.machine_id}' | config: {fuentes}"

    def efectiva(self) -> list:
        """[(clave, valor_para_mostrar, origen)] de la config EFECTIVA.

        Es lo que hay que mirar para saber que valor esta usando de verdad la
        maquina (no el del archivo). El token nunca se muestra entero.
        """
        filas = [
            ("dry_run", self.dry_run, self.origen.get("dry_run", "default (true)")),
            ("prefijo_telegram", self.prefijo_telegram,
             self.origen.get("prefijo_telegram", "-")),
            ("machine_id", self.machine_id, self.origen.get("machine_id", "default")),
            ("telegram_bot_token", _token_para_mostrar(self.telegram_bot_token),
             self.origen.get("telegram_bot_token", "-")),
            ("telegram_allowed_user_id", sorted(self.telegram_allowed_user_ids),
             self.origen.get("telegram_allowed_user_id", "-")),
            ("telegram_chat_errores", self.telegram_chat_errores,
             self.origen.get("telegram_chat_errores", "-")),
            ("no_robar_foco", self.no_robar_foco,
             self.origen.get("no_robar_foco", "default (true)")),
            ("plataformas", sorted(self.plataformas), self.origen.get("plataformas", "-")),
            ("cuentas", sorted(self._cuentas), self.origen.get("cuentas", "-")),
            ("cuenta_por_defecto", self.cuenta_por_defecto,
             self.origen.get("cuenta_por_defecto", "unica cuenta" if len(self._cuentas) == 1 else "-")),
        ]
        return filas


def _token_para_mostrar(token: str) -> str:
    if not token:
        return "(vacio)"
    return f"...{token[-6:]} ({len(token)} caracteres)"


def imprimir_efectiva(config: "Config", salida=None):
    salida = salida or sys.stdout
    print(f"Config efectiva - {config.resumen()}", file=salida)
    for clave, valor, origen in config.efectiva():
        print(f"  {clave:26} = {valor!s:40}  <- {origen}", file=salida)
    for alias, c in sorted(config.cuentas.items()):
        print(f"  cuenta {alias}: {c.plataforma}, max {c.max_contratos} contrato(s), "
              f"perfil {c.perfil}", file=salida)
    if config.advertencias:
        print("ADVERTENCIAS:", file=salida)
        for a in config.advertencias:
            print(f"  - {a}", file=salida)
    else:
        print("Sin advertencias.", file=salida)


CONFIG = Config()


if __name__ == "__main__":
    imprimir_efectiva(CONFIG)
