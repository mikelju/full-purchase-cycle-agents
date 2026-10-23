# LinkedIn series plan

Short series of LinkedIn posts that the owner publishes himself to present the project.
The framing is in English; the post texts are in Spanish because they are published as written.
Nothing here is published by an agent.

## Goal and audience

Show companies hiring a Senior AI Engineer or AI Systems Engineer that the owner can build an AI system that runs a real business process robustly, with traceability and checked results.
The posts do not teach what an agent is and use the word sparingly.

## Series

| # | Day | Topic | Animation |
|---|-----|-------|-----------|
| 1 | Monday | Customer orders; presents the whole project | `docs/assets/purchase-cycle-flow.mp4` |
| 2 | Wednesday | Supplier quotes, comparison and human approval | `docs/assets/quotes-flow.mp4` |
| 3 | Friday | Delivery notes and invoices: AI reads, code counts | `docs/assets/reconcile-flow.mp4` |
| 4 | Monday | How it is built: SDD Lite, tests, evaluations and what comes next (the orchestrator) | To be decided |

## Rules

- Spanish only.
- The GitHub link goes in the first comment, not in the post.
- Post 1 opens with an everyday scene; posts 2 to 4 pick up from the previous post instead of opening with a new scene.
- Owner's voice: short lines and standalone sentences, bullets with 👉, informal singular "tú", a colloquial turn here and there, a reflective or question close.
  Learned from post 3 as published: shorter and less technical, explaining how the system works rather than implementation detail.
- Post 1 gives the general scope (three parts plus a coordinator that organizes and supervises) before focusing on orders.
- Spanish copies of the three animations are proposed as a separate change; the owner will decide whether the posts use the Spanish or the English videos.
  The English ones stay in the README.
- Every post gets the same first comment with the GitHub link.

## Status

| # | Status |
|---|--------|
| 1 | Approved by the owner |
| 2 | Published by the owner on 2026-10-21; the text below is the published one |
| 3 | Published by the owner on 2026-10-23; the text below is the published one |
| 4 | Approved in general; the owner may still adjust the wording; image to be decided |

## Post 1 - orders

```text
Lunes, 9 de la mañana en una distribuidora de material sanitario.

Un email con un PDF adjunto.
Un WhatsApp de una residencia.
Un pedido que entra por la web.

Tres clientes, tres formatos, y alguien que tiene que pasarlo todo a mano antes de poder hacer nada más.

Llevo unas semanas construyendo un sistema con IA que se encarga de todo el ciclo de compra de una empresa así, en tres partes:

👉 Pedidos de clientes, lleguen por donde lleguen.
👉 Proveedores: pedir presupuestos cuando falta stock, compararlos y comprar.
👉 Facturas y albaranes: comprobar que todo cuadra y reclamar cuando no.

Y por encima, un coordinador que organiza y supervisa toda la operativa.

Hoy te enseño la primera, los pedidos. Cada caja del vídeo es como una persona del equipo con su tarea: un pequeño programa, unos con IA y otros sin ella.

👉 La IA lee el email, el PDF, el Excel o el WhatsApp y saca qué productos piden y cuántos.
👉 Si algo no está claro, pregunta al cliente y espera su respuesta sin perder el hilo.
👉 Si falta stock, pasa el testigo a la parte de proveedores.

La IA hace lo que hace bien: entender textos desordenados.
Las cuentas, las reglas y las decisiones importantes se quedan en código normal o en manos de una persona.

Y todo con pruebas, evaluaciones que miden cuánto acierta y trazas de cada paso. Que funcione bien no se da por hecho: se comprueba.

En el próximo post, los proveedores.

¿Qué parte de este proceso te quita más tiempo a ti?

#InteligenciaArtificial #Automatización #LangGraph
```

First comment:

```text
El código está abierto en GitHub, por si quieres curiosear cómo está hecho por dentro:
https://github.com/mikelju/full-purchase-cycle-agents
```

## Post 2 - suppliers

```text
Hoy la gestión de proveedores

Quizás viste mi post de antes de ayer sobre el proyecto de agentes que controlan globalmente una empresa de productos sanitarios. En él te enseñé cómo se gestionan los pedidos que van llegando, de forma automática.

Pero ¿qué pasa cuando alguien pide 12 cajas de guantes y en el almacén no quedan?
Ahí entra la segunda parte del sistema: el flujo de proveedores.

👉 Detecta qué productos faltan y escribe a todos los proveedores que pueden servirlo.
👉 Espera sus respuestas, días si hace falta: recordatorio al tercer día y fecha límite al quinto. Aunque el sistema se reinicie, no pierde la espera.
👉 La IA lee cada respuesta: si ofertan o no, cantidad, precio, IVA y plazo de entrega.

Y aquí viene lo importante: la IA no elige proveedor.
Eso lo hace una regla fija, escrita en código. Primero, las ofertas que cubren todo el pedido; entre ellas, la más barata; si empatan, la que llega antes.

En el vídeo, un proveedor dice que no, otro ofrece las 12 cajas y un tercero solo 8, algo más baratas. Gana el que sirve el pedido entero.

Y antes de comprar nada, el sistema se para y espera a que una persona apruebe, elija otro proveedor o lo rechace. Solo entonces sale el pedido de compra.

¿Por qué así? Porque una compra mal hecha cuesta dinero. Que la IA lea, sí. Que decida sola a quién se compra, no.

Medido: la lectura de ofertas acierta el 98,9% de los datos, sin ningún error grave, y la regla de decisión supera los 39 casos de prueba.

El viernes, la tercera parte: facturas y albaranes.

#InteligenciaArtificial #Automatización #Compras
```

## Post 3 - delivery notes and invoices

```text
Hoy, albaranes y facturas

El miércoles el sistema hacía un pedido de compra a un proveedor.
Días después llegan el albarán y la factura.

¿Cuadran con lo que pediste? Comprobarlo a mano es lento, y es justo ahí donde se escapan los errores.

La tercera parte del sistema se resume en una frase: la IA lee, el código cuenta.

👉 La IA lee cada albarán y cada factura, venga en PDF, en Excel o en texto
👉 El código lo compara línea a línea con el pedido: precio, cantidad, líneas de más y líneas de menos.
👉 Si algo no cuadra, la IA redacta el email de reclamación y una persona lo aprueba antes de que salga.
👉 Cuando todo cuadra, entra el stock y se sirven, por orden de llegada, los pedidos de clientes que estaban esperando.

En el vídeo, dos rondas: en la primera el pedido, el albarán y la factura cuadran y la factura se registra; en la segunda llegaron 35 unidades y se facturaron 40, así que se calcula la diferencia (-25,00 €) y se pide un abono al proveedor.

¿Por qué no dejar que la IA compare también? Porque sumar y comparar números es lo que un programa normal hace sin fallar nunca.

Medido y a la vista: la comparación supera los 58 casos de prueba.

La lectura de documentos tenía 2 errores graves. Los analicé, ajusté las instrucciones de la IA y ahora acierta los 90 documentos de prueba sin ningún error grave.

El lunes, lo que no se ve en los vídeos: cómo lo he construido.

#InteligenciaArtificial #Automatización #Finanzas
```

First comment as published:

```text
El proyecto completo:
https://github.com/mikelju/full-purchase-cycle-agents

Si te perdiste los posts anteriores de la serie:
- Pedidos: https://lnkd.in/p/e3v3Kzqt
- Proveedores: https://lnkd.in/p/esQAcdWa
```

## Post 4 - how it is built

```text
Esta semana te he enseñado las tres partes del sistema: pedidos, proveedores y facturas.
Hoy, lo que no se ve en los vídeos: cómo lo he construido.

No empecé escribiendo código. Empecé por un plan.

👉 Un plan general dividido en fases pequeñas, y cada una deja algo que funciona.
👉 Antes de cada fase, unos criterios de aceptación que se congelan. Si algo cambia, queda escrito por qué.
👉 Agentes de IA programan cada fase, y otros revisan el trabajo buscando fallos a propósito antes de que yo apruebe nada.

Lo que más me importa es poder demostrar que funciona:

👉 1.288 pruebas automáticas.
👉 17 evaluaciones que miden cuánto acierta la IA en cada tarea, con un mínimo exigido y una prueba estadística que avisa si algo empeora.
👉 Trazas de cada paso, para saber por qué el sistema hizo lo que hizo.
👉 Los fallos conocidos, publicados y no escondidos.

Y sin disparar el coste: toda la fase de reclamaciones costó menos de 20 céntimos en llamadas a la IA.

¿Qué viene ahora? La facturación a clientes y, después, el coordinador: la pieza que une las tres partes y supervisa toda la operativa de principio a fin.

Construir con IA no es pedirle que lo haga todo.
Es decidir bien qué hace ella, qué hace el código y qué decide una persona.

El código está abierto: te dejo el enlace en los comentarios.

#InteligenciaArtificial #LangGraph #IngenieríaDeSoftware
```

The figures in posts 2 to 4 come from the README and the change records as of 2026-10-16: 1,288 tests, 17 evaluations, offer extraction 98.9%, quote decision 39/39, reconciliation matching 58/58, document extraction 97.8% with 2 critical errors, phase 09 live calls about 0.18 USD.
On 2026-10-23 post 3 was published with the README figures after change 018: document extraction correct on all 90 test documents with 0 critical errors, after the 2 critical errors were analyzed and the instructions adjusted.
The published text does not carry the caveat that this result is not a clean held-out measure, because the instructions were worded from the two diagnosed misses.
Check them again before publishing.

## Possible new animations

Proposals only, not built: claim and goods receipt (phases 09-10), and the full cycle once the orchestrator exists (phase 12).
