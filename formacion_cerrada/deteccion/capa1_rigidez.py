"""
Capa 1: rigidez de la formacion.

Lo que un dron calcula por su cuenta con los datos que todos difunden por la
malla. Es la capa rapida y precisa, y la que atrapa el spoofing individual o
parcial.

Es tambien la capa CIEGA al caso B, por construccion y no por implementacion:
ver la explicacion en geometria.residuos_rigidez.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .geometria import residuos_rigidez, umbral_robusto


@dataclass
class VotoCapa1:
    """El veredicto que UN dron calcula y difunde por la malla."""

    emisor: int
    residuos: np.ndarray
    vectores: np.ndarray
    vectores_completo: np.ndarray
    umbral: float
    mediana: float
    traslacion: np.ndarray
    rango_efectivo: int
    excluido: int | None = None

    @property
    def sospechosos(self) -> np.ndarray:
        """Mascara booleana: que nodos superan el umbral robusto."""
        return self.residuos > self.umbral

    @property
    def formacion_degenerada(self) -> bool:
        """
        Con rango efectivo 1 los drones estan practicamente alineados y la
        reconstruccion no queda determinada. Informar un residuo ahi seria
        inventar un numero: conviene decir que la geometria no alcanza.
        """
        return self.rango_efectivo < 2


def evaluar_rigidez(
    emisor: int,
    P_sat: np.ndarray,
    D_rad: np.ndarray,
    k: float = 3.5,
    excluir: int | None = None,
) -> VotoCapa1:
    """
    Corre la prueba de rigidez y arma el voto de este dron.

    P_sat : posiciones que los n drones informan desde el satelite.
    D_rad : distancias medidas entre pares, consolidadas desde la malla.
    """
    v_completo, v_excluido, traslacion, rango = residuos_rigidez(
        P_sat, D_rad, excluir=excluir
    )
    umbral, mediana, _ = umbral_robusto(np.linalg.norm(v_excluido, axis=1), k=k)
    return VotoCapa1(
        emisor=emisor,
        residuos=np.linalg.norm(v_excluido, axis=1),
        vectores=v_excluido,
        vectores_completo=v_completo,
        umbral=umbral,
        mediana=mediana,
        traslacion=traslacion,
        rango_efectivo=rango,
        excluido=excluir,
    )
