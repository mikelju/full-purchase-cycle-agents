# Plan maestro - Full Purchase Cycle Agents

Estado: aprobado (índice vivo: se revisa con el usuario según lo que descubra cada fase)
Aprobado por el usuario: 2026-10-06

## Visión
Portfolio público que demuestra orquestación multiagente con LangGraph (Python) sobre el ciclo de compra de una empresa ficticia.
Tres módulos independientes (pedidos de clientes multicanal, presupuestos a proveedores y conciliación de facturas) comparten una base de datos y los compone un grafo orquestador.
Debe hacer visibles, con código y evidencia, cinco capacidades: estado persistente, human-in-the-loop, recuperación de fallos, observabilidad y evaluación estructurada.
Se sabrá que funciona porque cada módulo tiene una demo reproducible con un comando, pruebas y una evaluación con métricas y umbrales que corre en `npm run check`.
La latencia no es crítica; la claridad del código y la reproducibilidad sí.

## Fases
Cada fase deja algo ejecutable con su demo, sus pruebas y sus casos de evaluación.
Las fases 01 a 05 completan el módulo de pedidos; después vienen presupuestos, conciliación y orquestador.

| Fase | Objetivo en una frase | Depende de | Estado |
|---|---|---|---|
| 01 | Cimientos: proyecto Python con uv, esquema y semilla de la base de datos común (catálogo, clientes, stock, pedidos), cliente LLM configurable, trazas y arnés de evaluación integrado en `npm run check`, con un grafo mínimo que lo demuestra | - | pendiente |
| 02 | Pedidos por formulario web: subgrafo de entrada estructurada, correspondencia con catálogo, alta del pedido en la base de datos y respuesta al cliente | 01 | pendiente |
| 03 | Pedidos por correo: agente de entrada de correo y agente extractor para texto, PDF y Excel, con evaluación de la extracción campo a campo | 02 | pendiente |
| 04 | Excepciones: producto ambiguo, inexistente o cantidad dudosa lleva a pregunta al cliente, pausa con estado guardado y reanudación al llegar la respuesta, aunque el proceso se haya reiniciado | 03 | pendiente |
| 05 | Cierre del módulo de pedidos: WhatsApp simulado, enrutador de canales, recuperación de fallos (reintentos, salida inválida del modelo, caída a mitad de flujo) y demo completa del módulo | 04 | pendiente |
| 06 | Presupuestos, petición y espera: detección de falta de stock, petición de ofertas a varios proveedores y espera de días con estado guardado, con reloj simulado y recordatorios | 05 | pendiente |
| 07 | Presupuestos, decisión: extracción y comparación de ofertas, aprobación humana y alta del pedido de compra | 06 | pendiente |
| 08 | Conciliación, detección: cruce de factura de proveedor con pedido de compra y albarán y clasificación de diferencias (precio, cantidad, línea sobrante o faltante) | 07 | pendiente |
| 09 | Conciliación, reclamación: borrador de reclamación al proveedor con aprobación, edición o rechazo humano | 08 | pendiente |
| 10 | Orquestador: grafo superior que compone los tres subgrafos sobre la base de datos común, con demo de extremo a extremo y panel de métricas de evaluación | 09 | pendiente |
| 11 | Escaparate del portfolio: README orientado a CV con diagramas de grafos, trazas de ejemplo, resultados de evaluación e integración continua en GitHub Actions | 10 | pendiente |

Estados: pendiente, spec en revisión, spec aprobada, en curso, bloqueada, lista localmente, integrada.
Solo el usuario añade, quita o reordena fases; el agente propone.

## Decisiones estratégicas
| Fecha | Decisión | Motivo | Alternativa descartada |
|---|---|---|---|
| 2026-10-06 | LangGraph (Python) como framework de orquestación | Requisito del proyecto | Orquestación artesanal |
| 2026-10-06 | Claude Haiku 4.5 (Anthropic) para extraer pedidos | Decisión del usuario: coste bajo y buena extracción | OpenAI, modelo local |
| 2026-10-06 | SQLite para datos de negocio y checkpoints de LangGraph | Decisión del usuario: cero instalación y reproducible | PostgreSQL en Docker |
| 2026-10-06 | LangSmith para trazas | Decisión del usuario: nativo de LangGraph y experiencia nueva para el CV | Langfuse, solo registro local |
| 2026-10-06 | La evaluación de `npm run check` corre sin coste ni red | Decisión del usuario; el mecanismo concreto se fija en la spec de la fase 01 | Llamar al modelo real en cada `check` |

## Pendiente
Fuera de alcance, criterios globales, otras decisiones y hitos se redactan con el usuario fase a fase.
