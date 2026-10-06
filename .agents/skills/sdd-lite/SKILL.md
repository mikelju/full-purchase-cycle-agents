---
name: sdd-lite
description: Coordinar un proyecto grande desde el plan maestro y sus fases hasta entregas comprobadas y revisadas. Usar para iniciar un proyecto, planear el plan maestro, una fase o un cambio, implementar, continuar o reanudar trabajo; no para consultas de solo lectura.
---
# SDD Lite

El usuario decide qué se construye y con qué criterios; el agente decide cómo y lo demuestra.
Las dos aprobaciones humanas son el plan maestro y la spec de cada fase. Entre ambas, el trabajo es autónomo.

## Enrutado: fase o cambio
- ¿Fija criterios congelados contra los que se medirá trabajo futuro? Fase, en `docs/plans/fase-NN-nombre/`.
- ¿No? Cambio, en `docs/changes/NNN-nombre.md`: arreglos, ajustes y mejoras acotadas.
- ¿Altera criterios de una fase aprobada? Desviación `NN.M-nombre.md` en la carpeta de esa fase, nunca un cambio suelto.
- Arreglo trivial: basta la petición y la evidencia de entrega.
Plantillas en `docs/templates/`. Todo cambio sustancial sube: desviación a spec y plan, alcance a plan maestro.

## Flujo
1. Iniciar: sustituye por los del producto la descripción y el Mapa de `AGENTS.md`, `README.md` y el nombre y el script `check` de `package.json`;
   borra `docs/changes/*` y `docs/refs/*` heredados de la plantilla. El README del producto enlaza a `docs/como-trabajar.md`, que no se edita.
   Después redacta con el usuario `docs/plans/0_plan_maestro.md`.
   Propón fases pequeñas que dejen algo funcionando; el usuario las aprueba.
2. Especificar una fase: `spec.md` con objetivo, alcance y criterios observables.
   Declara suposiciones y lleva las dudas a "Decisiones abiertas"; al aprobarla queda congelada.
3. Planear: `plan.md` con incrementos verificables ligados a criterios. No requiere nueva aprobación.
4. Ejecutar: crea la rama de trabajo (`fase-NN-nombre` o `cambio-NNN`) y relee los incrementos pendientes de `plan.md`.
   Una lista de tareas del harness, si existe, es copia efímera; la fuente es `plan.md`.
   Implementa uno a uno; reproduce bugs antes de corregirlos; al cerrar cada incremento marca el plan con su evidencia.
5. Validar: carga `../sdd-delivery/SKILL.md`, que incluye la revisión adversarial de `../sdd-review/SKILL.md`.
6. Cerrar: resultados por criterio, estado en el plan maestro y aprendizajes candidatos; entrega en rama y PR, nunca fusiones.
Si el usuario pidió solo plan, termina en el paso que corresponda.

## Cuándo parar y preguntar
Decisión del usuario, desviación que cambia criterios, coste o dependencia nueva, publicación no autorizada,
o dos intentos sin progreso: conserva trabajo y evidencia, explica el bloqueo y la decisión necesaria.

## Delegación
- Lectura y revisión en abanico: hasta cuatro subagentes en contexto limpio; cada uno devuelve un resumen, no volcados.
- Escritura: un solo ayudante, salvo worktrees con propiedad de archivos disjunta; integra y verifica tú.
- Brief de ayudante como a un recién llegado: objetivo, archivos permitidos, criterios y cómo se comprueba.
- Nada de delegar trabajo secuencial ni tareas de segundos; sin delegación recursiva ni equipos de agentes.

## Sesiones y contexto
- Una fase o un cambio por sesión; reanuda desde el plan y Git, no desde la memoria de la conversación.
- `AGENTS.md` no se edita a mitad de sesión porque invalida la caché del prefijo; las únicas excepciones son `/iniciar`
  y la pasada de aprendizaje, que van al final de su sesión.
- Exploración amplia a subagentes; en el contexto principal solo decisiones y evidencia.

## Aprendizaje con presupuesto
Al cerrar una fase, propone al usuario como máximo cinco ediciones a `AGENTS.md` o a las skills.
Cada una necesita evidencia de al menos dos sesiones con cita textual; si no cabe en el presupuesto, otra sale.
Si el disparador es estrecho y detectable, va a una skill; si aplica a casi toda sesión, a `AGENTS.md`.
La herramienta backpass de Kun Chen automatiza esta pasada; no está instalada ni verificada en esta plantilla.

## Contexto bajo demanda
Revisión: `../sdd-review/SKILL.md`. Entrega y No Mistakes: `../sdd-delivery/SKILL.md`.
GitHub: `../github/SKILL.md`. Planes visuales: `../lavish/SKILL.md`.
