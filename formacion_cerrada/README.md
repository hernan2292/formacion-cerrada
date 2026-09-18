# Formación Cerrada — núcleo de detección

Detección de falsificación de señal satelital en un enjambre de cinco drones,
por validación geométrica cruzada entre pares.

Este paquete es **el código que correría a bordo**. No hay cifras escritas a
mano: cada residuo, cada umbral y cada cambio de postura sale de correr el
algoritmo sobre datos simulados con ruido. El tablero web no calcula nada.

```
Eje 1 · Hackathon Nacional de Ciberdefensa CYBER.AR 2026
Trayectorias, sensores y ataques enteramente simulados.
Ningún componente se ejecuta sobre aeronaves ni sistemas reales.
```

## Arrancar

Hacen falta dos procesos. El núcleo:

```bash
pip install numpy websockets
python -m formacion_cerrada.puente.servidor      # ws://localhost:8765
```

Y el tablero, desde la raíz del repositorio:

```bash
npm install
npm run dev                                       # http://localhost:3000
```

Si el núcleo no está, el tablero lo dice y reintenta solo. Nunca inventa cifras
para rellenar.

```bash
python -m pytest formacion_cerrada/pruebas/ -q    # 17 pruebas
```

## Qué hace, en una imagen

Cada dron informa dónde cree estar, según el satélite. Además, cada par de
drones mide con su radio a qué distancia está del otro, y **esa medición no se
puede falsificar desde el suelo**, porque es una conversación directa entre dos
aeronaves.

Con las distancias medidas se reconstruye la *forma* del enjambre, se la calza
contra la *forma* que describen las posiciones informadas, y se mira cuánto le
sobra a cada uno. Al que le miente el satélite, no le cierra.

## Las tres capas

**Capa 1 — rigidez de la formación** (`deteccion/capa1_rigidez.py`)
Escalado multidimensional clásico sobre las distancias medidas, alineamiento
rígido de Kabsch contra las posiciones informadas, y residuo por dron. Decisión
por mediana y desviación absoluta mediana, no por promedio y desvío: un solo
mentiroso arrastra el promedio lo suficiente como para esconderse dentro de su
propio umbral.

**Es ciega a la traslación coherente, por construcción.** Si el atacante corre a
los cinco drones con el mismo vector, las distancias entre pares no cambian, la
forma informada es idéntica a la real, y ninguna cantidad de verificación entre
pares puede verlo. Eso no es un defecto de la implementación: es una propiedad
matemática, y `pruebas/test_geometria.py` la demuestra.

**Capa 2 — anclas absolutas** (`deteccion/capa2_anclas.py`)
Las únicas verificaciones que no dependen del enjambre, y por eso las únicas que
sobreviven al caso anterior. La inercial cubre las tres dimensiones; el
barómetro sólo la vertical.

**Capa 3 — consenso robusto** (`deteccion/capa3_consenso.py`)
Cada distancia se mide dos veces, una por cada punta del par. Un nodo que falsea
lo que publica contradice a sus cuatro compañeros a la vez, y el otro extremo de
cada par es testigo.

La regla de combinación está en `deteccion/fusion.py` y es la única que cubre el
caso B: *la capa 1 sola no puede bajar la confianza global; si todos los residuos
son chicos pero la capa 2 discrepa en todo el enjambre, la formación entera
queda sospechada.*

## El lazo está cerrado: el ataque tiene consecuencia

Cada dron navega hacia donde **cree** estar. Si el satélite le miente veinte
metros, corrige veinte metros y se mueve de verdad. El atacante no empuja a la
aeronave: la convence de que se empuje sola.

Por eso la métrica que importa no es el residuo sino el **desvío de ruta**:
cuánto se apartó físicamente el enjambre de donde tenía que estar. El residuo
dice si el sistema se dio cuenta; el desvío dice si el atacante consiguió lo
que quería.

Mismo ataque de traslación coherente, con y sin la defensa a bordo:

| | 10 s | 30 s | 60 s |
|---|---|---|---|
| **Sin defensa** | 11 m | 46 m | **93 m** |
| **Con defensa** | 7 m | 13 m | **16 m** |

Sin defensa el desvío crece al ritmo del atacante y no tiene techo. Con
defensa, al detectarse el caso B se deja de navegar por satélite y se pasa a
rumbo inercial: el enjambre igual se aparta —la inercial deriva y eso no se
puede evitar— pero deja de crecer al ritmo del atacante y pasa a crecer al
ritmo, mucho menor, de la deriva del sensor.

Con la defensa apagada **la detección sigue corriendo e informando**. Lo que se
desconecta es la consecuencia. En los dos casos el sistema se da cuenta; lo que
cambia es si hace algo al respecto.

## Comportamiento medido

| Escenario | Capa 1 | Capa 2 | Capa 3 | Resultado |
|---|---|---|---|---|
| Vuelo limpio, viento 3,7 m/s | — | — | — | NOMINAL, desvío 3,6 m |
| Salto de 40 m en D-05 | ~9 s | — | expulsa D-05 | COLECTIVO, quórum 4/5 |
| Arrastre de D-05 a 0,5 m/s | ~7 s | — | expulsa D-05 | COLECTIVO, quórum 4/5 |
| **Traslación coherente** | **nunca** | ~6 s | — | **REPLIEGUE** |
| Coherente + D-04 mintiendo | nunca | ~6 s | ~2 s | REPLIEGUE, quórum 4/5 |

El piso del residuo es 0,56 m. **No lo fija la radio entre pares, que mide con
diez centímetros, sino el ruido del receptor satelital**: la prueba compara la
forma informada contra la medida, y la informada es la ruidosa.

## Tres problemas abiertos

Están medidos y documentados en las pruebas. Ninguno invalida la demostración,
los tres hay que resolverlos antes de hablar de uso real.

**La formación pierde cohesión en repliegue.** Cinco estimaciones inerciales
independientes no sostienen la geometría: pasados unos veinte segundos la
formación se abre lo suficiente como para que la prueba de rigidez marque
residuos donde no hay ningún atacante individual. La consecuencia hay que
decirla sin vueltas: *la capa 1 nunca ve la traslación coherente* vale para el
ataque, y deja de valer para lo que pasa después como efecto de la propia
respuesta defensiva. Es el problema a resolver primero.

**La detección del salto instantáneo es lenta.** Nueve segundos para un
desplazamiento de cuarenta metros. Antes era 0,1 s, con una versión que elegía
el nodo a excluir buscando sobre los datos de cada ciclo: rápida y también
inestable, porque excluía al inocente en uno de cada cuatro ciclos. Se cambió
por una decisión sobre residuos promediados, que es estable pero paga el
promedio en latencia. El intercambio está mal calibrado.

**El quórum parpadea durante los ataques.** Hay persistencia temporal en la
expulsión, pero no alcanza en todos los escenarios.

## Estados de navegación

Detectar es la mitad. `navegacion/maquina_estados.py` define la otra mitad.

```
NOMINAL → SOSPECHA → AISLADO → COLECTIVO
                        ↓
                    REPLIEGUE
```

En **COLECTIVO** los compañeros confiables pasan a ser los satélites de este
dron y la posición se recupera por trilateración: la misión sigue en vez de
abortarse.

De **REPLIEGUE** sólo se sale por recuperación completa, nunca pasando a
COLECTIVO. Es la transición que más importa: se llegó ahí porque las anclas se
cayeron en *todo* el enjambre, y preguntarles la posición a los pares en esa
situación reproduce el mismo error coherente en todos. No hay a quién
preguntarle.

## Lo que falta, dicho sin maquillaje

- **La malla no está autenticada.** La capa 3 tolera que un *integrante* mienta,
  no que un tercero inyecte mensajes con formato válido desde afuera. Se corrige
  firmando cada paquete con clave precompartida más un contador monótono.
- **No hay ancla de reloj.** El receptor resuelve cuatro incógnitas, no tres, y
  un ataque de reenvío corre el reloj sí o sí. Es el ancla que cerraría el hueco
  del plano horizontal, donde hoy sólo muerde la inercial.
- **Un arrastre muy lento se absorbe.** El filtro inercial estima y cancela la
  deriva del propio sensor, y un arrastre por debajo de esa velocidad es
  indistinguible de esa deriva. Es un límite físico de la capa 2 con una sola
  ancla, no un parámetro mal puesto.
- **La referencia temporal viene del satélite.** Comparar las posiciones de cinco
  drones exige que correspondan al mismo instante. Acá el instante común es el
  número de ciclo; a bordo habría que disciplinar un reloj de enjambre con los
  propios intercambios de la radio.
- **Escala con el cuadrado de los nodos.** Diez pares con cinco, ciento noventa
  con veinte. El límite es el aire, no el procesador.

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
  simulador/
    cinematica.py       formación en V patrullando
    sensores.py         modelo de ruido
    ataques.py          las cuatro clases
  malla/bus.py          latencia y pérdida configurables
  nodo.py               lo que corre a bordo de UN dron
  puente/
    simulacion.py       el lazo completo
    servidor.py         adaptador de E/S hacia el tablero
  pruebas/
```

`nodo.py` es el archivo que se desplegaría en la computadora compañera. Ningún
dron manda: los cinco reciben los mismos datos, corren el mismo código y votan
—incluido el engañado, que va a votar que está todo bien—. Por eso la decisión
se toma por mediana, y por eso hacen falta cinco nodos y no tres.
