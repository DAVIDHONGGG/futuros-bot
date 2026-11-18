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

## Alias de las cuentas y perfiles

- **El alias dice la FIRMA y un número**, no la plataforma: `MFF1`, `MFF2`
  (My Funded Futures), `LUC1` (Lucid), `TFY1` (Tradeify), `TOP1` (Topstep).
  En Tradovate pueden convivir varias firmas, y `TRA1` no diría cuál es.
- El perfil del navegador, si la cuenta no dice otra cosa, es
  `perfiles/<ALIAS>`. **Al renombrar una cuenta** hay que poner
  `"perfil": "perfiles/<ALIAS_VIEJO>"`: si no, el bot abre un perfil vacío
  y se pierde el login (en Tradovate, la contraseña guardada en Edge y con
  ella el autologin). Si la cuenta ya tenía `"perfil"`, se conserva.
- **Cada login de Tradovate necesita su propio perfil**, con su usuario y
  contraseña cargados a mano por el operador en `edge://settings/passwords`
  de ese perfil (ver "Autologin" en "PC FUTVAN5").
- **Las cuentas que comparten login comparten perfil** (como `TOP1`..`TOP5`
  en `perfiles\TOPSTEPX`). El ejecutor abre un navegador por perfil, no por
  cuenta. En Tradovate, el bot elige la cuenta en el desplegable antes de
  cada orden (medido con 5 cuentas en un login, ver "PC FUTVAN4").
- Medir con `python config.py` después de cualquier cambio: la cuenta tiene
  que aparecer con el perfil esperado y sin "Cuenta descartada".

### La cuenta por NÚMERO completo (18/11/2026)

El usuario lleva en un Excel el número de cada cuenta. Su código arma las
órdenes, y cuando una cuenta muere la reemplaza otra con un número nuevo.
Por eso CUENTA, en la orden, puede ser **el alias o el número completo**
(`cuenta_plataforma`): `FUTVAN7 M6E buy 1 4 8 668214-39660124`.

- **Número de una cuenta configurada**: es esa cuenta. Si en la misma orden
  van el alias y su número, cuenta una sola vez.
- **Número que no está en `cuentas`**: se arma con el molde
  `cuenta_nueva` del `config.local.json`, con el número como alias:
  `{"plataforma": "topstepx", "max_contratos": 50, "perfil": "perfiles/TOPSTEPX"}`.
  - Es una clave local y el `perfil` es obligatorio (el del login de la
    máquina).
  - **Solo sirve con un login por máquina.** Con dos logins, el número no
    dice qué perfil usar.
- **Sin molde, un número nuevo se rechaza** ("cuenta desconocida", y el
  mensaje dice que falta `cuenta_nueva`).
- **Qué se toma como número**: algo con un tramo de 5 dígitos
  (`NUMERO_DE_CUENTA` en `config.py`). Así un alias mal escrito (`TOP6`)
  sigue siendo "desconocida" y no se toma por una cuenta nueva.
- **Un número mal escrito no opera.** El bot elige la cuenta en la
  plataforma por ese número y la verifica antes del clic. Si no existe,
  falla ahí.
- **`all` y `close all` son solo las cuentas de `cuentas`.** Una cuenta que
  entró por el molde no se suma. Para operar todas las del Excel, el código
  manda los números separados por coma.
- **Mayúsculas:** el número se pasa a mayúsculas. Todos los medidos lo son
  (TopstepX `668214-…`, Lucid `LFE…`, Tradeify `FTDFY…`, MFF `MFFU…`).

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
- **TopstepX con autologin "Continue with Google"** desde el 16/11/2026 (PC
  FUTVAN7, ver "PC FUTVAN7, 16/11/2026"). 331 tests en verde.
- **Tradovate con varias cuentas en un login** desde el 16/11/2026 (PC
  FUTVAN4, Lucid, ver "PC FUTVAN4"): `pasos_seleccionar_cuenta` cambia de
  cuenta en el desplegable; el ticket y Positions siguen a la cuenta elegida
  (orden real del operador, 17/11/2026). NET POS se busca por el nombre de
  la columna. 359 tests en verde.
- **Tradeify en Tradovate, en vivo** desde el 17/11/2026 (PC FUTVAN8, ver
  "PC FUTVAN8"): primera orden real del bot con el bracket verificado. El
  bloque `tradovate` sirvió sin cambios. 363 tests en verde.
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

### PC FUTVAN7, 16/11/2026: autologin de TopstepX con Google

TopstepX pasó a la versión 2.4.38.2 y cambió el login: `/trade` sin sesión
redirige a `login.topstep.com`.

- **Medido con un perfil vacío y descartable**, headless y sin clic. La página
  tiene el email, "Continue to TopstepX", **"Continue with Google"** y
  "Continue with Facebook". Los botones no tienen `data-testid` y su `id`
  cambia en cada carga, por eso el selector va por texto.
- **El perfil del bot seguía con sesión ese día**: abrió directo en el
  trading y los selectores que usa el bot seguían iguales. El login nuevo
  solo afecta cuando la sesión vence.
- **Se usa el mismo `login` que armó FUTVAN5 para Tradovate**, con
  `"sin_contrasena": true` y sin `campos_autorrelleno`. En la página no hay
  contraseña que autocompletar: el bot solo hace clic en el botón de Google y
  espera `sesion_iniciada`. La validación no deja mezclar `sin_contrasena`
  con campos, y solo `true` lo activa.
  - Se hizo así porque las dos PCs armaron un autologin el mismo día.
  - La primera versión de FUTVAN7, una lista aparte `pasos_login`, se
    descartó al integrar para no tener dos mecanismos.
- **Si Google pide algo**, la sesión no aparece. Entonces la orden falla con
  la captura `login-fallido` y no se toca el ticket.
- **TODO, sin medir:** qué hace Google con el navegador automatizado. El
  11/11 bloqueó el login ("This browser or app may not be secure"), y no
  sabemos si muestra el selector de cuenta. Se mide la primera vez que la
  sesión venza de verdad: buscar en el log "autologin con el boton" y mirar
  la captura.

### Ritmo de la entrada en TopstepX (16/11/2026)

El usuario pidió que la entrada salga **con calma y más natural**: unos 12
s desde que empieza la orden hasta el clic en BUY. Antes tardaba ~8 s,
incluyendo abrir el navegador.

- **Clave nueva `ritmo`**, opcional por plataforma:
  `{"segundos_hasta_el_clic": 12, "variacion_s": 2}`.
  - Cada orden sortea entre 10 y 14 s.
  - Ese tiempo se reparte en pausas **desparejas** antes de cada paso (de
    0,4 a 1,6 veces la parte que le toca a cada uno).
  - La última pausa, justo antes del clic, completa lo que falte.
  - Si abrir el navegador ya se comió el tiempo, no espera más.
- **Solo hasta el clic.** El SL/TP de la fila (`pasos_despues`) no espera:
  con la posición abierta se pone lo antes posible.
- **Un stop corta durante las pausas**: espera en tramos de 100 ms
  (`esperar_con_aborto`).
- **Corre también en dry run**: así se ve el ritmo sin colocar nada.
- **En un lote, cada orden lleva su propio ritmo.** Con `all` y 5 cuentas,
  el lote tarda ~1 minuto.
- **Validación:** entre 0 y 60 s, y la variación tiene que ser menor que el
  total.
- **Tradovate no tiene ritmo**: sus pausas son pasos `esperar` medidos.

### PC FUTVAN5: Tradovate medido (13/11/2026)

Mini PC con el bot de MT5 corriendo al lado (pyautogui y plantillas de
píxeles en 1920x1080). Opera **solo Tradovate**: prefijo `FUTVAN5`, bot
@FUTVAN5_bot, cuenta `MFF1` = `MFFUSFBLDR700298009` (un solo login, una
cuenta, "Demo & Active"), `max_riesgo_usd: 800`, ventana **visible**.
La cuenta se llamó `TRA1` hasta el 16/11/2026; se renombró a `MFF1` por la
convención de alias (ver "Alias de las cuentas y perfiles"). Tiene
`"perfil": "perfiles/TRA1"` en el `config.local.json`: sin eso el bot
buscaría `perfiles\MFF1` (vacío) y se perdería la contraseña cargada en
Edge, o sea el autologin.
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
  - Con eso, `FUTVAN5 M6E buy 1 4 4 TRA1` (hoy `MFF1`) tardó 17 s del mensaje al ticket
    completo, autologin incluido, y se detuvo antes de Send.
  - `FUTVAN99 …` y la misma línea sin prefijo quedaron "ignorado".
- Sin medir todavía:
  - ~~que el **precio** del STP/LMT guardado esté a SL/TP ticks del fill~~:
    medido en FUTVAN8 con Tradeify (fill 26996.50, LMT 26986.50 y STP
    27006.50 con 40/40 ticks). El bot sigue verificando que existan y
    estén "Working", no la distancia;
  - cuánto dura la sesión con el navegador abierto por horas.
- ~~TODO: segunda cuenta en el mismo login~~: resuelto en FUTVAN4
  (16/11/2026), ver "PC FUTVAN4". Hasta entonces `pasos_seleccionar_cuenta`
  solo verificaba la cuenta y no la cambiaba. **Después del update hay que
  probar en esta PC** que la orden de `MFF1` siga pasando: con una sola
  cuenta, el bot elige la que ya está (en FUTVAN4 eso no tuvo efectos).

### PC FUTVAN4: Lucid en Tradovate, varias cuentas en un login (16/11/2026)

PC VAN4, con el bot de MT5 corriendo al lado (no se tocó). Opera **solo
Tradovate**, cuentas de **Lucid**: prefijo `FUTVAN4`, bot @FUTVAN4_bot,
`LUC1`..`LUC5` = `LFE05089433290001`, `…0003`, `…0005`, `…0006` y `…0007`.
Las cinco están en **un solo login** y comparten el perfil `perfiles/LUCID`.
`max_riesgo_usd: 800`, `max_contratos: 1` ("por ahora", usuario),
`cuenta_por_defecto: null` (con cinco cuentas no se adivina), ventana
**visible**.

- **Lucid usa la misma web que MFF**, `https://trader.tradovate.com/`. El
  login, `/trading-mode` con "Start Simulated Trading" (`simulation-button`)
  y `balance-view-panel` son iguales que en FUTVAN5: el bloque `tradovate`
  compartido sirve tal cual, sin separar por firma.
- **Autologin:** funcionó igual que en FUTVAN5. Ojo: **Edge pide la
  credencial de Windows para editar** una contraseña guardada (para
  agregarla no). Con una contraseña mal cargada y sin esa credencial, la
  salida fue un perfil nuevo; el viejo quedó como `perfiles\LUCID_viejo`.
- **`cuenta_plataforma` es el número a secas**, el mismo que muestra el
  disparador del desplegable en su `.name`.
- **El desplegable de cuenta** (medido sin Send):
  - Es único. Su disparador `[data-toggle='dropdown']` lo abre **y lo
    cierra**; **Escape no lo cierra**.
  - El menú (`ul.dropdown-menu`) lista cada cuenta como
    `li[data-testid='account-item-<cuenta>']`, con el número exacto en
    `div.main`. Después vienen "Manage Groups", "Go to Replay" y "Logout",
    **con la misma clase `a.account`**: nunca elegir por clase.
  - Con el menú abierto, el texto del contenedor trae **todas** las
    cuentas: un "contiene" sobre el contenedor pasaría con cualquiera.
- **Elegir una cuenta** la cambia en 0,3 s, sin recargar ni navegar; el menú
  se cierra solo y el EQUITY pasa a la cuenta nueva.
- **`pasos_seleccionar_cuenta` (bloque compartido) ahora cambia de cuenta:**
  clic en el disparador, esperar el menú, `click_texto` **exacto** de
  `{cuenta_plataforma}` **dentro del menú** (así nunca cae en Logout ni en
  Replay; los selectores no admiten marcadores, por eso no se usa el
  testid), esperar 1 s y `verificar` que el `.name` del disparador sea
  **igual** a la cuenta. Probado en vivo con los pasos reales:
  - de `LUC2` a `LUC1`: OK en 1,3 s, sin navegación, menú cerrado, EQUITY
    de la cuenta nueva;
  - `LUC1` ya elegida (el caso de MFF con una cuenta): OK en 1,2 s, menú
    cerrado, nada cambió.
- **Workspace de Lucid:** "Order Ticket", "Accounts" y "Positions" son
  pestañas del mismo panel, como venía en FUTVAN5, y hay tres carteles de
  novedades abiertos ("Lock accounts individually", "Plan exits on the Order
  ticket", "Risk settings"). El operador tiene que mover Positions a su
  propio panel y cerrar los carteles antes de la primera orden; si no, el
  bot falla sin operar.
- **Orden real del operador (17/11/2026, Buy 1 MNQZ6 desde el Order
  Ticket en LUC1):**
  - **El ticket manda a la cuenta elegida arriba**: la fila quedó con
    ACCOUNT `LFE05089433290001`.
  - **Positions muestra solo la cuenta elegida**: con LUC2 elegida quedó
    vacío y al volver a LUC1 reapareció la fila. No hace falta filtrar
    por cuenta.
  - El interruptor S/A está en la pestaña **Order Ticket** (sin medir qué
    hace; queda en S). El de Positions cambia entre tabla y vista
    **compacta**, que esconde los encabezados y **no sirve** para el bot.
- **Con varias cuentas, Positions agrega la columna ACCOUNT** en segundo
  lugar y NET POS pasa al tercero: el `nth=1` fijo leía la cuenta. Ahora
  NET POS se busca **por el nombre de la columna** (`columna_cantidad` +
  `encabezados` en `posiciones`), sin contar la flecha de orden
  ("ACCOUNT ▼", medido). Y la lectura de **antes del clic** frena si la
  cantidad no es un número (`exigir_numero`). Medido en vivo: NET POS en el
  índice 2, la fila en 0 se lee como "ninguna". **FUTVAN5 tiene que hacer
  un dry run después del update**: la lectura es antes del clic.
- **Primer dry run por Telegram (17/11/2026, `MNQ buy 1 4 4 LUC1`):**
  autologin, cuenta y Positions bien, pero se cortó en el clic de Buy del
  ticket: la **lista de sugerencias del símbolo se había vuelto a abrir**
  y lo tapaba. Medido con los pasos reales en el Edge de medición:
  - La **primera búsqueda de un contrato después de entrar** llega tarde:
    ~1–3 s después de elegirlo, la lista se abre otra vez con ese contrato
    y queda abierta (MNQZ6, MESZ6, MYMZ6, M2KZ6, MGCZ6, M6AZ6). Una pausa
    antes de elegir **no** alcanza. **Un segundo clic en el contrato la
    cierra** (lo indicó el usuario; medido): `click_si_aparece` acepta
    ahora `texto` (+ `exacto`), igual que `click_texto`, y espera 3 s.
  - Con el campo vacío la lista trae las **búsquedas recientes** y
    "Popular Instruments": el contrato puede estar dos veces y el clic a
    ~1,7 s chocó con el modo estricto. Ahora hay 1,5 s de pausa entre
    teclear y elegir (7 de 7 bien).
  - Con los dos cambios, 4 de 4 dejaron Buy/Sell libres (leído con
    `elementFromPoint`, sin clic), con contrato nuevo y repetido. El símbolo
    tarda ~3,5 s si la lista se reabre y ~6,5 s si no (espera los 3 s).
- **Dry run por Telegram con los arreglos (17/11/2026, chat privado):**
  - `MNQ buy 1 4 4 LUC1`: autologin, ticket completo (MNQZ6, Buy, 1,
    MARKET, TP/SL 4 ticks) y frenó antes de Send. 33 s del mensaje al
    ticket, con el navegador y el autologin.
  - `MNQ sell 1 4 4 LUC2`: **cambió a LUC2** (EQUITY de LUC2, Positions
    vacío), Sell, ticket completo, frenó antes de Send. 19 s.
  - **El bracket del ticket es por cuenta**: en LUC2 estaba apagado y el
    bot lo prendió con `simple-tpsl-order-ticket-enable-tpsl-button`.
- En FUTVAN4 no se instaló todavía el listener como tarea programada.

### PC FUTVAN8: Tradeify en Tradovate (17/11/2026)

PC VAN8, con el bot de MT5 corriendo al lado (`C:\mt5-bot`, tarea `mt5-bot
listener`; no se tocó). Opera **solo Tradovate**, cuentas de **Tradeify**:
prefijo `FUTVAN8`, bot @FUTVAN8_bot, `TFY1` = `FTDFYSLX50527729629` (un
solo login, una cuenta por ahora, perfil `perfiles/TRADEIFY` para que las
TFY que se sumen lo compartan). `max_riesgo_usd: 800`, `max_contratos: 1`,
ventana **visible**. Python313 es el del bot de MT5: Playwright 1.64.1 se
instaló después de un `pip install --dry-run` que solo **agregaba**
greenlet, playwright y pyee.

- **El número que da Tradeify no es el de Tradovate.** El operador pasó
  `TDFYU175192319` (su id de Tradeify: aparece en la columna COMMENT de las
  órdenes); el desplegable dice `FTDFYSLX50527729629` ("Demo & Active"),
  en el `.name` del disparador y en `account-item-…`. `cuenta_plataforma`
  es lo que dice el desplegable: con el otro, el `verificar` de la cuenta
  frena antes del ticket.
- **Misma plataforma, no la misma pantalla que MFF.** `/trading-mode` dice
  "Your user is associated with Tradeify…", y al lado de "Start Simulated
  Trading" (`simulation-button`, igual) el botón dice **"Access Market
  Replay"** (no "Purchase"), con el mismo `market-replay-button`. Hay un
  banner "Accept Cookies" que no molesta. El `login` del bloque compartido
  sirvió tal cual: autologin 7 de 7, ~9 s hasta la sesión.
- **Workspace por defecto de Tradeify:** gráfico con su barra (Buy Mkt /
  Sell Mkt, "TP/SL: Strategy 1": el bot no la usa) y Accounts abajo; **sin
  Order Ticket ni Positions**, y dos carteles ("Lock accounts
  individually", "Risk settings are here now"). El operador agregó Positions
  y Order Ticket en paneles propios, cerró los carteles y maximizó la
  ventana. El layout se conservó en cada reapertura del navegador (la
  sesión no, por eso el autologin).
- **Positions de Tradeify:** 4 columnas (`SYMBOL`, `NET POS`, `NET PRICE`,
  `OPEN P/L, $`) y **sin fila** cuando no hay posición. NET POS por nombre
  de columna sirve igual.
- **El Order Ticket tiene scroll propio** y no se puede agrandar más: con
  el bracket prendido, "Take profit / stop loss" queda debajo de Send. No
  hace falta: Playwright desplaza el panel solo, para escribir los ticks y
  para el clic en Send (dry run local y orden real, medido).
- **El bracket estaba apagado** en la cuenta nueva (como en LUC2); se
  prendió a mano y queda prendido entre órdenes.
- **Diálogo de confirmación (13:15):** después de Send apareció "Sell 1
  MKT?" con "[Buy 1 STP at MKT+1.00]" / "[Buy 1 LMT at MKT-1.00]", botones
  **Sell / Cancel** y "DO NOT SHOW AGAIN" marcado. `pasos_confirmar` está
  vacío: la orden **no salió** y el bot avisó "no confirmada" (correcto).
  En las órdenes siguientes no volvió a aparecer. **TODO, sin medir:** el
  selector del botón del diálogo en el DOM; si vuelve a salir, el bot falla
  sin operar.
- **Falsa alarma "SIN SL/TP" con un bracket chico (13:17, 4/4 ticks):**
  entrada 27053.25, el TP (LMT 27052.25) se llenó en segundos y el STP pasó
  a "Canceled" antes de que `verificar_despues` buscara "STP at … -
  Working". La posición ya estaba cerrada. **TODO:** aceptar una pata
  "Filled" y la otra "Canceled" cuando la posición volvió a 0.
- **Orden real del bot OK de punta a punta (13:23, `MNQ sell 1 40 40
  TFY1`, navegador ya abierto):** clic en Send 13:23:23,5, posición `[] ->
  ['-1']` en 0,4 s y "SL/TP del bracket verificados" a los 0,7 s del clic.
  En el resumen: fill 26996.50, LMT 26986.50 y STP 27006.50, o sea **40/40
  ticks exactos**. Cerró después por el TP.
- **Flecha del resumen (`icon-back`):** después de una orden confirmada el
  ticket queda en el resumen. A las 13:23 el formulario ya estaba a la
  vista (el `click_si_aparece` de la flecha no la encontró y no hizo falta;
  sin medir por qué volvió solo). A las 13:27, con el resumen de la orden
  anterior en pantalla, el selector de FUTVAN5 **la encontró y volvió al
  formulario** (paso 2, 0,1 s antes de la "×"). Esa orden (`MNQ sell 1 40
  40 TFY1`) también salió completa: Send 13:27:16,7, `[] -> ['-1']` en
  0,1 s y bracket verificado a los 0,24 s del clic.
- Dry run por Telegram (13:06, chat privado): `FUTVAN8 MNQ sell 1 4 4
  TFY1` llenó el ticket y frenó antes de Send en 32 s. La compra, el
  prefijo ajeno y la línea sin prefijo **no llegaron** al listener (no
  quedó rastro en el log): sin probar en vivo en esta PC.
- **Falta:** la tarea programada `bot-futuros listener` (el listener corrió
  desde una sesión de Claude) y los dos TODO de arriba.

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
