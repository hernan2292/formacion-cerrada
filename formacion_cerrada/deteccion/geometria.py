"""
Geometria de la prueba de rigidez.

Es el corazon del sistema y es geometria clasica: no hay aprendizaje
automatico, no hay umbrales entrenados, y para la misma entrada devuelve
siempre exactamente la misma salida.

Tres pasos:
  1. Escalado multidimensional clasico  -> reconstruye la FORMA del enjambre
     a partir de las distancias medidas entre pares, sin saber donde esta
     nadie. Sale con orientacion arbitraria.
  2. Alineamiento rigido (Kabsch)       -> rota y traslada esa forma para
     calzarla lo mejor posible contra las posiciones que informa el satelite.
  3. Residuo por dron                   -> cuanto le sobra a cada uno despues
     del mejor calce posible.

Todo con numpy: son tres descomposiciones de matrices de 5x5 y 3x3, del
orden de decenas de microsegundos. Corre holgado en una Raspberry Pi Zero.
"""

from __future__ import annotations

import numpy as np

# Un valor propio se considera una dimension real de la formacion si supera
# esta fraccion del mayor. Por debajo es ruido de medicion.
TOL_RANGO = 5e-3


def mds_clasico(D: np.ndarray, dim: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """
    Escalado multidimensional clasico (Torgerson).

    Recibe la matriz de distancias medidas entre pares y devuelve una nube de
    puntos que respeta esas distancias. La nube sale centrada en el origen y
    con orientacion arbitraria: recupera forma y tamano, no posicion ni rumbo.

    Devuelve (X, valores_propios). X es (n, dim).
    """
    n = D.shape[0]
    D2 = D**2
    # Matriz de centrado: J = I - (1/n) * 1 * 1^T
    J = np.eye(n) - np.ones((n, n)) / n
    # Matriz de productos internos a partir de distancias
    B = -0.5 * J @ D2 @ J
    # B es simetrica por construccion, asi que eigh es el metodo correcto
    # (y devuelve valores propios reales y ordenados de forma ascendente).
    valores, vectores = np.linalg.eigh(B)
    orden = np.argsort(valores)[::-1]
    valores = valores[orden]
    vectores = vectores[:, orden]
    # Con distancias ruidosas aparecen valores propios levemente negativos.
    # No son dimensiones reales: se recortan a cero.
    positivos = np.clip(valores[:dim], 0.0, None)
    X = vectores[:, :dim] * np.sqrt(positivos)
    return X, valores


def rango_efectivo(valores: np.ndarray, tol: float = TOL_RANGO) -> int:
    """
    Cuantas dimensiones tiene realmente la formacion.

    Importa de verdad: una formacion que vuela toda a la misma altura es
    COPLANAR y su rango efectivo es 2, no 3. No es un caso de borde teorico,
    es como vuelan las formaciones en la practica. Si el rango cae a 1 (todos
    los drones alineados) la reconstruccion deja de estar determinada y el
    residuo no significa nada.
    """
    v = np.clip(valores, 0.0, None)
    if v.size == 0 or v[0] <= 0:
        return 0
    return int(np.sum(v / v[0] > tol))


def kabsch(X: np.ndarray, P: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Mejor alineamiento rigido entre dos nubes de puntos ya emparejadas.

    Busca la rotacion R y la traslacion t que minimizan  ||X R^T + t - P||^2,
    sujeto a que R sea una rotacion propia (R^T R = I, det R = +1).

    La correccion por determinante es la parte que no se puede omitir: sin
    ella la descomposicion puede devolver una REFLEXION, que calza igual de
    bien en el sentido algebraico pero describe una formacion espejada que
    fisicamente no existe.
    """
    centro_X = X.mean(axis=0)
    centro_P = P.mean(axis=0)
    Xc = X - centro_X
    Pc = P - centro_P

    H = Xc.T @ Pc
    U, _, Vt = np.linalg.svd(H)

    # Fuerza det(R) = +1: rotacion propia, nunca reflexion.
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    correccion = np.eye(X.shape[1])
    correccion[-1, -1] = d

    R = Vt.T @ correccion @ U.T
    t = centro_P - R @ centro_X
    return R, t


def aplicar(X: np.ndarray, R: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Aplica la transformacion rigida a una nube en formato fila."""
    return X @ R.T + t


def residuos_rigidez(
    P_sat: np.ndarray, D_rad: np.ndarray, excluir: int | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]:
    """
    La prueba de rigidez completa.

    P_sat   : (n, 3) posicion que cada dron informa desde el satelite.
    D_rad   : (n, n) distancia medida entre cada par por radio.
    excluir : nodo que NO participa del calce, por venir marcado como
              sospechoso. Los residuos se devuelven igual para los n nodos.

    Devuelve (vectores_completo, vectores_excluido, traslacion, rango).

    SE DEVUELVEN LOS DOS AJUSTES, y esa duplicacion es la que mantiene al
    sistema estable. Cada uno sirve para una cosa distinta:

      COMPLETO  calza usando a los cinco nodos. Es imparcial, y por eso es el
                unico sobre el que se puede DECIDIR a quien excluir.
      EXCLUIDO  calza sin el sospechoso. Da la separacion limpia que se
                informa y se dibuja, pero no sirve para decidir nada.

    Decidir sobre el ajuste con exclusion es un lazo que se auto-confirma: al
    nodo excluido se le mide el residuo contra un calce del que no participo,
    su residuo crece por esa sola razon, y eso lo mantiene excluido para
    siempre. Medido, con esa realimentacion el vuelo limpio expulsaba a un
    inocente en uno de cada cinco ciclos y no se recuperaba nunca.

    POR QUE SE EXCLUYE A UN NODO DEL CALCE. El alineamiento de Kabsch es por
    minimos cuadrados sobre todos los nodos que participen, asi que un solo
    dron enganado arrastra el ajuste entero y le unta residuo a los inocentes:
    medido, un desplazamiento de 40 m en un nodo deja a los otros cuatro con 7
    a 9 m de residuo propio, la separacion cae a 3,5x, y en pantalla se ven
    cinco barras altas en vez de una.

    POR QUE LA EXCLUSION VIENE DE AFUERA Y NO SE BUSCA ACA. Es la leccion que
    costo mas caro: sobre los datos de UN ciclo, la decision de a quien excluir
    es demasiado ruidosa para tomarla. El error satelital independiente de cada
    nodo ronda los 4 m contra una formacion de 23 m de envergadura, y con esa
    relacion senal a ruido el inocente mas desafortunado supera al atacante en
    uno de cada cuatro ciclos. Como cada exclusion define un ajuste distinto,
    elegir mal hace saltar los residuos entre dos bases incompatibles, y con
    eso el promedio temporal deja de significar nada.

    La decision correcta se toma sobre los residuos YA PROMEDIADOS en el
    tiempo, donde el piso de ruido baja de 4 m a medio metro, y se realimenta
    aca en el ciclo siguiente. Un atacante sostiene su desplazamiento; el ruido
    no. Ver formacion_cerrada/nodo.py.

    LO QUE ESTA PRUEBA NO PUEDE VER, y es deliberado: si un atacante desplaza
    a TODO el enjambre con el mismo vector, las distancias entre pares se
    conservan intactas, la forma medida y la informada siguen siendo
    identicas, y todos los residuos salen ~0. El vector de traslacion sale
    grande y los residuos chicos -- que es la firma exacta de ese ataque,
    pero tambien la de un enjambre que de verdad se movio. Distinguir una
    cosa de la otra exige un ancla absoluta, y por eso existe la capa 2.
    """
    X, valores = mds_clasico(D_rad, dim=P_sat.shape[1])
    rango = rango_efectivo(valores)

    n = P_sat.shape[0]
    participan = np.ones(n, dtype=bool)
    # Tres puntos no colineales alcanzan para determinar un calce rigido: solo
    # se excluye si quedan suficientes.
    if excluir is not None and 0 <= excluir < n and n - 1 >= P_sat.shape[1]:
        participan[excluir] = False

    R_c, t_c = kabsch(X, P_sat)
    vectores_completo = aplicar(X, R_c, t_c) - P_sat

    if participan.all():
        return vectores_completo, vectores_completo, t_c, rango

    R_e, t_e = kabsch(X[participan], P_sat[participan])
    vectores_excluido = aplicar(X, R_e, t_e) - P_sat
    return vectores_completo, vectores_excluido, t_e, rango


# Residuo por debajo del cual nada se considera sospechoso, pase lo que pase
# con el estadistico relativo. Sale de medir 135 s de vuelo limpio: el residuo
# se queda en 0,56 m de media con un percentil 99,9 de 1,65 m, asi que 2,0 m
# deja margen de sobra sin acercarse a ningun ataque de interes.
#
# Hace falta porque la prueba relativa se vuelve exigente justamente cuando
# todo anda bien: con cinco residuos parecidos, la MAD es minuscula y el umbral
# se pega a la mediana, de modo que cualquier nodo que fluctue un poco por
# encima de sus companeros queda marcado. Un residuo de 90 cm cuando el piso de
# ruido es de 56 cm no es un ataque, es ruido.
PISO_UMBRAL_M = 2.0


def umbral_robusto(
    residuos: np.ndarray, k: float = 3.5, piso_m: float = PISO_UMBRAL_M
) -> tuple[float, float, float]:
    """
    Umbral por mediana y desviacion absoluta mediana, con piso absoluto.

    Se usa mediana y MAD en vez de promedio y desvio estandar por una razon
    concreta: un solo mentiroso arrastra el promedio y infla el desvio lo
    suficiente como para esconderse dentro de su propio umbral. La mediana no
    se mueve mientras menos de la mitad de los nodos mientan.

    Devuelve (umbral, mediana, mad).
    """
    # La dispersion se estima RECORTANDO al residuo mas alto.
    #
    # Es necesario por el mismo arranque circular que obliga al calce por
    # mediana minima de cuadrados, una vuelta mas arriba: con cinco nodos y un
    # atacante, el residuo del atacante entra en el calculo de la dispersion,
    # la infla, y el umbral termina por encima del propio valor que tiene que
    # marcar. Medido sobre el arrastre gradual: el umbral trepaba a 7,8 m
    # mientras el nodo atacado estaba en 6,3 m, y la deteccion se apagaba sola
    # justo cuando el ataque se hacia mas grande.
    #
    # Recortar uno de cinco es legitimo mientras se tolere un solo atacante,
    # que es exactamente la hipotesis que sostiene todo el diseno.
    ordenados = np.sort(np.asarray(residuos, dtype=float))
    recortados = ordenados[:-1] if ordenados.size >= 4 else ordenados

    mediana = float(np.median(recortados))
    mad = float(np.median(np.abs(recortados - mediana)))
    # 1.4826 lleva la MAD a una escala comparable con el desvio estandar
    # cuando los datos son gaussianos.
    escala = max(1.4826 * mad, 0.05)
    return max(mediana + k * escala, piso_m), mediana, mad
