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
  solo colocar órdenes): hechas. 276 tests en verde (12/11/2026: tope de
  riesgo y SL/TP en dólares en la fila de la posición).
- `telegram_allowed_user_id` en `config.json`: los mismos tres ids de Telegram
  que la flota de MT5 (lo confirmó el usuario).
- **El bot va a correr en otra PC**, no en la de desarrollo. Ahí hay que
  instalar todo, hacer el login en los perfiles y sacar los selectores.
- Nada de los selectores está medido: las dos plataformas están en `TODO`.
  (Desactualizado: ver "PC FUTVAN" abajo para TopstepX.)

### PC FUTVAN: TopstepX medido (11/11/2026)

Máquina con el bot de MT5 corriendo al lado; opera **solo TopstepX** (Tradovate
está en otra PC). Prefijo `FUTVAN`, cuentas `TOP1`..`TOP5` (50K DLL Express,
**un solo login**, perfil compartido `perfiles\TOPSTEPX`), `max_riesgo_usd: 800`.

- **Chromium de Playwright no arranca** ahí ("side-by-side configuration is
  incorrect", también reinstalado): `"canal": "msedge"` en `config.json`.
- **El login con Google falla en un navegador automatizado** ("This browser
  or app may not be secure"): lo bloquea Google, no TopstepX. Con el login
  propio de TopstepX entra, y la sesión persiste en el perfil, incluso headless.
- **Selectores**: todo con `data-testid` (ver `config.json`). Se midieron sin
  ventana, sin clic en BUY/SELL, y los pasos de cuenta, contrato y cantidad se
  probaron con el intérprete real: TOP2 y vuelta, MNQZ26 x3 (`BUY +3 MARKET`)
  y vuelta a M6EZ26 x1.
- **Cuenta**: el botón muestra `(...2304)`; la lista muestra el ACCT completo.
  Se elige con `click_texto {cuenta_plataforma}` y se verifica `(...{cuenta_ultimos4})`.
- **Contrato**: autocompletado de MUI. `fill()` **no filtra** (51 sugerencias)
  y `fill("")` **no vacía** (React lo repone: quedaba `M6EZ26NQZ26`). Por eso
  `escribir` tiene `"teclear": true` (clic, Ctrl+A, tecla por tecla).
- **Cantidad**: no hay campo; preset `1` y `repetir` el `+`.
- **SL/TP** (REEMPLAZADO el 12/11, ver abajo): diálogo "Position Brackets" en **dólares** (`Risk (~$)` /
  `Profit (~$)`), por cuenta, sin botón Guardar. Con "Automatically apply"
  desmarcado o los campos vacíos, **la orden sale sin stop** (así estaba). El
  bot escribe `{riesgo_usd}`/`{ganancia_usd}` (ticks x `valor_tick` x
  contratos), `marcar` la casilla y `verificar` los dos valores.
- **Fila de posición** (captura del operador con un short real de 1 M6E):
  Symbol dice **`/M6E`** (raíz con barra, no `M6EZ26`) y Position `-1`. Por
  eso `posiciones.texto_fila = "/{simbolo}"`: buscando el contrato, ninguna
  orden real se habría confirmado. Risk $10 dejó el stop 8 ticks arriba
  (8 x $1.25): el bracket es en dólares **de la posición entera**.
- **Fila en el DOM** (medida con una segunda posición abierta): MUI DataGrid,
  `[role='row'][data-id]` **dentro** del panel (Orders y Trades son tablas del
  mismo tipo). `leer_posiciones` real devolvió `('-1',)` para M6E. Con esto
  **TopstepX quedó completa** y el bot ya opera ahí (en `dry_run` hasta la
  Etapa 4).
- ~~Falta medir confirmación y bracket~~: ver la sección de abajo, del 12/11.

### PC FUTVAN, 12/11/2026: TopstepX cambió y el SL/TP pasó a la fila

TopstepX se actualizó ese día (versión 2.4.36.2):

- **Login unificado con el Dashboard**: la sesión del perfil se invalidó. El
  primer dry run respondió "sesión no iniciada" con captura, sin llenar nada:
  el chequeo de `sesion_iniciada` funcionó. Hubo que repetir el login con
  `abrir_perfil.py`.
- **El diálogo de brackets ahora tiene CANCEL / SAVE CHANGES** (aparecen al
  editar). El bot lo cerraba con la X: **descartaba los valores**, y la
  verificación pasaba igual porque leía los inputs antes de cerrar. Medido:
  tras un dry run, Risk/Profit vacíos y "Automatically apply" desmarcado. **Una
  orden real habría salido sin SL ni TP.** Lección: verificar lo GUARDADO, no
  lo escrito.
- **Solución: como el operador.** La entrada sale sin bracket, y el SL/TP se
  carga en la **fila de la posición**. Se midió con una orden real que puso el
  usuario (long 1 M6E en TOP1), con un script que solo observaba:
  - las celdas son `[data-field='risk']` y `[data-field='toMake']`, cada una
    con un `svg[data-icon='pencil']`;
  - con doble clic la celda pasa a `MuiDataGrid-cell--editing` y aparece un
    `input[type=number]` vacío;
  - Enter confirma, y la celda muestra `$5.00`;
  - son dólares de **toda la posición**: To Make $5 dejó el TP 4 ticks arriba.
- **Cómo lo hace el bot:** etapa nueva `pasos_despues`, con selectores
  **relativos a la fila** (`fila_de`) y acción nueva `doble_click`.
  - Corre solo después de confirmar la posición, nunca en dry run.
  - No mira el stop: con la posición abierta, cortar ahí la dejaría sin SL.
- **Si falla, o si ya había posición en ese contrato:** `ResultadoOrden.sin_proteccion`.
  - Manda **un** ERROR "POSICION ABIERTA SIN SL/TP CONFIRMADO", que va al grupo de errores.
  - La cuenta queda bloqueada por el resto del lote.
  - Nunca cierra nada solo.
- **TopstepX no pide confirmación después de BUY** (orden del usuario).
- **Falta en vivo:** los `pasos_despues` del bot todavía no corrieron con una
  posición real. La primera orden real del bot es la prueba.
- **Edge recuerda la posición de la ventana** en el perfil: después de que el bot
  la abre fuera de pantalla, `abrir_perfil.py` la abre en (-32000, -32000) y
  el operador no la ve. Hubo que moverla con SetWindowPos. Arreglo pendiente.
- **Al recargar** (limpieza después de un dry run), TopstepX vuelve al layout
  guardado del usuario (6EZ26 x3 ese día). No es riesgo para el bot, que
  escribe y verifica contrato y cantidad antes de cada clic.
- El prefijo de esta PC pasó a **`FUTVAN7`**: un número por máquina, para sumar más.

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

1. ~~Paso `verificar`~~: hecho (11/11/2026), junto con `repetir`, `marcar`,
   `escribir` con `teclear`, los marcadores `{riesgo_usd}`, `{ganancia_usd}`,
   `{contratos_menos_1}` y `{cuenta_ultimos4}`, y el tope `max_riesgo_usd`.
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
