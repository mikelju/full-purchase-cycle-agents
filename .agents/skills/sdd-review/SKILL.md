---
name: sdd-review
description: Revisión adversarial en contexto limpio de un diff, rama, incremento, fase o PR contra sus criterios. Usar al cerrar un incremento o una fase, antes de abrir una PR, o cuando el usuario pida revisar, auditar, buscar fallos, "¿está listo?" o una segunda opinión sobre un cambio. No para leer código sin cambios.
---
# Revisión adversarial

El autor no se revisa en su propio contexto: arrastra sus suposiciones y se ancla en ellas.
La revisión la hacen revisores que no han visto la implementación y que buscan cómo falla, no si parece correcta.

## Qué recibe cada revisor
- Los criterios: `spec.md` de la fase o el contrato del cambio.
- El rango: `git diff <base>` (commits y árbol de trabajo) más `git ls-files --others --exclude-standard`.
  Antes de lanzar, comprueba que el rango no está vacío; una revisión de un diff vacío no cuenta.
- `AGENTS.md` y el brief de su lente en `references/lentes.md`.
- Nunca la conversación, el razonamiento ni el resumen del implementador.

## Cuántas lentes
- Arreglo trivial: ninguna; bastan las comprobaciones del proyecto.
- Cambio normal: corrección y la lente que más riesgo cubra.
- Cierre de fase, seguridad, datos o dependencias: las cuatro, en paralelo y en contextos separados.
  La auditoría `/verificar seguridad` es otra cosa: una sola lente, con su definición en `../sdd-delivery/SKILL.md`.
Sin subagentes en el harness: una sesión nueva por lente, en serie.

## Contrato de hallazgo
Cada hallazgo trae `archivo:línea`, severidad (bloqueante, importante, menor), el defecto en una frase,
un escenario de fallo concreto (entrada o estado, resultado incorrecto) y el criterio afectado.
Sin escenario concreto no es un hallazgo. Una lista vacía es un resultado válido.

## Verificación, corrección y cierre
1. El coordinador intenta reproducir cada bloqueante e importante: test, comando o lectura dirigida.
   Marca confirmado, plausible o descartado; descartar exige motivo escrito.
2. Corrige lo confirmado dentro del contrato, con regresión cuando aporte valor.
   Lo que cambia criterios es una desviación, no una corrección.
3. Re-revisa solo el diff de las correcciones con una lente fresca. Máximo dos rondas;
   lo que siga abierto pasa al usuario como pendiente.
4. Registra cada ronda en la tabla "Revisión adversarial" del plan de fase o del cambio.
5. Hallazgos de seguridad que quedan abiertos o aplazados: una línea en `docs/security.md`, que se crea con el primero.
   Columnas: ID, severidad de seguridad (Critical, High, Medium, Low), dónde, qué pasa, estado y origen (fase o cambio).
   El ID sigue al mayor `SEC-NNN` usado en el repo, contando catálogos anteriores (`docs/security/`) aunque estén cerrados.
   Al corregir uno, se marca corregido con su regresión.
   Es la única lista de aplazados de seguridad; la regla de entrega con Critical o High abiertos está en `../sdd-delivery/SKILL.md`.
En modo verificación, definido en `../sdd-delivery/SKILL.md`, se detiene tras el paso 1 y entrega los hallazgos, sin escribir nada.

## Backends
- Local, por defecto: lo descrito arriba; funciona sin remoto, que es el estado de toda copia nueva.
- No Mistakes: solo cuando se cumplan las precondiciones de `../sdd-delivery/SKILL.md`, que son la única fuente.
  Su revisión sustituye a la local para ese rango; no se duplican.
- Las revisiones integradas del harness (por ejemplo `/code-review` o `/security-review` en Claude Code)
  pueden aportar una lente más, pero sus hallazgos entran por el mismo contrato y la misma verificación.

## Gotchas
- Un revisor que recibe el resumen del autor confirma el resumen, no el código.
- "Los tests pasan" no prueba nada si el test se escribió para pasar; la lente de evidencia lo comprueba.
- Tras cualquier corrección, la evidencia anterior de los criterios afectados deja de valer.
