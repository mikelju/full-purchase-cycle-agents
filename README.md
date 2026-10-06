# Full Purchase Cycle Agents

Proyecto de portfolio que demuestra orquestación multiagente con [LangGraph](https://github.com/langchain-ai/langgraph) (Python) sobre el ciclo de compra completo de una empresa ficticia.
Estado: plan maestro en borrador; todavía no hay código.

## Qué hace

| Módulo | Flujo | Rasgos que demuestra |
|---|---|---|
| Pedidos de clientes | Correo (texto, PDF, Excel), formulario web y WhatsApp simulado; extracción, correspondencia con catálogo, excepciones y respuesta | Subgrafos por canal, pausa hasta la respuesta del cliente, base de datos |
| Presupuestos a proveedores | Falta de stock, petición de ofertas, espera de días con estado guardado, comparación y aprobación humana | Estado persistente, human-in-the-loop, reanudación |
| Conciliación de facturas | Factura contra pedido de compra y albarán, diferencias y borrador de reclamación aprobado por una persona | Validación estructurada, human-in-the-loop |
| Orquestador | Grafo superior que compone los tres módulos sobre la base de datos común | Composición de subgrafos, recuperación de fallos |

Cada módulo tendrá demo ejecutable, pruebas, evaluación con métricas sobre un conjunto de casos y trazas.
El plan y las fases están en [docs/plans/0_plan_maestro.md](docs/plans/0_plan_maestro.md).

## Cómo se trabaja

El proyecto se desarrolla con el método SDD Lite: plan maestro, fases con criterios congelados, ejecución autónoma por agentes y revisión adversarial.
Guía de uso en [docs/como-trabajar.md](docs/como-trabajar.md).
`npm run check` agrupa las comprobaciones; las garantías del marco viven en `.claude/settings.json` y `.claude/hooks/`.

## Requisitos

Windows, Python 3.13 con uv, Node.js 22 (herramientas del marco), Git y GitHub CLI.
Instalación de las herramientas del marco: `npm ci --ignore-scripts --no-audit --no-fund`.
La instalación del producto se documentará en la fase 01.

## Límites

- Datos, clientes, proveedores y canales son ficticios o simulados; no hay integración con correo, WhatsApp ni ERP reales.
- `npm run check` comprueba por ahora solo dependencias y hooks del marco, no comportamiento de producto.
