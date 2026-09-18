"""
La posicion que el dron CREE tener y con la que navega.

Es una pieza distinta del ancla inercial de la capa 2, y la separacion es
deliberada. El ancla es un DETECTOR: su filtro corrige siempre contra el
satelite, porque lo que necesita medir es cuanto discrepa el satelite de la
inercia. Este modulo es la SOLUCION DE NAVEGACION: cuando se decide que el
satelite miente, tiene que dejar de escucharlo, que es exactamente lo contrario.

Confundir las dos cosas rompe el sistema en los dos sentidos. Si el detector
deja de corregir, diverge y nunca se recupera. Si el navegador sigue
corrigiendo contra un satelite comprometido, el ataque funciona igual aunque se
lo haya detectado: se sabe que hay un ataque y se obedece lo mismo.

Tres modos, uno por decision de la maquina de estados:

    SATELITAL   se le cree al satelite (NOMINAL, SOSPECHA)
    COLECTIVO   se navega con la posicion trilaterada desde los pares confiables
    INERCIAL    no hay fuente absoluta creible: rumbo inercial, y la deriva
                se acumula de verdad (AISLADO, REPLIEGUE)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

SATELITAL = "satelital"
COLECTIVO = "colectivo"
INERCIAL = "inercial"


@dataclass
class EstimadorNavegacion:
    # Cuan rapido se corrige contra una fuente absoluta. Mas alta que la del
    # detector porque acá el objetivo es seguir bien la referencia, no medir
    # cuanto discrepa.
    ganancia_posicion: float = 1.2
    ganancia_sesgo: float = 0.02
    pos: np.ndarray | None = None
    sesgo_velocidad: np.ndarray = field(default_factory=lambda: np.zeros(3))
    segundos_a_ciegas: float = 0.0

    def actualizar(
        self,
        dt: float,
        pos_satelital: np.ndarray,
        vel_inercial: np.ndarray,
        modo: str,
        fijo_colectivo: np.ndarray | None = None,
    ) -> np.ndarray:
        pos_satelital = np.asarray(pos_satelital, dtype=float)
        if self.pos is None:
            self.pos = pos_satelital.copy()
            return self.pos

        # Propagacion inercial, siempre. Es lo unico que nunca depende de nadie.
        vel = np.asarray(vel_inercial, dtype=float) - self.sesgo_velocidad
        self.pos = self.pos + vel * dt

        referencia: np.ndarray | None = None
        if modo == SATELITAL:
            referencia = pos_satelital
        elif modo == COLECTIVO and fijo_colectivo is not None:
            referencia = np.asarray(fijo_colectivo, dtype=float)

        if referencia is not None:
            innovacion = referencia - self.pos
            self.pos = self.pos + self.ganancia_posicion * dt * innovacion
            self.sesgo_velocidad = (
                self.sesgo_velocidad - self.ganancia_sesgo * dt * innovacion
            )
            self.segundos_a_ciegas = 0.0
        else:
            # Sin referencia absoluta la deriva se acumula y nadie la corrige.
            # Es el costo real del estado de repliegue, y tiene que verse.
            self.segundos_a_ciegas += dt

        return self.pos
