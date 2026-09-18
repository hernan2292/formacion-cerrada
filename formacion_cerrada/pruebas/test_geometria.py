"""
Pruebas de la prueba de rigidez.

La cuarta prueba es la mas importante del proyecto: demuestra que la capa 1
es CIEGA a la traslacion coherente. No es un defecto que se esconde, es una
propiedad matematica de la construccion, y es la razon de que exista la capa 2.
"""

import numpy as np

from formacion_cerrada.deteccion.geometria import (
    kabsch,
    mds_clasico,
    rango_efectivo,
    residuos_rigidez,
    umbral_robusto,
)

# Formacion en V de cinco nodos, TODA A LA MISMA ALTURA (z = 40).
# Es coplanar a proposito: asi vuelan las formaciones de verdad, y es el
# caso que rompe una implementacion ingenua de Procrustes.
FORMACION_V = np.array(
    [
        [0.0, 4.5, 40.0],
        [-6.0, 0.0, 40.0],
        [6.0, 0.0, 40.0],
        [-11.5, -4.5, 40.0],
        [11.5, -4.5, 40.0],
    ]
)


def matriz_distancias(P: np.ndarray) -> np.ndarray:
    """Distancias reales entre pares: lo que mide la radio."""
    dif = P[:, None, :] - P[None, :, :]
    return np.linalg.norm(dif, axis=2)


def test_kabsch_recupera_transformacion_conocida():
    """Con una rotacion y traslacion conocidas, el calce debe ser exacto."""
    ang = 0.7
    R_real = np.array(
        [
            [np.cos(ang), -np.sin(ang), 0.0],
            [np.sin(ang), np.cos(ang), 0.0],
            [0.0, 0.0, 1.0],
        ]
    )
    t_real = np.array([120.0, -45.0, 8.0])
    P = FORMACION_V @ R_real.T + t_real

    R, t = kabsch(FORMACION_V, P)
    assert np.allclose(R, R_real, atol=1e-9)
    assert np.allclose(t, t_real, atol=1e-9)
    # Nunca una reflexion
    assert np.isclose(np.linalg.det(R), 1.0, atol=1e-9)


def test_formacion_coplanar_tiene_rango_dos():
    """
    La V vuela toda a la misma altura, asi que su rango efectivo es 2.
    Detectarlo importa: con rango 1 (drones en linea) el residuo no
    significaria nada y hay que decirlo en vez de informar un numero.
    """
    D = matriz_distancias(FORMACION_V)
    _, valores = mds_clasico(D)
    assert rango_efectivo(valores) == 2


def test_enjambre_limpio_residuos_nulos():
    """Sin ataque y sin ruido, los cinco residuos son cero."""
    D = matriz_distancias(FORMACION_V)
    completo, _, _, _ = residuos_rigidez(FORMACION_V, D)
    assert np.all(np.linalg.norm(completo, axis=1) < 1e-6)


def test_spoofing_individual_se_detecta():
    """
    Un solo dron enganado: la forma informada deja de cerrar con la medida.
    Su residuo tiene que dispararse muy por encima del umbral robusto, y el
    de los otros cuatro tiene que quedarse abajo.
    """
    D = matriz_distancias(FORMACION_V)  # la radio mide la realidad
    P_informada = FORMACION_V.copy()
    P_informada[4] += np.array([40.0, 0.0, 0.0])  # a D-05 le mienten 40 m

    # Con el calce COMPLETO el mentiroso arrastra el ajuste y le unta residuo a
    # los inocentes. Igual queda a la vista quien es: es el mas desviado, y eso
    # alcanza para decidir a quien excluir.
    completo, _, _, _ = residuos_rigidez(P_informada, D)
    r_completo = np.linalg.norm(completo, axis=1)
    assert int(np.argmax(r_completo)) == 4, r_completo

    # Excluyendolo del calce, la separacion se vuelve inequivoca: cuatro
    # residuos nulos y uno igual al desplazamiento inyectado.
    _, excluido, _, _ = residuos_rigidez(P_informada, D, excluir=4)
    r = np.linalg.norm(excluido, axis=1)
    umbral, _, _ = umbral_robusto(r)
    assert r[4] > umbral
    otros = np.delete(r, 4)
    assert np.all(otros < 1e-6), r
    assert r[4] > 39.0


def test_traslacion_coherente_es_INVISIBLE_para_capa1():
    """
    EL CASO B. Esta prueba es el argumento central del proyecto.

    El atacante corre a los CINCO drones con el mismo vector. Las distancias
    entre pares no cambian ni un milimetro, asi que la forma informada es
    geometricamente identica a la real. Ninguna cantidad de verificacion
    entre pares puede ver esto, porque desde adentro del enjambre no hay
    diferencia con un vuelo legitimo.

    Lo que se afirma aca: residuos ~0 Y traslacion grande. Esa combinacion es
    la firma del ataque, y la unica forma de distinguirla de un enjambre que
    de verdad se movio es un ancla que no dependa del enjambre.
    """
    D = matriz_distancias(FORMACION_V)
    desplazamiento = np.array([200.0, -150.0, 0.0])
    P_informada = FORMACION_V + desplazamiento

    completo, _, t, _ = residuos_rigidez(P_informada, D)
    residuos = np.linalg.norm(completo, axis=1)

    # Los cinco residuos son practicamente nulos: la capa 1 no ve NADA.
    assert np.all(residuos < 1e-6), f"la capa 1 deberia estar ciega: {residuos}"
    # Pero el vector de traslacion delata que la nube se fue lejos.
    assert np.linalg.norm(t) > 200.0


def test_par_mentiroso_ensucia_la_reconstruccion():
    """
    Un nodo que falsea las distancias que publica corrompe la nube que todos
    reconstruyen. La capa 1 lo nota como residuo elevado, pero no alcanza
    para decidir quien miente: para eso esta la capa 3.
    """
    D = matriz_distancias(FORMACION_V)
    D_falsa = D.copy()
    # D-04 infla todas sus distancias en 6 m (fila y columna, para que la
    # matriz siga siendo simetrica y no se delate por lo obvio).
    D_falsa[3, :] += 6.0
    D_falsa[:, 3] += 6.0
    D_falsa[3, 3] = 0.0

    completo, _, _, _ = residuos_rigidez(FORMACION_V, D_falsa)
    assert np.linalg.norm(completo, axis=1).max() > 1.0


def test_umbral_robusto_resiste_un_mentiroso():
    """
    Con un solo valor atipico enorme, la mediana y la MAD casi no se mueven.
    Con promedio y desvio estandar, el mentiroso se esconderia dentro de su
    propio umbral: esta prueba fija esa diferencia.
    """
    residuos = np.array([0.10, 0.12, 0.09, 0.11, 25.0])
    umbral, mediana, _ = umbral_robusto(residuos)

    assert residuos[4] > umbral
    assert mediana < 0.2  # la mediana ni se entero del atipico

    # El mismo caso con estadisticos no robustos: el umbral se infla tanto
    # que el mentiroso queda por debajo y pasa desapercibido.
    umbral_ingenuo = residuos.mean() + 3.5 * residuos.std()
    assert residuos[4] < umbral_ingenuo
