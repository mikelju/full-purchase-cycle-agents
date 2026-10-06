# Full Purchase Cycle Agents

Portfolio público de orquestación multiagente con LangGraph (Python) sobre el ciclo de compra de una empresa: pedidos de clientes multicanal, presupuestos de proveedores y conciliación de facturas.
Cada módulo es un subgrafo con demo, pruebas, evaluación y trazas; una base de datos común los une y un grafo orquestador los compone.
Se trabaja con SDD Lite: plan maestro, fases con criterios congelados, ejecución autónoma y revisión adversarial.

## Siempre
- Actúa como interlocutor único; para desarrollar carga `.agents/skills/sdd-lite/SKILL.md`.
- Declara suposiciones y dudas antes de programar; pregunta cuando cambien el resultado.
- Haz lo mínimo que cumpla el criterio: sin funciones, abstracciones ni configuración no pedidas.
- Cambios quirúrgicos: toca solo lo que exige la petición y respeta el estilo existente.
- Trabaja contra criterios observables; reproduce fallos en el recorrido real del usuario antes de corregirlos.
- Cada resultado lleva evidencia vigente; corrige defectos claros relacionados y registra los ajenos.
- Lee solo el contexto necesario; la exploración amplia va a subagentes que devuelven resúmenes.
- Guion simple; en Markdown largo, una oración por línea; salidas Python sin símbolos especiales.
- Una fuente por decisión; los aprendizajes reutilizables van al archivo más específico.
- Este archivo tiene un tope de 5.000 tokens y no se edita a mitad de sesión; una regla nueva exige evidencia y, si no cabe, otra sale.

## Pregunta antes
- Decisiones pendientes del usuario, costes o dependencias nuevas y cambios a criterios congelados.
- Publicar fuera de la entrega: la autorización vale para ese destino y ese contenido; respeta confidencialidad y permisos heredados.
  La entrega incluye push de la rama de trabajo y PR a `main` en el remoto `origin` del proyecto.

## Nunca
- Rebajar criterios u ocultar pruebas fallidas o no ejecutadas.
- Fusionar, aprobar tu propio trabajo o escribir en la rama por defecto; los hooks de `.claude/` lo bloquean.
- Editar archivos generados o CHANGELOG.md a mano, ni añadir al agente como coautor de commits.

## Mapa
- Skills en `.agents/skills/`: flujo, enrutado y delegación en `sdd-lite`; revisión en `sdd-review`; entrega y No Mistakes en `sdd-delivery`.
- Plan maestro y fases: `docs/plans/`; cambios sueltos: `docs/changes/`; plantillas: `docs/templates/`.
- Garantías: `.claude/settings.json` y `.claude/hooks/`; comprobaciones: `npm run check`.
- Producto, instalación y límites: `README.md`. Entorno: Windows, Python 3.13 con uv, Node.js 22 (solo herramientas del marco), Git y GitHub CLI.
- Código Python en `src/`, pruebas en `tests/`, casos de evaluación en `evals/` (se crean en la fase 01).
