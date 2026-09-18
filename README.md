# Formación Cerrada

Detección de falsificación de señal satelital en enjambres de drones, por
validación geométrica cruzada entre pares.

> **Eje 1 · Hackathon Nacional de Ciberdefensa CYBER.AR 2026**
> Trayectorias, sensores y ataques enteramente simulados.
> Ningún componente de este repositorio se ejecutó sobre aeronaves reales.

---

## El problema

Un dron sabe dónde está porque escucha satélites. Esas señales viajan veinte mil
kilómetros y llegan tan débiles que cualquiera con un transmisor barato puede
taparlas con una falsa más fuerte. El receptor no tiene forma de distinguirlas.

Y ahí está lo que hace al ataque tan efectivo: **el atacante no empuja al dron,
lo convence de que se empuje solo.** Le dice «estás veinte metros al este», el
dron corrige veinte metros al oeste, y se mueve de verdad.

Desde adentro de una aeronave, una señal falsa se ve idéntica a una real: el
atacante la construye coherente y no hay ningún número que no dé. Un dron solo
no puede resolver esto.

Cinco, sí.

## La idea

Cada dron informa dónde cree estar. Además, cada par de drones mide con su radio
a qué distancia está del otro — y **esa medición no se puede falsificar desde el
suelo**, porque es una conversación directa entre dos aeronaves.

Con las distancias se reconstruye la *forma* del enjambre. Se la calza contra la
*forma* que describen las posiciones informadas. Al que le mienten, no le cierra.

No hace falta saber dónde está nadie realmente: alcanza con que la historia sea
internamente consistente.

## Qué aporta sobre lo publicado

La validación cruzada entre pares está publicada. Lo que no está cubierto es
esto:

**Si el atacante desplaza a los cinco drones con el mismo vector, las distancias
entre pares no cambian.** La forma informada es geométricamente idéntica a la
real, sólo que corrida. Ninguna cantidad de verificación entre pares puede verlo
— no por una limitación de implementación, sino por construcción.

Ese caso se ataca aparte, con anclas que no dependen del enjambre. Y hay una
prueba automatizada que demuestra la ceguera:
[`test_caso_B_la_capa_1_NUNCA_dispara`](formacion_cerrada/pruebas/test_capas.py).

---

## Arrancar

```bash
pip install -r requirements.txt
./arrancar.sh                       # Linux / macOS
.\arrancar.ps1                      # Windows
```

Levanta el núcleo en `ws://localhost:8765`. El tablero web vive en otro
repositorio y se conecta ahí; sin tablero, el núcleo igual corre y se le puede
hablar con cualquier cliente WebSocket.

```bash
python -m pytest -q                 # 34 pruebas
```

## Las tres capas

**Capa 1 — rigidez de la formación.** Escalado multidimensional clásico sobre
las distancias medidas, alineamiento rígido de Kabsch contra las posiciones
informadas, residuo por dron, y decisión por mediana y desviación absoluta
mediana. Es geometría clásica: determinista, explicable, sin aprendizaje
automático. **Es ciega a la traslación coherente, por construcción.**

**Capa 2 — anclas absolutas.** Las únicas verificaciones que no dependen del
enjambre, y por eso las únicas que sobreviven al caso anterior. La inercial cubre
las tres dimensiones; el barómetro sólo la vertical.

**Capa 3 — consenso robusto.** Cada distancia se mide dos veces, una por cada
punta del par. Un nodo que falsea lo que publica contradice a sus cuatro
compañeros a la vez, y el otro extremo de cada par es testigo.

La regla que las combina está en [`fusion.py`](formacion_cerrada/deteccion/fusion.py)
y es la única que cubre el caso B: *la capa 1 sola no puede bajar la confianza
global; si todos los residuos son chicos pero la capa 2 discrepa en todo el
enjambre, la formación entera queda sospechada.*

## El ataque tiene consecuencia

Cada dron navega hacia donde **cree** estar, así que el lazo está cerrado: un
ataque de falsificación mueve la aeronave de verdad. La métrica que importa no es
el residuo sino el **desvío de ruta**: cuánto se apartó físicamente el enjambre
de donde tenía que estar.

Mismo ataque de traslación coherente, con y sin la defensa a bordo:

| | 10 s | 30 s | 60 s |
|---|---|---|---|
| **Sin defensa** | 11 m | 46 m | **93 m** |
| **Con defensa** | 7 m | 13 m | **16 m** |

Sin defensa el desvío crece al ritmo del atacante y no tiene techo. Con defensa,
al detectarse el caso B se pasa a rumbo inercial: el enjambre igual se aparta
—la inercial deriva y eso no se puede evitar— pero deja de crecer al ritmo del
atacante.

Con la defensa apagada **la detección sigue corriendo e informando**. Lo que se
desconecta es la consecuencia.

## Comportamiento medido

| Escenario | Capa 1 | Capa 2 | Capa 3 | Resultado |
|---|---|---|---|---|
| Vuelo limpio, viento 3,7 m/s | — | — | — | NOMINAL, desvío 3,6 m |
| Salto de 40 m en un nodo | ~9 s | — | lo expulsa | COLECTIVO, quórum 4/5 |
| Arrastre a 0,5 m/s | ~7 s | — | lo expulsa | COLECTIVO, quórum 4/5 |
| **Traslación coherente** | **nunca** | ~6 s | — | **REPLIEGUE** |
| Coherente + un nodo mintiendo | nunca | ~6 s | ~2 s | REPLIEGUE, quórum 4/5 |

El piso del residuo es 0,56 m. **No lo fija la radio entre pares, que mide con
diez centímetros, sino el ruido del receptor satelital**: la prueba compara la
forma informada contra la medida, y la informada es la ruidosa.

## Estados de navegación

```
NOMINAL → SOSPECHA → AISLADO → COLECTIVO
                        ↓
                    REPLIEGUE
```

En **COLECTIVO** los compañeros confiables pasan a ser los satélites de este
dron y la posición se recupera por trilateración: la misión sigue en vez de
abortarse.

De **REPLIEGUE** sólo se sale por recuperación completa, nunca pasando a
COLECTIVO. Se llegó ahí porque las anclas se cayeron en *todo* el enjambre, y
preguntarles la posición a los pares en esa situación reproduce el mismo error
coherente en todos. No hay a quién preguntarle.

---

## Llevarlo a una aeronave

**[MONTAJE.md](MONTAJE.md)** tiene el procedimiento completo: lista de
materiales, conexionado, instalación, configuración, y el camino de validación
por etapas.

Lo esencial antes de abrir ese documento:

> El núcleo de detección está probado. **El adaptador de hardware no.** Las
> clases que hablan MAVLink y con la radio de distancias están escritas contra
> la documentación de esos protocolos, no contra un banco.
>
> Por eso el modo por defecto es **OBSERVACIÓN**: el sistema mide, detecta y
> registra, y no le escribe nada a la controladora de vuelo. En ese modo, lo
> peor que puede pasar si el adaptador tiene un error es que no detecte algo.
> La aeronave vuela igual que sin esto instalado.

Retrofit por nodo: **~45 g, ~2 W, ~USD 60**, y cero líneas de cambio en el
firmware de vuelo.

## Estructura

```
formacion_cerrada/
  deteccion/
    geometria.py        MDS + Kabsch + residuos + umbral robusto
    capa1_rigidez.py    el voto que calcula y difunde cada dron
    capa2_anclas.py     inercial y barométrica
    capa3_consenso.py   asimetría de pares y votación por mediana
    fusion.py           índice de confianza y regla de combinación
  navegacion/
    maquina_estados.py  los cinco estados y sus transiciones
    trilateracion.py    recuperación de posición desde los pares
    estimador.py        la posición con la que se navega
  simulador/            cinemática con dinámica de vuelo, sensores, ataques
  malla/
    bus.py              transporte en memoria, para el banco
    autenticacion.py    firma HMAC y contador contra reenvío
  nodo.py               lo que corre a bordo de UN dron
  config.py             configuración de despliegue
  puente/
    simulacion.py       el lazo completo del banco
    servidor.py         adaptador hacia el tablero
    aeronave.py         adaptador hacia hardware real
  pruebas/
```

`nodo.py` es el archivo que se despliega en la computadora compañera. **Ningún
dron manda**: los cinco reciben los mismos datos, corren el mismo código y votan
—incluido el engañado, que va a votar que está todo bien—. Por eso la decisión se
toma por mediana, y por eso hacen falta cinco nodos y no tres.

## Limitaciones conocidas

Están medidas y documentadas en las pruebas. Ninguna invalida la demostración;
las tres primeras hay que resolverlas antes de hablar de uso operativo.

- **La formación pierde cohesión en repliegue.** Cinco estimaciones inerciales
  independientes no sostienen la geometría: pasados unos veinte segundos, la
  prueba de rigidez empieza a marcar residuos donde no hay ningún atacante. *La
  capa 1 nunca ve la traslación coherente* vale para el ataque, y deja de valer
  para lo que pasa después como efecto de la propia respuesta defensiva.
- **La detección del salto instantáneo es lenta**: nueve segundos para un
  desplazamiento de cuarenta metros. Es el precio de decidir sobre residuos
  promediados en vez de datos de un solo ciclo, y el intercambio está mal
  calibrado.
- **El quórum parpadea** durante algunos ataques.
- **Un arrastre muy lento se absorbe.** El filtro inercial estima y cancela la
  deriva de su propio sensor, y un arrastre por debajo de esa velocidad es
  indistinguible de esa deriva. Es un límite físico con una sola ancla inercial;
  la salida es una segunda ancla, el sesgo de reloj del receptor, identificada y
  no implementada.
- **La referencia temporal viene del satélite.** En el banco el instante común es
  el número de ciclo; a bordo habría que disciplinar un reloj de enjambre con los
  propios intercambios de la radio.
- **Escala con el cuadrado de los nodos.** Diez pares con cinco, ciento noventa
  con veinte. El límite es el aire, no el procesador.

## Licencia

GPL-3.0-or-later. Ver [LICENSE](LICENSE).
