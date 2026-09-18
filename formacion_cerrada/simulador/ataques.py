"""
Banco de ataques.

Cuatro clases, y no son igual de interesantes. El salto sirve para calibrar; la
deriva gradual es lo que haria un atacante competente; la traslacion coherente
es el caso B y el corazon del proyecto; el par bizantino es donde se justifica
que la decision se tome por mediana y que los nodos sean cinco y no tres.

Todos los ataques operan sobre lo que el receptor INFORMA, nunca sobre la
posicion real: el dron sigue fisicamente donde esta, lo que cambia es lo que
cree. Esa distincion es el ataque entero.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SIN_ATAQUE = "none"
SALTO = "step"
DERIVA = "drift_d5"
COHERENTE = "coherent"
BIZANTINO = "byzantine"

OBJETIVO_INDIVIDUAL = 4  # D-05
OBJETIVO_MENTIROSO = 3  # D-04


@dataclass
class Ataque:
    """
    Estado del ataque en curso. `t` es el tiempo transcurrido desde que se
    inyecto, que es lo que permite que las rampas crezcan.
    """

    tipo: str = SIN_ATAQUE
    t: float = 0.0
    # Rampa del arrastre individual y del coherente.
    tasa_deriva: float = 0.5  # m/s
    tasa_coherente: float = 1.6  # m/s
    salto_m: float = 40.0
    # Cuanto infla sus distancias el nodo mentiroso.
    mentira_m: float = 6.0

    def inyectar(self, tipo: str) -> None:
        self.tipo = tipo
        self.t = 0.0

    def avanzar(self, dt: float) -> None:
        if self.tipo != SIN_ATAQUE:
            self.t += dt

    def aplicar_a_posicion(self, gnss: np.ndarray) -> np.ndarray:
        """Falsea lo que informan los receptores satelitales."""
        gnss = gnss.copy()

        if self.tipo == SALTO:
            # Offset instantaneo en un solo nodo. El caso facil: sirve para
            # calibrar el umbral, no para el pitch.
            gnss[OBJETIVO_INDIVIDUAL] += np.array([self.salto_m, 0.0, 0.0])

        elif self.tipo == DERIVA:
            # Arrastre gradual de un solo nodo. Este es el que hay que mostrar:
            # es lo que haria alguien que sabe lo que hace, y la metrica
            # interesante es el tiempo hasta la deteccion contra la tasa.
            corrimiento = self.tasa_deriva * self.t
            gnss[OBJETIVO_INDIVIDUAL] += np.array([corrimiento, 0.0, 0.0])

        elif self.tipo in (COHERENTE, BIZANTINO):
            # EL CASO B. Los CINCO receptores reciben el mismo desplazamiento,
            # asi que las distancias entre pares no cambian ni un milimetro y
            # la capa 1 no ve absolutamente nada. Es el momento del pitch.
            corrimiento = self.tasa_coherente * self.t
            gnss += np.array([corrimiento * 0.8, -corrimiento * 0.6, 0.0])

        return gnss

    def aplicar_a_distancias(self, filas: np.ndarray) -> np.ndarray:
        """
        Falsea las distancias que un nodo comprometido PUBLICA.

        Solo se toca la fila del mentiroso: las distancias que los demas miden
        hacia el siguen siendo correctas. Esa asimetria es la que lo delata, y
        es la razon de que cada par se mida desde las dos puntas.
        """
        if self.tipo != BIZANTINO:
            return filas
        filas = filas.copy()
        filas[OBJETIVO_MENTIROSO, :] += self.mentira_m
        filas[OBJETIVO_MENTIROSO, OBJETIVO_MENTIROSO] = 0.0
        return filas


DESCRIPCIONES = {
    SALTO: "Salto en escalon de +40 m en D-05",
    DERIVA: "Arrastre gradual de D-05 a 0,5 m/s",
    COHERENTE: "Traslacion coherente de los cinco receptores (caso B)",
    BIZANTINO: "Traslacion coherente + D-04 falseando sus distancias",
}
