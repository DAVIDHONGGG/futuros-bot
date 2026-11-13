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
| `ejecutor_web.py` | Playwright en un único hilo dueño del navegador; autologin |
| `pasos.py` | Validación de plataformas e intérprete de pasos (`login`, `verificar_despues`) |
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
  plantillas de píxeles a MT5 y se lleva sus teclas. Medido en FUTVAN7
  (12/11/2026): fuera de pantalla y sin robar el foco. Igual, en FUTVAN7 el
  usuario eligió **ventana visible** (`"no_robar_foco": false` en el
  `config.local.json`), para poder ver la orden y cerrar a mano si algo falla.
  Es clave local: se decide por máquina.

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
- El bot nunca **escribe ni lee** una contraseña. Por defecto el login se hace
  a mano en el perfil de cada cuenta (`abrir_perfil.py`). Con `login` en la
  plataforma (Tradovate, decisión del usuario del 13/11/2026) hay
  **autologin**: el operador cargó usuario y contraseña a mano en
  `edge://settings/passwords` del Edge del perfil, Edge los autocompleta, y el
  bot solo hace clic en Login si **todos** los campos están marcados como
  autocompletados (`:autofill`, se lee un booleano). Si falta uno, no hace
  clic. `pasos.py` no deja escribir ni teclear en los pasos del login.

## Estado (11/11/2026)

- **Etapa 1** (listener, parser, ejecutor falso) y **Etapa 2** (ejecutor web,
  solo colocar órdenes): hechas. 286 tests en verde (12/11/2026: tope de
  riesgo y SL/TP en dólares en la fila de la posición).
- **En vivo en TopstepX** desde el 12/11/2026 (PC FUTVAN7): orden real con
  SL/TP en la fila, verificada de punta a punta. Ver "PC FUTVAN, 12/11/2026".
- **Tradovate medido completo** el 13/11/2026 en la PC FUTVAN5 (ver "PC
  FUTVAN5"): selectores, autologin, bracket del ticket. 318 tests en verde.
  Falta el dry run real y la primera orden del bot.
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
- **Segunda orden real del bot (14:26): entró y puso el SL, pero no el TP.**
  Mientras se editaba To Make apareció el diálogo **"Switch Chart Symbol"**
  (CANCEL/SWITCH, "Don't show me again") y tapó la celda. El bot avisó
  "POSICION ABIERTA SIN SL/TP" y no cerró nada; el usuario cargó el TP a mano.
  - **Qué lo dispara (medido headless con la posición abierta, sin Enter):**
    un **clic simple** en cualquier parte de la fila (lápiz, símbolo,
    cantidad), 3 de 3. El doble clic en el lápiz no lo dispara.
  - El culpable era `escribir` con `teclear`, que hace clic en el input.
  - **Arreglo:** en `pasos_despues`, `escribir` sin `teclear`. `fill()`
    escribe en el input ya enfocado sin dialogo (medido: valor '5' y foco en
    el input).
  - **Por las dudas:** acción nueva `click_si_aparece` (busca en la
    página, espera `ms` y si no aparece sigue). Se usa con el CANCEL del
    diálogo antes de cada Enter y antes de verificar. Ese selector lo cerró 3 de 3.
- **Primera orden real del bot: se cortó antes de BUY** y no se colocó nada.
  Si el ticket ya tiene el contrato, TopstepX **no filtra** el autocompletado
  (muestra los 51), y el clic en "la opción" chocó con el modo estricto. El
  dry run anterior había pasado de casualidad: el ticket tenía 6EZ26. Ahora se
  hace `click_texto {contrato}` dentro de `.MuiAutocomplete-popper`
  (`click_texto` acepta `selector` para acotar). Medido: 1 sola coincidencia
  con el mismo contrato, con otro y de vuelta, y los pasos reales corrieron
  4 veces seguidas sin BUY.
- **Tercera orden real del bot (14:45, `M6E buy 1 4 4 TOP2`): OK de punta a
  punta.** Con el arreglo anterior (89d834e) y la ventana visible:
  - mensaje 14:45:32, clic en BUY 14:45:40, posición `[] -> ['1']` 14:45:41;
  - "SL/TP puestos y verificados en la fila" (riesgo 5 / ganancia 5 USD) a
    las 14:45:43: 2,6 s para las dos celdas, releídas después del Enter;
  - el diálogo "Switch Chart Symbol" **no apareció** (`click_si_aparece`
    no dejó su WARNING): el arreglo de fondo es escribir sin clic, y el
    CANCEL queda de red;
  - sin capturas de error.
  Con esto la Etapa 4 quedó probada en TopstepX: Telegram -> orden -> SL/TP
  en la fila. `dry_run` lo maneja el usuario.
- **Edge recuerda la posición de la ventana** en el perfil: después de que el bot
  la abre fuera de pantalla, `abrir_perfil.py` la abre en (-32000, -32000) y
  el operador no la ve. Hubo que moverla con SetWindowPos. Arreglado:
  `abrir_perfil` fuerza `--window-position=0,0` y `--window-size` al tamaño
  de la pantalla. Medido con un perfil descartable: sin los args quedaba en
  (-1570, 0), con 30 px a la vista; con los args, en (0, 0).
- **Al recargar** (limpieza después de un dry run), TopstepX vuelve al layout
  guardado del usuario (6EZ26 x3 ese día). No es riesgo para el bot, que
  escribe y verifica contrato y cantidad antes de cada clic.
- El prefijo de esta PC pasó a **`FUTVAN7`**: un número por máquina, para sumar más.

### PC FUTVAN5: Tradovate medido (13/11/2026)

Mini PC con el bot de MT5 corriendo al lado (pyautogui y plantillas de
píxeles en 1920x1080). Opera **solo Tradovate**: prefijo `FUTVAN5`, bot
@FUTVAN5_bot, cuenta `TRA1` = `MFFUSFBLDR700298009` (un solo login, una
cuenta, "Demo & Active"), `max_riesgo_usd: 800`, ventana **visible**.
Python313 es el mismo del bot de MT5: Playwright se instaló después de un
`pip install --dry-run` que mostró que solo **agregaba** paquetes.

- **La app de escritorio no sirve.** Es Electron 27 (Chromium 118) y carga la
  interfaz desde archivos locales (`app.asar`), no desde la web. Playwright
  para Python no tiene API de Electron, y `connect_over_cdp` falla: "Browser
  context management is not supported" (medido con una segunda instancia,
  sin login). Se usa `https://trader.tradovate.com/` en Edge.
- **La sesión web se pierde al cerrar el navegador** (2 de 2). Recargar la
  página la conserva, y convive con la app de escritorio abierta con el
  mismo login. Con el navegador abierto aguantó al menos 14 minutos sin
  cortes (medición corta). Por eso el autologin; `abrir_perfil.py` no sirve
  para Tradovate (hace login y cierra).
- **Autologin:** Edge **no ofrece** guardar la contraseña ("Ofrecer guardar"
  activado y aun así nada: no detecta el formulario). Cargada a mano en
  `edge://settings/passwords`, Edge abierto por Playwright marca
  `#name-input` y `#password-input` como `:autofill` en ~4 s, y el clic en
  Login entra directo, **sin captcha**. Después viene `/trading-mode`:
  "Start Simulated Trading" (`simulation-button`). **Al lado está
  "Purchase" (`market-replay-button`), que compra Market Replay.** En el
  menú de cuentas están "Go to Replay" y "Logout". Los tests prohíben
  todos esos selectores.
- **Workspace:** "Order Ticket" y "Positions" son pestañas del **mismo
  panel**, así que no se ven a la vez. El operador movió Positions a su
  propio panel. Si queda activa la pestaña "Orders", el ticket desaparece
  del DOM: el primer paso hace clic en `text=Order Ticket`.
- **Positions deja la fila del contrato con NET POS 0** sin posición. El bot
  la tomaba como "ya había posición" y nunca habría puesto el SL/TP.
  Arreglado: `leer_posiciones` no cuenta cantidades 0.
- **SL/TP = bracket del ticket**, no la fila (el usuario había dicho "fila";
  lo medido manda). "Enable Bracket Orders" queda activado entre órdenes,
  en **ticks**, que es justo el formato de Telegram. Sale con la entrada,
  así que nunca hay posición sin protección. Después de Send el panel
  muestra el **historial** de la orden: "Buy 1 M6EZ6 MKT - Filled - 1/1" y
  las dos órdenes del bracket "Sell … STP at 1.1033 - Working" / "LMT at
  1.1041 - Working". Eso es lo guardado, y lo relee `verificar_despues`
  (acción nueva, solo lectura). Medido con 2 órdenes reales del operador,
  fill 1.1037 y 4/4 ticks.
- **Send es el mismo botón para comprar y vender**: el lado se elige en un
  switch Buy/Sell (marcadores nuevos `{lado}` y `{lado_opuesto}`) y se
  verifica justo antes del clic. **Cambiar el símbolo vacía la cantidad**:
  primero el contrato, después la cantidad.
- **En esta PC la ventana del bot SÍ toma el foco** al abrirse, aun con
  `ARGS_NO_ROBAR_FOCO` (2 de 2). En FUTVAN7 se había medido lo contrario.
  Pasa una vez por apertura del navegador.
- **Dry run local (13/11/2026, 14:18–14:24).** Corre con el ejecutor real y
  sin Telegram (`parse_orden` → `expandir_lote` → `colocar_lote`):
  - El autologin funcionó 5 de 5, en 6–8 s.
  - Dos ajustes salieron del dry run, medidos en el DOM sin clic:
    - **El combo de cantidad se abre con su flecha** (`div.btn`): el clic al
      centro cae en el input y la lista no se abre.
    - **Las casillas TP/SL no se pueden "marcar"**: el input está tapado por
      la casilla dibujada y dice desmarcado aunque se vea el tilde. Se
      **exige** la clase `checkbox-active` del label.
  - `M6E buy 1 4 4` y `M6E sell 1 6 8` llenaron el ticket entero y se
    detuvieron antes de Send.
  - En la venta, el ticket calculó TP 8 ticks abajo (1.1031) y SL 6 arriba
    (1.1045) con el último en 1.1039: **la plataforma toma lo que escribe
    el bot**.
  - Reset vacía contrato y cantidad.
  - La fila con NET POS 0 se leyó como "ninguna".
- **Dry run por Telegram (13/11/2026, 14:34–14:51).** Las pruebas se
  hicieron en un **chat privado** con @FUTVAN5_bot, no en el grupo: ahí
  escuchan FUTVAN7, que opera en vivo, y el bot de MT5. Para el prefijo
  ajeno se usó `FUTVAN99`, que no existe; nunca el de otra máquina.
  - El primer intento se cortó antes de Send. **Tradovate restaura el último
    ticket al entrar** (M6EZ6, 1, Sell, MARKET), y escribir el mismo contrato
    no despliega la sugerencia.
  - Arreglo, medido en el DOM:
    - vaciar el campo con su "×" (`typeahead-clear`);
    - con el campo vacío aparece "Popular Instruments" (encabezado y 9
      contratos), así que el clic en "el li" chocó con el modo estricto;
    - la sugerencia es `li > a > div`, con "M6EZ6" suelto y un div hijo
      "E-Micro Euro": ningún elemento dice exactamente el contrato. Se elige
      con `click_texto` **no exacto** dentro de `ul.dropdown-menu` y después
      se verifica el campo.
  - Con eso, `FUTVAN5 M6E buy 1 4 4 TRA1` tardó 17 s del mensaje al ticket
    completo, autologin incluido, y se detuvo antes de Send.
  - `FUTVAN99 …` y la misma línea sin prefijo quedaron "ignorado".
- Sin medir todavía:
  - que el **precio** del STP/LMT guardado esté a SL/TP ticks del fill
    (después de Send se verifica que existan y estén "Working", no la
    distancia);
  - el cambio entre varias cuentas;
  - cuánto dura la sesión con el navegador abierto por horas.

### Lo que se vio en las plataformas (capturas del usuario, 11/11/2026)

(Lo de Tradovate de esta sección quedó superado por "PC FUTVAN5" de arriba.)

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
3. **Etapa 4**: ~~orden real mínima~~ hecha en TopstepX (12/11/2026, ver
   "PC FUTVAN"). Falta `INSTALAR-MAQUINA-NUEVA.md`, con las verificaciones
   en orden: tests, check_setup, valor efectivo de la config, dry run con
   prefijo propio, que ignore prefijo ajeno y orden sin prefijo, y recién ahí
   una orden real mínima.
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
