"""
Capa 3: consenso robusto.

Existe porque en cuanto hay votacion hay que suponer que alguien vota de mala
fe. Un protocolo de consenso clasico no sirve aca: los de tolerancia a fallos
toleran CAIDAS, y el nodo problematico de este escenario no se cae -- miente,
con formato valido y a horario.

Dos mecanismos, y son distintos:

  ASIMETRIA DE PARES  Cada distancia se mide DOS veces, una por cada punta del
                      par. Un nodo que falsea las distancias que publica
                      contradice a sus companeros: para el par (i,j), lo que
                      informa i deja de coincidir con lo que informa j. Eso
                      identifica al mentiroso sin necesidad de votar nada,
                      porque el otro extremo de cada par es testigo.

  VOTACION POR MEDIANA  Los n drones corren la misma prueba de rigidez con los
                      mismos datos y votan. La decision se toma por mediana y
                      no por unanimidad porque el dron enganado tambien vota,
                      y va a votar que esta todo bien. Con cinco nodos se
                      tolera uno; con tres, un solo voto de mala fe es un
                      tercio del total y rompe la mediana.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class EstadoCapa3:
    quorum: int
    total: int
    expulsados: np.ndarray  # mascara booleana
    asimetria_por_nodo: np.ndarray
    votos_por_nodo: np.ndarray  # cuantos votantes marcaron a cada nodo

    @property
    def etiqueta_quorum(self) -> str:
        return f"{self.quorum}/{self.total}"


def asimetria_de_pares(filas_difundidas: np.ndarray) -> np.ndarray:
    """
    Detecta al nodo que falsea las distancias que publica.

    filas_difundidas[i][j] es la distancia al nodo j que el nodo i DICE haber
    medido. Para un par honesto, filas[i][j] y filas[j][i] coinciden salvo
    ruido de medicion. Un mentiroso discrepa contra sus cuatro companeros A LA
    VEZ, y es esa simultaneidad -- no la magnitud -- lo que lo delata.

    Devuelve la discrepancia MEDIANA de cada nodo contra el resto.

    Tiene que ser la mediana y no el promedio, y la diferencia no es sutil:
    cuando un nodo miente, cada companiero honesto tiene exactamente UN par
    contaminado de sus cuatro, el que lo une al mentiroso. Con promedio, esa
    unica discrepancia grande dividida por cuatro alcanza para llevar a todos
    los honestos por encima del umbral, y el sistema expulsa al enjambre
    entero. Con mediana, el mentiroso queda con sus cuatro pares malos y los
    honestos con uno solo de cuatro, que la mediana ignora.
    """
    n = filas_difundidas.shape[0]
    resultado = np.zeros(n)
    for i in range(n):
        discrepancias = [
            abs(filas_difundidas[i, j] - filas_difundidas[j, i])
            for j in range(n)
            if j != i
        ]
        resultado[i] = float(np.median(discrepancias)) if discrepancias else 0.0
    return resultado


def consolidar_distancias(filas_difundidas: np.ndarray) -> np.ndarray:
    """
    Arma una matriz de distancias simetrica a partir de lo que difunde cada uno.

    Se toma el MINIMO de las dos mediciones de cada par, no el promedio. La
    razon es fisica: la obstruccion de la linea de vista siempre ALARGA el
    camino aparente, nunca lo acorta, asi que entre dos mediciones del mismo
    par la mas corta es la menos contaminada. De paso, un mentiroso que infla
    sus distancias queda neutralizado; uno que las acorta, no, y para ese esta
    la prueba de asimetria.
    """
    D = np.minimum(filas_difundidas, filas_difundidas.T)
    np.fill_diagonal(D, 0.0)
    return D


def consenso_por_mediana(
    votos: np.ndarray, umbral_asimetria: float, asimetria: np.ndarray
) -> EstadoCapa3:
    """
    votos[v][n] : True si el votante v considera sospechoso al nodo n.

    Un nodo queda expulsado si lo marca la MAYORIA de los votantes, o si su
    asimetria de pares lo delata como mentiroso. Los dos caminos son
    necesarios: la votacion atrapa al enganado, la asimetria atrapa al que
    miente a proposito.
    """
    votos = np.asarray(votos, dtype=bool)
    n_votantes, n_nodos = votos.shape

    conteo = votos.sum(axis=0)
    por_mayoria = conteo > (n_votantes / 2.0)
    por_asimetria = asimetria > umbral_asimetria

    expulsados = por_mayoria | por_asimetria
    quorum = int(n_nodos - expulsados.sum())

    return EstadoCapa3(
        quorum=quorum,
        total=n_nodos,
        expulsados=expulsados,
        asimetria_por_nodo=asimetria,
        votos_por_nodo=conteo,
    )
