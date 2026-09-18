"""
Recuperacion de posicion por trilateracion.

Es lo que convierte al sistema en navegacion resiliente en vez de una alarma:
cuando un dron deja de creerle a su satelite, sus companeros confiables pasan
a ser sus satelites. Se conoce la posicion de cada uno de ellos y la distancia
medida por radio hasta cada uno; con cuatro pares alcanza de sobra.

Gauss-Newton sobre los residuos de distancia, resuelto con numpy. No hace
falta scipy para esto: son cuatro ecuaciones, tres incognitas y media docena
de iteraciones.
"""

from __future__ import annotations

import numpy as np


def trilaterar(
    anclas: np.ndarray,
    distancias: np.ndarray,
    altura_barometrica: float,
    inicial: np.ndarray | None = None,
    iteraciones: int = 20,
    tolerancia: float = 1e-6,
    residuo_maximo: float = 2.0,
) -> tuple[np.ndarray, bool]:
    """
    anclas             : (m, 3) posiciones de los pares confiables.
    distancias         : (m,)   distancia medida hasta cada uno de ellos.
    altura_barometrica : altura propia medida por presion.

    Devuelve (posicion, valida).

    LA ALTURA SE FIJA CON EL BAROMETRO Y SOLO SE RESUELVEN DOS INCOGNITAS.
    No es una simplificacion: es lo que hace que el resultado sea unico.

    Cuatro drones de una formacion son casi coplanares, y contra un conjunto de
    anclas coplanares las distancias admiten DOS soluciones -- la verdadera y
    su reflejo al otro lado de ese plano -- que ajustan exactamente igual de
    bien. Ninguna comprobacion sobre los residuos puede distinguirlas, porque
    el reflejo cierra perfecto. Medido: al aceptar el reflejo, el nodo aislado
    se iba a 500 m de su consigna con el error de recuperacion creciendo hasta
    775 m, y cada ciclo empeoraba el siguiente.

    Hace falta informacion que no venga de las distancias, y el barometro es
    justamente eso: una medicion independiente, que no se puede falsificar por
    radio, y que el sistema ya tiene a bordo para la capa 2. Fijando la altura,
    la ambiguedad de reflexion desaparece por construccion.
    """
    anclas = np.asarray(anclas, dtype=float)
    distancias = np.asarray(distancias, dtype=float)

    if anclas.shape[0] < 3:
        partida = anclas.mean(axis=0) if anclas.size else np.zeros(3)
        return partida, False

    z = float(altura_barometrica)
    if inicial is not None:
        p = np.array([inicial[0], inicial[1]], dtype=float)
    else:
        p = anclas.mean(axis=0)[:2]

    for _ in range(iteraciones):
        actual = np.array([p[0], p[1], z])
        delta = actual - anclas  # (m, 3)
        normas = np.linalg.norm(delta, axis=1)
        normas = np.where(normas < 1e-9, 1e-9, normas)

        residuo = normas - distancias
        # Solo las dos columnas horizontales: la altura no es incognita.
        jacobiano = (delta / normas[:, None])[:, :2]

        paso, *_ = np.linalg.lstsq(jacobiano, -residuo, rcond=None)
        p = p + paso

        if np.linalg.norm(paso) < tolerancia:
            break

    solucion = np.array([p[0], p[1], z])
    comprobacion = np.linalg.norm(solucion - anclas, axis=1) - distancias
    rms = float(np.sqrt(np.mean(comprobacion**2)))
    return solucion, bool(rms <= residuo_maximo)


def error_de_recuperacion(
    posicion_recuperada: np.ndarray, posicion_real: np.ndarray
) -> float:
    """Metrica del banco: cuanto se aleja la posicion recuperada de la verdad."""
    return float(np.linalg.norm(np.asarray(posicion_recuperada) - np.asarray(posicion_real)))
