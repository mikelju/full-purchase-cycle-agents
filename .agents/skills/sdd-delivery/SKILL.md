---
name: sdd-delivery
description: Validar y entregar un incremento, fase o cambio - comprobaciones, evidencia por criterio, revisión adversarial, documentación y rama o PR - con backend local o No Mistakes. Usar al terminar de implementar, al cerrar una fase o antes de publicar; comprobar precondiciones antes de afirmar que un gate pasó.
---
# Entrega

La validación es un pipeline, no una lectura del diff. Cada etapa deja evidencia en el plan de fase o en el cambio.

## Pipeline local (por defecto)
1. Comprobaciones del proyecto en verde (`npm run check` o las del mapa de `AGENTS.md`).
   La puerta `Stop` las repite; un rojo ajeno al cambio se registra como pendiente, no se oculta.
2. Evidencia por criterio: comando o recorrido real y resultado observado, obtenidos tras el último cambio que lo afecta.
3. Revisión adversarial con `../sdd-review/SKILL.md` sobre el rango completo contra la base, archivos nuevos incluidos.
4. Documentación afectada al día: plan de fase, plan maestro, README y skills que el cambio contradiga.
5. Entrega: commit en la rama de trabajo; si hay remoto `origin`, push de la rama y PR a `main` para que la revise el usuario.
   El cuerpo de la PR, en un archivo con `--body-file`: objetivo, criterios con su evidencia, tabla de revisión adversarial y pendientes.
   Sin remoto, la entrega es la rama local. Otro remoto o destino requiere autorización.
   Nunca fusionar ni aprobar: los hooks lo bloquean y la decisión es del usuario.
   No se entrega ni se cierra una fase o un cambio con un hallazgo de seguridad Critical o High abierto en su rango,
   salvo aplazamiento decidido por el usuario y anotado en `docs/security.md`.
   Los abiertos de otras partes no bloquean, pero la revisión de seguridad los menciona si el rango toca ese código.
Modo verificación (`/verificar`): se ejecutan 1 a 3 sin corregir ni publicar y se entregan hallazgos y límites.

## Auditoría de seguridad (`/verificar seguridad [alcance]`)
Sustituye a las etapas 1 a 3: una sola revisión con la lente de seguridad y datos de `../sdd-review/references/lentes.md`,
sin corregir ni escribir nada. Es la única definición de este modo.
- Alcance: `completo` (todo el código, antes de publicar o desplegar), `deps` (manifiestos y CVE),
  `secretos` (árbol e historial), una ruta o, sin alcance, lo que cambia la rama respecto a la base.
- No rige el tope de diez hallazgos de `sdd-review`: se entregan todos los del alcance.
  Si el alcance resulta vacío (por ejemplo, sin alcance desde `main`), se dice y no se da ninguna conclusión.
- En el chat: hallazgos por severidad, lo que no se revisó y qué herramientas faltan.
- Al final propone catalogar en `docs/security.md` los que el usuario quiera conservar; si acepta, se hace con `/planear`,
  como un cambio suelto, igual que sus arreglos.

## Backend No Mistakes
Usarlo solo si se cumplen todas: remoto configurado, `.no-mistakes.yaml` adaptado al producto,
gate inicializado, runner operativo y autorización de publicación, porque el gate empuja y abre PR.
Entonces sustituye a las etapas 3 a 5 para ese rango; no se duplica con la revisión local.
Instalación, activación y operación: `references/no-mistakes.md`.
Sin alguna precondición: pipeline local y se declara "No Mistakes pendiente", nunca "gate aprobado".

## Gotchas
- Omitir etapas para conseguir verde es rebajar criterios.
- Un escáner limpio no sustituye revisar permisos, entradas externas, secretos y dependencias.
- Las comprobaciones de la plantilla solo cubren dependencias y hooks; un producto debe sustituirlas por pruebas reales.
