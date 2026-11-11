# Notas para Claude sobre este proyecto

Bot que coloca órdenes en las terminales **web** de firmas de futuros
(TopstepX, Tradovate y otras) con **Playwright**, controlado por **Telegram**.
Es un proyecto nuevo e independiente, con diseño tomado del bot de MT5 (repo
aparte). De aquel se **copia y adapta**; nunca se importa nada de él.

## Cómo se trabaja

- **Por etapas.** Al terminar cada una: detenerse, reportar qué se hizo,
  cuántos tests pasan y qué falta, y **esperar el OK del usuario** antes de
  seguir. No adelantar etapas por iniciativa propia.
- El usuario habla español. Código, comentarios y logs en español **sin
  tildes** (por las consolas de Windows); los `.md` pueden llevarlas.
- La ruta del proyecto puede tener espacios (`Bot futuros`): en `.bat`,
  `.ps1` y Python, siempre comillas y rutas relativas a la carpeta del bot.
- **No inventar selectores.** Lo que no se midió en la plataforma real queda
  como `TODO` y el bot se niega a operar ahí.

## Lo primero: opera con dinero

`dry_run: false` coloca órdenes **de verdad** en cuentas de evaluación o
fondeadas.

- `dry_run` es **true** salvo un `false` explícito. Un valor raro (`""`,
  `"talvez"`) no lo apaga: queda en true y se avisa.
- Nunca hacer clic en Comprar/Vender para "probar". Tampoco dentro de
  `playwright codegen`: ahí la cuenta es real.
- Antes de dar por bueno un cambio en el flujo de órdenes, mirar el log real
  (`logs/listener-AAAA-MM-DD.log`) y las capturas de `logs/diagnosticos/`.

## Cómo correr las cosas

Python **no está en el PATH** en estas máquinas, y `python` a secas puede ser
el alias vacío de la Microsoft Store. Usar la ruta completa del intérprete
(`%LOCALAPPDATA%\Programs\Python\Python3XX\python.exe`). Ojo: puede haber una
carpeta `Python3XX` **sin** `python.exe` adentro (pasó con `Python313` en la
PC de desarrollo); `run_listener.bat` y `actualizar.ps1` prueban cada
candidato antes de usarlo.

```
python -m unittest discover -s tests -t . -q
```

El `-t .` **importa**: medido con Python 3.12, sin él `tests/__init__.py` (la
red de seguridad) **no se importa**. Por eso, además, todo archivo `test_*.py`
importa `tests.apoyo`, y `test_red_de_seguridad.py` falla si alguno se olvida.
La suite tarda unos 5 s y **tiene que pasar entera**: es el semáforo del
comando `update`.

Otros comandos útiles:

- `python config.py`: imprime la config **efectiva**, con el origen de cada
  valor y las advertencias.
- `python check_setup.py`: diagnóstico de la instalación (por ahora mínimo; la
  Etapa 3 lo completa).
- `python abrir_perfil.py ALIAS`: abre el navegador de una cuenta, a la vista,
  para hacer el login a mano. Exige el listener parado.

## Mapa del código

| Archivo | Qué hace |
| --- | --- |
| `listener_futuros.py` | Telegram: prefijo por línea, stop, programadas, update, avisos y latido |
| `ordenes.py` | Formato de orden y cierre, validación contra la config |
| `ejecutor.py` | Candado, aborto cooperativo, reglas del lote y marcas `INICIO/FIN OPERACION` |
| `ejecutor_web.py` | Playwright en un único hilo dueño del navegador |
| `pasos.py` | Validación de plataformas e intérprete de pasos |
| `ejecutor_falso.py` | Solo registra en el log (lo usan los tests) |
| `config.py` | Precedencia `FUTBOT_*` > `config.local.json` > `config.json` > defaults |
| `avisos.py` | Errores y latido al grupo aparte (copiado de MT5) |
| `abrir_perfil.py`, `cerrar_navegadores.ps1` | Login manual / Chromium huérfanos |
| `run_listener.bat`, `actualizar.ps1`, `configurar.bat` | Arranque, update con reversión, alta del token |

Formato de orden (detalle en `ordenes.py`):
`PREFIJO SIMBOLO buy|sell CONTRATOS SL_TICKS TP_TICKS [CUENTA] [HH:MM]`.
Contratos, SL y TP son **enteros estrictos**. CUENTA es un alias, varios
separados por coma, o `all`. El símbolo raíz se traduce al contrato con la
tabla `simbolos` de cada plataforma.

## Convivencia con el bot de MT5 en la misma PC

Todo lleva nombres propios a propósito:

- El listener es `listener_futuros.py` y **no** `telegram_listener.py`: el
  `actualizar.ps1` de MT5 mata cualquier python cuya línea de comando contenga
  `telegram_listener.py`.
- La tarea programada es `bot-futuros listener`, las variables de entorno son
  `FUTBOT_*` (nunca `TELEGRAM_BOT_TOKEN`, que puede ser el de MT5) y el
  temporal del `.bat` es `botfuturos-fecha.txt`.
- `no_robar_foco` (default true): la ventana del navegador se abre **fuera de
  la pantalla** y nunca se trae al frente. Una ventana encima le tapa las
  plantillas de píxeles a MT5 y se lleva sus teclas. **Falta medirlo** en una
  máquina real: que no aparezca, que no robe el foco y que la plataforma siga
  actualizando.

## Lecciones heredadas de MT5 que aplican acá

- **La trampa del config.** `config.local.json` **pisa** a `config.json`
  (el de flota, el que viaja con el update). Una clave de flota copiada al
  local deja a esa máquina sin los cambios, sin avisar. Acá solo valen en el
  local las claves de `CLAVES_LOCALES` (`config.py`); cualquier otra genera una
  advertencia al arrancar. Antes de dar por hecho un cambio de flota, **medir
  el valor efectivo** con `python config.py`. Los usuarios autorizados se
  **suman** entre las dos fuentes.
- **Un token por máquina.** Telegram entrega cada mensaje a un solo poller;
  dos procesos con el mismo token se roban los mensajes (`Conflict`).
- **`concurrent_updates(True)` es necesario**: sin eso el `stop` queda en cola
  detrás del lote que se está colocando.
- **Aislamiento de los tests**: ver `tests/__init__.py`. Ningún test puede
  salir a la red, lanzar `actualizar.ps1`, abrir un navegador real ni depender
  del `config.local.json` de la máquina.
- **Medir antes de concluir.** Mirar la captura del momento exacto antes de
  teorizar. El log solo registra lo que el bot logró hacer: sacar conclusiones
  de ahí sobre lo que falla es sesgo de supervivencia.

## Reglas de seguridad del ejecutor web (no aflojarlas)

- Si el panel de posiciones no se puede leer **antes** del clic, **no se hace
  clic**: después no habría cómo verificar.
- El `stop` se revisa antes de cada paso y justo antes del clic final
  (también antes del clic de confirmar, si la plataforma lo pide).
- La verificación compara la **cantidad** de la fila del contrato, no la fila
  entera (el P&L cambia solo).
- Orden no confirmada o con error → esa cuenta queda **bloqueada solo por el
  resto del lote** (decisión del usuario). Nunca se reintenta un clic.
- El bot nunca maneja contraseñas: el login se hace a mano en el perfil de
  cada cuenta.

## Estado (11/11/2026)

- **Etapa 1** (listener, parser, ejecutor falso) y **Etapa 2** (ejecutor web,
  solo colocar órdenes): hechas. 220 tests en verde.
- `telegram_allowed_user_id` en `config.json`: los mismos tres ids de Telegram
  que la flota de MT5 (lo confirmó el usuario).
- **El bot va a correr en otra PC**, no en la de desarrollo. Ahí hay que
  instalar todo, hacer el login en los perfiles y sacar los selectores.
- Nada de los selectores está medido: las dos plataformas están en `TODO`.

### Lo que se vio en las plataformas (capturas del usuario, 11/11/2026)

- **TopstepX** (`https://topstepx.com/trade`): contratos con el año en **dos
  dígitos** (`M6EZ26`). Selector de cuenta arriba a la izquierda ("50K DLL
  Express (...2304)"). El ticket está en el DOM de la derecha: botones de
  cantidad, **"Position Bracket"** (Enabled/Disabled, con engranaje) y
  `BUY +1 MARKET` / `SELL -1 MARKET`, que dicen la cantidad. Las posiciones
  están en la pestaña **Positions** de abajo (columna "Position").
- **Tradovate**: el usuario usa la **app de escritorio**; el bot tiene que usar
  la versión **web** (probablemente `trader.tradovate.com`, sin confirmar).
  Contratos con el año en **un dígito** (`M6EZ6`, `MNQZ6`). Arriba están
  `Buy Mkt` / `Sell Mkt`, la cantidad, una **ATM** ("scalp") con engranaje y
  **POSITION** del contrato (sirve para verificar). Selector de cuenta
  arriba ("MFFUSFBLDR700298009").
- **En las dos, el SL/TP no se escribe en el ticket**: sale del bracket o de la
  plantilla ATM. Falta decidir con codegen si los pasos escriben los ticks en
  esa configuración en cada orden o si se eligen plantillas fijas. Un bracket
  en Disabled manda la orden **sin stop**.

### Pendiente

1. **Paso `verificar`** (propuesto, sin aprobar todavía): leer el texto de un
   elemento y cortar sin clic si no contiene lo esperado. Por ejemplo, que el
   bracket diga "Enabled" y que el botón diga `+{contratos}`.
2. **Etapa 3**: cierres (cuenta por defecto, una cuenta o todas), multi-cuenta,
   latido con datos del navegador, `check_setup.py` completo (Playwright,
   Chromium, config, perfiles).
3. **Etapa 4**: este archivo completo e `INSTALAR-MAQUINA-NUEVA.md`, con las
   verificaciones en orden: tests, check_setup, valor efectivo de la config,
   dry run con prefijo propio, que ignore prefijo ajeno y orden sin prefijo, y
   recién ahí una orden real mínima.
4. **Selectores** de las dos plataformas, sacados con codegen en la PC donde
   corre el bot.

## Cómo sacar los selectores (en la PC donde corre el bot)

1. `python -m playwright install chromium`, o poner `"canal": "msedge"` en
   `navegador` para usar el Edge de Windows.
2. Con el listener parado: `python abrir_perfil.py ALIAS` y hacer el login.
3. `python -m playwright codegen --target python --user-data-dir "perfiles\ALIAS" <url>`
4. **No hacer clic en Comprar/Vender.** Para botones, panel y celdas, usar
   **"Pick locator"** del Inspector.
5. Traducir al formato de `config.json`: `get_by_role("button", name="X")` →
   `role=button[name='X']`; `get_by_text("X")` → `text=X`;
   `get_by_placeholder("X")` → `[placeholder='X']`;
   `get_by_test_id("x")` → `[data-testid='x']`; `locator("#id")` → `#id`.
   Evitar clases generadas al azar (`.css-1x2y3z`): cambian con cada versión.

## Git

- Repo: `https://github.com/DAVIDHONGGG/futuros-bot.git`, rama `main`.
- Autor configurado solo para el repo de la PC de desarrollo:
  `DAVIDHONGGG <dvhong98@gmail.com>`.
- Si Git viene de Scoop, el primer push abre una ventanita
  (`git-credential-helper-selector`) que suele quedar escondida: elegir
  `manager`. Un push lanzado sin pantalla la cancela ("User cancelled
  dialog").
- Commit y push solo cuando el usuario lo pide.
