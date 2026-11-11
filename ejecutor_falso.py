"""
Ejecutor FALSO: no abre ningun navegador, solo registra en el log lo que
haria. Es el de la Etapa 1, para probar el listener de punta a punta (Telegram,
prefijos, stop, programadas) sin riesgo.
"""

import logging

from ejecutor import EjecutorBase, ResultadoOrden

log = logging.getLogger("bot_futuros.ejecutor_falso")


class EjecutorFalso(EjecutorBase):
    nombre = "falso"

    def _colocar_una(self, trabajo: dict, etiqueta: str) -> ResultadoOrden:
        log.info(
            "EJECUTOR FALSO (%s): colocaria %s %s x%d de %s en %s [%s], SL %d "
            "ticks, TP %d ticks. No se toca ningun navegador.",
            "dry_run" if self.dry_run else "EN VIVO pedido",
            trabajo["accion"], trabajo["contrato"], trabajo["contratos"],
            trabajo["simbolo"], trabajo["cuenta"], trabajo["plataforma"],
            trabajo["sl_ticks"], trabajo["tp_ticks"])
        return ResultadoOrden(etiqueta, trabajo["cuenta"], confirmado=None,
                              detalle="ejecutor falso, solo log")

    def _cerrar_cuenta(self, alias: str) -> int:
        log.info("EJECUTOR FALSO: cerraria las posiciones de %s. No se toca "
                 "ningun navegador.", alias)
        return 0

    def salud(self) -> list:
        return ["ejecutor FALSO (no opera)"]
