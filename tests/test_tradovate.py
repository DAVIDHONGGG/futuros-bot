"""Tradovate (medido en la PC FUTVAN5, 13/11/2026): lo que cambio en el bot.

  - Una fila con NET POS 0 NO es una posicion: Tradovate deja la fila del
    contrato aunque no haya nada abierto.
  - Autologin: la sesion web se pierde al cerrar el navegador. El bot entra
    solo, pero UNICAMENTE si el navegador autocompleto todos los campos (la
    contrasena la cargo el operador a mano); nunca escribe en ellos.
  - El SL/TP sale con la entrada (bracket del ticket) y despues se relee,
    sin tocar nada, que quedo guardado (verificar_despues).
  - {lado} / {lado_opuesto}: "Buy"/"Sell" como los muestra el ticket.
"""

import copy
import json
import logging
import unittest
import unittest.mock
from pathlib import Path

from tests.apoyo import FLOTA_PRUEBA, config_de_prueba, dir_temporal, local_con, trabajo
from tests.navegador_falso import PlaywrightFalso, fabrica

import ejecutor
from ejecutor_web import EjecutorWeb
from pasos import leer_posiciones, validar_plataforma, valores_de

RAIZ = Path(__file__).resolve().parent.parent

LOGIN = {
    "campos_autorrelleno": ["#usuario", "#clave"],
    "boton": "#entrar",
    "pasos_al_entrar": [
        {"accion": "esperar", "selector": "#modo-simulacion"},
        {"accion": "click", "selector": "#modo-simulacion"},
    ],
}

VERIFICAR_DESPUES = [
    {"accion": "esperar", "selector": "#bracket-stop"},
    {"accion": "verificar", "selector": "#bracket-stop",
     "contiene": "{lado_opuesto} {contratos} {contrato} STP at"},
    {"accion": "verificar", "selector": "#bracket-stop", "contiene": "Working"},
]


def plataforma(**cambios):
    p = copy.deepcopy(FLOTA_PRUEBA["plataformas"]["tradovate"])
    p.update(copy.deepcopy(cambios))
    return p


class TestCeroNoEsPosicion(unittest.TestCase):

    def setUp(self):
        self.pw = PlaywrightFalso()
        self.pw.chromium.launch_persistent_context()
        self.page = self.pw.pagina()
        self.conf = FLOTA_PRUEBA["plataformas"]["tradovate"]["posiciones"]

    def test_una_fila_en_cero_no_cuenta(self):
        for cero in ("0", "0.00", "-0", " 0 "):
            with self.subTest(cero=cero):
                self.page.posiciones = {"MNQZ6": cero}
                self.assertEqual(leer_posiciones(self.page, self.conf, "MNQZ6", 100), ())

    def test_las_demas_cantidades_si(self):
        self.page.posiciones = {"MNQZ6": "-1"}
        self.assertEqual(leer_posiciones(self.page, self.conf, "MNQZ6", 100), ("-1",))
        # Algo que no es un numero no se descarta: mejor de mas que de menos.
        self.page.posiciones = {"MNQZ6": "?"}
        self.assertEqual(leer_posiciones(self.page, self.conf, "MNQZ6", 100), ("?",))

    def test_con_fila_en_cero_la_orden_no_cree_que_ya_habia_posicion(self):
        # El bug que se habria visto en Tradovate: antes=('0',) -> "ya habia
        # posicion" -> nunca se ponia el SL/TP en la fila.
        flota = copy.deepcopy(FLOTA_PRUEBA)
        flota["plataformas"]["tradovate"]["pasos_despues"] = [
            {"accion": "verificar", "selector": ".risk", "igual": "20", "numerico": True}]
        cfg = config_de_prueba(flota=flota, local=local_con(dry_run=False))

        def preparar(pagina):
            pagina.posiciones["MNQZ6"] = "0"

            def comprar(pag, selector):
                if selector == "#comprar":
                    pag.posiciones["MNQZ6"] = "1"
            pagina.efecto_clic = comprar
            pagina.textos["#posiciones .fila[fila=MNQZ6] >> .risk"] = "$20.00"
        pw = PlaywrightFalso(preparar_pagina=preparar)
        ej = EjecutorWeb(cfg, fabrica=fabrica(pw), dir_diagnosticos=dir_temporal())
        self.addCleanup(ej.cerrar)
        res = ej.colocar_lote([dict(trabajo(), riesgo_usd="20", ganancia_usd="40")])[0]
        self.assertIs(res.confirmado, True)
        self.assertIsNone(res.sin_proteccion)
        self.assertIn("[] -> ['1']", res.detalle)


class TestMarcadoresDeLado(unittest.TestCase):

    def test_buy_y_sell_con_mayuscula(self):
        v = valores_de(trabajo(accion="buy"), None)
        self.assertEqual((v["accion"], v["lado"], v["lado_opuesto"]), ("buy", "Buy", "Sell"))
        v = valores_de(trabajo(accion="sell"), None)
        self.assertEqual((v["accion"], v["lado"], v["lado_opuesto"]), ("sell", "Sell", "Buy"))

    def test_se_aceptan_en_los_pasos(self):
        p = plataforma(verificar_despues=VERIFICAR_DESPUES)
        p["pasos_orden"].append({"accion": "verificar", "selector": "#lado", "igual": "{lado}"})
        self.assertEqual(validar_plataforma("x", p), [])


class TestValidacion(unittest.TestCase):

    def problemas(self, **cambios):
        return " | ".join(validar_plataforma("x", plataforma(**cambios)))

    def test_login_completo_es_valido(self):
        self.assertEqual(self.problemas(login=LOGIN), "")

    def test_login_sin_campos_o_sin_boton(self):
        login = dict(LOGIN, campos_autorrelleno=[])
        self.assertIn("campos_autorrelleno", self.problemas(login=login))
        login = dict(LOGIN, boton="TODO: boton de login")
        self.assertIn("'login.boton' sin configurar", self.problemas(login=login))
        login = dict(LOGIN, campos_autorrelleno=["#usuario", "TODO"])
        self.assertIn("campo sin configurar", self.problemas(login=login))

    def test_en_el_login_nunca_se_escribe(self):
        for accion in ({"accion": "escribir", "selector": "#clave", "valor": "x"},
                       {"accion": "tecla", "tecla": "Enter"},
                       {"accion": "click_texto", "texto": "Login"}):
            with self.subTest(accion=accion["accion"]):
                login = dict(LOGIN, pasos_al_entrar=[accion])
                self.assertIn("no permitida en el login", self.problemas(login=login))

    def test_verificar_despues_es_solo_lectura(self):
        self.assertEqual(self.problemas(verificar_despues=VERIFICAR_DESPUES), "")
        for accion in ({"accion": "click", "selector": "#x"},
                       {"accion": "escribir", "selector": "#x", "valor": "1"}):
            with self.subTest(accion=accion["accion"]):
                p = self.problemas(verificar_despues=VERIFICAR_DESPUES + [accion])
                self.assertIn("solo verificar y esperar", p)

    def test_fila_o_ticket_no_los_dos(self):
        p = self.problemas(verificar_despues=VERIFICAR_DESPUES,
                           pasos_despues=[{"accion": "click", "selector": "#x"}])
        self.assertIn("no en los dos", p)


class _ConEjecutor(unittest.TestCase):

    def crear(self, dry_run=True, extra=None, preparar=None):
        flota = copy.deepcopy(FLOTA_PRUEBA)
        flota["plataformas"]["tradovate"].update(copy.deepcopy(extra or {}))
        cfg = config_de_prueba(flota=flota, local=local_con(dry_run=dry_run))
        self.diag = dir_temporal()
        self.pw = PlaywrightFalso(preparar_pagina=preparar)
        self.ej = EjecutorWeb(cfg, fabrica=fabrica(self.pw), dir_diagnosticos=self.diag)
        self.addCleanup(self.ej.cerrar)
        self.addCleanup(ejecutor.limpiar_aborto)


class TestAutologin(_ConEjecutor):

    def preparar_login(self, autorrelleno=("#usuario", "#clave"), entra=True):
        def preparar(pagina):
            pagina.sin_sesion = True
            for campo in autorrelleno:
                pagina.textos[campo] = True

            def efecto(pag, selector):
                if selector == "#modo-simulacion" and entra:
                    pag.sin_sesion = False
            pagina.efecto_clic = efecto
        return preparar

    def test_con_autorrelleno_entra_y_sigue_con_la_orden(self):
        self.crear(extra={"login": LOGIN}, preparar=self.preparar_login())
        with self.assertLogs("bot_futuros.ejecutor_web", level="WARNING") as logs:
            res = self.ej.colocar_lote([trabajo()])[0]
        self.assertIsNone(res.error)
        self.assertIsNone(res.confirmado)              # dry_run: ticket lleno, sin clic
        clics = self.pw.clics()
        self.assertEqual(clics[:2], ["#entrar", "#modo-simulacion"])
        self.assertNotIn("#comprar", clics)
        self.assertTrue(any("sesion recuperada con autologin" in m for m in logs.output))

    def test_nunca_escribe_ni_teclea_en_los_campos_del_login(self):
        self.crear(extra={"login": LOGIN}, preparar=self.preparar_login())
        self.ej.colocar_lote([trabajo()])
        tocados = [s for _, a, s, _ in self.pw.registro
                   if a in ("escribir", "teclear", "tecla", "click") and s in ("#usuario", "#clave")]
        self.assertEqual(tocados, [])

    def test_si_falta_un_campo_autocompletado_no_hace_clic(self):
        self.crear(extra={"login": LOGIN}, preparar=self.preparar_login(autorrelleno=("#usuario",)))
        res = self.ej.colocar_lote([trabajo()])[0]
        self.assertIn("no autocompleto #clave", res.error)
        self.assertIn("edge://settings/passwords", res.error)
        self.assertEqual(self.pw.clics(), [])
        self.assertTrue(list(self.diag.glob("*-TV1-login-sin-autorrelleno.png")))

    def test_si_despues_del_login_no_hay_sesion_corta_sin_operar(self):
        self.crear(extra={"login": LOGIN}, preparar=self.preparar_login(entra=False))
        res = self.ej.colocar_lote([trabajo()])[0]
        self.assertIn("la sesion no aparecio", res.error)
        self.assertNotIn("#comprar", self.pw.clics())
        self.assertTrue(list(self.diag.glob("*-TV1-login-fallido.png")))

    def test_un_stop_mientras_se_mira_el_autorrelleno_no_hace_clic(self):
        # El stop llega DURANTE el autologin (no antes del lote): lo tiene que
        # ver el chequeo justo antes del clic de login.
        import ejecutor_web

        def autorrellenado_y_stop(page, selector, timeout_ms):
            ejecutor.solicitar_aborto()
            return True
        self.crear(extra={"login": LOGIN}, preparar=self.preparar_login())
        with unittest.mock.patch.object(ejecutor_web, "_autorrellenado", autorrellenado_y_stop):
            res = self.ej.colocar_lote([trabajo()])[0]
        self.assertTrue(res.omitida)
        self.assertIn("login", res.detalle)
        self.assertEqual(self.pw.clics(), [])

    def test_con_sesion_no_toca_el_login(self):
        self.crear(extra={"login": LOGIN})
        self.ej.colocar_lote([trabajo()])
        self.assertNotIn("#entrar", self.pw.clics())
        self.assertEqual([s for _, a, s, _ in self.pw.registro if a == "leer"
                          and s in ("#usuario", "#clave")], [])

    def test_sin_login_configurado_sigue_pidiendo_el_login_a_mano(self):
        self.crear(preparar=lambda p: setattr(p, "sin_sesion", True))
        res = self.ej.colocar_lote([trabajo()])[0]
        self.assertIn("abrir_perfil.py TV1", res.error)


class TestVerificarDespues(_ConEjecutor):

    def preparar_compra(self, ya_habia=None, linea="#722 Sell 1 MNQZ6 STP at 7500.00 - Working"):
        def preparar(pagina):
            if ya_habia:
                pagina.posiciones["MNQZ6"] = ya_habia

            def comprar(pag, selector):
                if selector == "#comprar":
                    pag.posiciones["MNQZ6"] = str(int(pag.posiciones.get("MNQZ6", "0")) + 1)
                    pag.textos["#bracket-stop"] = linea
            pagina.efecto_clic = comprar
        return preparar

    def test_relee_lo_guardado_y_confirma(self):
        self.crear(dry_run=False, extra={"verificar_despues": VERIFICAR_DESPUES},
                   preparar=self.preparar_compra())
        res = self.ej.colocar_lote([trabajo()])[0]
        self.assertIs(res.confirmado, True)
        self.assertIsNone(res.sin_proteccion)
        self.assertIn("SL/TP puestos", res.detalle)
        # Solo lectura: despues del clic de Comprar, ningun otro clic.
        self.assertEqual(self.pw.clics()[-1], "#comprar")

    def test_si_no_quedo_working_alerta_y_bloquea_la_cuenta(self):
        self.crear(dry_run=False, extra={"verificar_despues": VERIFICAR_DESPUES},
                   preparar=self.preparar_compra(linea="#722 Sell 1 MNQZ6 STP at 7500.00 - Rejected"))
        with self.assertLogs("bot_futuros.ejecutor_web", level="ERROR") as logs:
            res = self.ej.colocar_lote([trabajo(), trabajo()])
        self.assertIs(res[0].confirmado, True)
        self.assertIn("Working", res[0].sin_proteccion)
        self.assertTrue(res[0].bloquea_la_cuenta)
        self.assertTrue(res[1].omitida)
        self.assertEqual(self.pw.clics().count("#comprar"), 1)
        self.assertTrue(any("POSICION ABIERTA SIN SL/TP" in r.getMessage()
                            for r in logs.records if r.levelno >= logging.ERROR))

    def test_el_bracket_es_de_la_orden_aunque_ya_hubiera_posicion(self):
        # A diferencia de la fila (TopstepX), el bracket del ticket cubre ESTA
        # orden: una posicion previa no impide verificarlo.
        self.crear(dry_run=False, extra={"verificar_despues": VERIFICAR_DESPUES},
                   preparar=self.preparar_compra(ya_habia="1"))
        res = self.ej.colocar_lote([trabajo()])[0]
        self.assertIs(res.confirmado, True)
        self.assertIsNone(res.sin_proteccion)

    def test_en_dry_run_no_relee_nada(self):
        self.crear(dry_run=True, extra={"verificar_despues": VERIFICAR_DESPUES},
                   preparar=self.preparar_compra())
        res = self.ej.colocar_lote([trabajo()])[0]
        self.assertIsNone(res.confirmado)
        self.assertEqual([s for _, a, s, _ in self.pw.registro if s == "#bracket-stop"], [])

    def test_un_stop_despues_del_clic_no_corta_la_verificacion(self):
        def preparar(pagina):
            self.preparar_compra()(pagina)
            efecto = pagina.efecto_clic

            def comprar_y_stop(pag, selector):
                efecto(pag, selector)
                if selector == "#comprar":
                    ejecutor.solicitar_aborto()
            pagina.efecto_clic = comprar_y_stop
        self.crear(dry_run=False, extra={"verificar_despues": VERIFICAR_DESPUES},
                   preparar=preparar)
        res = self.ej.colocar_lote([trabajo()])[0]
        self.assertIsNone(res.sin_proteccion)
        self.assertTrue([s for _, a, s, _ in self.pw.registro
                         if a == "leer" and s == "#bracket-stop"])


class TestConfigRealDeTradovate(unittest.TestCase):
    """El bloque tradovate de config.json, tal como quedo medido."""

    PELIGROSOS = ("market-replay-button", "purchase", "logout", "go to replay",
                  "add-bracket", "bracket-name-select", "exit", "buy mkt", "sell mkt",
                  "buy bid", "account-item", "disable-tpsl")

    def setUp(self):
        flota = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))
        self.tv = flota["plataformas"]["tradovate"]

    def todos_los_pasos(self):
        listas = ("pasos_seleccionar_cuenta", "pasos_orden", "pasos_confirmar",
                  "pasos_limpiar", "verificar_despues")
        for lista in listas:
            for paso in self.tv.get(lista, []):
                yield lista, paso
        for paso in self.tv["login"]["pasos_al_entrar"]:
            yield "login", paso

    def test_valida_completa(self):
        self.assertEqual(validar_plataforma("tradovate", self.tv), [])

    def test_send_es_el_unico_clic_final_para_los_dos_lados(self):
        self.assertEqual(self.tv["boton_comprar"], "[data-testid='order-ticket-send-button']")
        self.assertEqual(self.tv["boton_vender"], self.tv["boton_comprar"])
        for lista, paso in self.todos_los_pasos():
            with self.subTest(lista=lista, paso=paso):
                self.assertNotIn("send-button", paso.get("selector", ""))

    def test_ningun_paso_toca_algo_peligroso(self):
        textos = [self.tv["login"]["boton"]]
        for lista, paso in self.todos_los_pasos():
            textos.append(json.dumps({k: v for k, v in paso.items() if not k.startswith("_")}))
        for texto in textos:
            with self.subTest(texto=texto):
                self.assertFalse([x for x in self.PELIGROSOS if x in texto.lower()])

    def test_el_lado_se_verifica_justo_antes_del_clic(self):
        ultimo = self.tv["pasos_orden"][-1]
        self.assertEqual(ultimo["accion"], "verificar")
        self.assertEqual(ultimo["igual"], "{lado}")
        self.assertIn("order-ticket-action-switch", ultimo["selector"])

    def test_cada_campo_escrito_se_relee(self):
        pasos = self.tv["pasos_orden"]
        verificados = {p["selector"]: p["igual"] for p in pasos if p["accion"] == "verificar"}
        for p in pasos:
            if p["accion"] == "escribir":
                with self.subTest(selector=p["selector"]):
                    self.assertEqual(verificados.get(p["selector"]), p["valor"])

    def test_sl_tp_en_ticks_y_marcados(self):
        pasos = self.tv["pasos_orden"]
        escritos = {p["selector"]: p["valor"] for p in pasos if p["accion"] == "escribir"}
        self.assertEqual(escritos["[data-testid='simple-tpsl-bracket-0-take-profit-input'] input"],
                         "{tp_ticks}")
        self.assertEqual(escritos["[data-testid='simple-tpsl-bracket-0-stop-loss-input'] input"],
                         "{sl_ticks}")
        # Las casillas se EXIGEN marcadas (clase del label): "marcar" no sirve,
        # el input esta tapado y no refleja el estado (dry run 13/11 14:22).
        self.assertEqual([p for p in pasos if p["accion"] == "marcar"], [])
        exigidas = [p["selector"] for p in pasos if p["accion"] == "esperar"
                    and "checkbox-label" in p["selector"]]
        self.assertEqual(len(exigidas), 2)
        self.assertTrue(all("[class*='checkbox-active']" in s for s in exigidas))
        unidades = [p for p in pasos if "show-in-units" in p.get("selector", "")]
        self.assertEqual(unidades[0]["igual"], "Ticks")

    def test_contrato_antes_que_cantidad(self):
        # Medido: cambiar el simbolo VACIA la cantidad.
        sel = [p.get("selector", "") for p in self.tv["pasos_orden"]]
        simbolo = sel.index("[data-testid='order-ticket-symbol-search']")
        cantidad = [i for i, s in enumerate(sel) if s.startswith("[data-testid='order-ticket-quantity']")]
        self.assertTrue(cantidad)
        self.assertLess(simbolo, min(cantidad))

    def test_el_contrato_se_vacia_antes_de_escribirlo(self):
        # Medido 13/11/2026: al entrar, Tradovate restaura el ultimo ticket, y
        # escribir el MISMO contrato no despliega la sugerencia.
        pasos = self.tv["pasos_orden"]
        sel = [p.get("selector", "") for p in pasos]
        escribir = sel.index("[data-testid='order-ticket-symbol-search']")
        anterior = pasos[escribir - 1]
        self.assertEqual(anterior["accion"], "click_si_aparece")
        self.assertIn("typeahead-clear", anterior["selector"])
        self.assertIn("order-ticket-main-entry", anterior["selector"])
        # La sugerencia: por texto (no exacto: 'M6EZ6' va suelto junto a la
        # descripcion), acotada a la lista, y el campo se verifica despues.
        sugerencia, verificacion = pasos[escribir + 1], pasos[escribir + 2]
        self.assertEqual(sugerencia["accion"], "click_texto")
        self.assertEqual(sugerencia["texto"], "{contrato}")
        self.assertIs(sugerencia["exacto"], False)
        self.assertTrue(sugerencia["selector"].endswith("ul.dropdown-menu"))
        self.assertEqual((verificacion["accion"], verificacion["igual"]), ("verificar", "{contrato}"))

    def test_la_cantidad_se_abre_con_la_flecha_no_con_el_input(self):
        # Dry run 13/11/2026 14:19: el clic al centro del combo cae en el
        # input y la lista no se abre. La abre el boton de la flecha (div.btn).
        clics = [p["selector"] for p in self.tv["pasos_orden"] if p["accion"] == "click"]
        self.assertIn("[data-testid='order-ticket-quantity'] .btn", clics)
        self.assertNotIn("[data-testid='order-ticket-quantity']", clics)

    def test_despues_de_enviar_solo_relee_stop_y_limite_working(self):
        pasos = self.tv["verificar_despues"]
        self.assertTrue(all(p["accion"] in ("verificar", "esperar") for p in pasos))
        texto = json.dumps(pasos)
        for esperado in ("STP at", "LMT at", "Working", "MKT - Filled", "{lado_opuesto}"):
            self.assertIn(esperado, texto)

    def test_login_sin_escribir_y_en_simulacion(self):
        login = self.tv["login"]
        self.assertEqual(login["campos_autorrelleno"], ["#name-input", "#password-input"])
        clics = [p["selector"] for p in login["pasos_al_entrar"] if p["accion"] == "click"]
        self.assertEqual(clics, ["[data-testid='simulation-button']"])
        for lista, paso in self.todos_los_pasos():
            with self.subTest(lista=lista, paso=paso):
                self.assertNotIn("name-input", paso.get("selector", ""))
                self.assertNotIn("password", paso.get("selector", "").lower())

    def test_posiciones_y_contrato_de_un_digito(self):
        self.assertEqual(self.tv["posiciones"]["texto_fila"], "{contrato}")
        self.assertEqual(self.tv["simbolos"]["M6E"], "M6EZ6")


if __name__ == "__main__":
    unittest.main()
