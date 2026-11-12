"""
Abre el navegador de UNA cuenta, a la vista, para que el operador haga el
login a mano. El bot nunca maneja contrasenas: usa la sesion que queda
guardada en el perfil (perfiles/ALIAS, o el "perfil" de la cuenta).

    python abrir_perfil.py TS1

Se niega si el listener esta corriendo: un perfil de Chromium no se puede
abrir desde dos procesos, y el listener lo usa para operar. Pararlo antes:

    Stop-ScheduledTask -TaskName "bot-futuros listener"
    (y matar el python.exe de listener_futuros.py, ver INSTALAR)

Al terminar el login, cerra la ventana del navegador: el script termina solo.
Despues se vuelve a levantar el listener.
"""

import subprocess
import sys

from config import es_sin_configurar
import ejecutor_web
from ejecutor_web import ARGS_SIN_FRENOS

SCRIPT_LISTENER = "listener_futuros.py"

# Tamano de la ventana del login: el de la pantalla, sin pasarse de esto.
ANCHO_MAXIMO, ALTO_MAXIMO = 1600, 1000
TAMANO_SI_NO_SE_SABE = (1280, 760)


def _tamano_de_pantalla():
    """(ancho, alto) de la pantalla principal segun Windows, o None."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        ancho, alto = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        return (ancho, alto) if ancho > 0 and alto > 0 else None
    except Exception:          # no es Windows, o sin escritorio
        return None


def args_ventana_a_la_vista(pantalla=None) -> list:
    """Fuerza la ventana DENTRO de la pantalla.

    Hace falta porque Edge/Chromium guarda en el perfil donde estaba la
    ventana, y el bot la abre en (-32000, -32000). Medido el 12/11/2026 con
    un perfil descartable: despues de una corrida del bot, abrir sin estos
    args la dejaba en (-1570, 0)-(30, 1000), con 30 px a la vista, y el
    operador no podia hacer el login. Con --window-position/--window-size
    queda en (0, 0).
    """
    ancho, alto = pantalla or _tamano_de_pantalla() or TAMANO_SI_NO_SE_SABE
    # 40 px menos de alto: la barra de tareas.
    ancho, alto = min(ancho, ANCHO_MAXIMO), min(alto - 40, ALTO_MAXIMO)
    return ["--window-position=0,0", f"--window-size={ancho},{max(alto, 400)}"]


def listener_corriendo() -> bool:
    """True si hay un python.exe corriendo listener_futuros.py."""
    comando = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
               f"Where-Object {{ $_.CommandLine -like '*{SCRIPT_LISTENER}*' }} | "
               "Measure-Object | Select-Object -ExpandProperty Count")
    try:
        salida = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", comando],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return int((salida or "0").strip() or 0) > 0
    except (OSError, subprocess.SubprocessError, ValueError):
        # Ante la duda, se asume que si: abrir el perfil con el listener
        # usandolo puede dejarlo trabado a mitad de una orden.
        return True


def main(argv=None, config=None, fabrica=None, corriendo=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if config is None:
        from config import CONFIG as config
    if len(argv) != 1:
        print(__doc__)
        return 2
    alias = argv[0].strip().upper()
    cuenta = config.cuentas.get(alias)
    if cuenta is None:
        print(f"Cuenta {alias} desconocida. Configuradas: "
              f"{', '.join(sorted(config.cuentas)) or 'ninguna'} (config.local.json).")
        return 1
    if (corriendo or listener_corriendo)():
        print("El listener esta corriendo y usa los perfiles. Paralo primero "
              "(ver el comentario al principio de este archivo).")
        return 1

    plataforma = config.plataformas.get(cuenta.plataforma, {})
    url = plataforma.get("url")
    if es_sin_configurar(url):
        print(f"La url de {cuenta.plataforma} no esta configurada en config.json; "
              "se abre una pestana vacia: navega a mano.")
        url = None

    opciones = {"user_data_dir": str(cuenta.perfil), "headless": False,
                "args": list(ARGS_SIN_FRENOS) + args_ventana_a_la_vista(),
                "no_viewport": True}
    if config.navegador["canal"]:
        opciones["channel"] = config.navegador["canal"]
    cuenta.perfil.mkdir(parents=True, exist_ok=True)

    # ejecutor_web._fabrica_real se busca al llamar (no al importar): asi la
    # red de seguridad de los tests lo puede reemplazar.
    pw = (fabrica or ejecutor_web._fabrica_real)()
    try:
        ctx = pw.chromium.launch_persistent_context(**opciones)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if url:
            page.goto(url)
        print(f"Navegador abierto con el perfil de {alias} ({cuenta.perfil}).")
        print("Inicia sesion a mano (marca 'recordarme' si la plataforma lo ofrece) "
              "y despues CERRA la ventana del navegador.")
        ctx.wait_for_event("close", timeout=0)
        print("Listo. La sesion quedo guardada en el perfil.")
    finally:
        pw.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
