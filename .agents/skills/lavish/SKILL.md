---
name: lavish
description: Pedir decisiones y comentarios al usuario, o que revise planes, alternativas o maquetas, mediante HTML local y feedback visual con Lavish. Usar cuando la presentación visual ayude a decidir, no para cada arreglo pequeño ni para informes.
---
# Lavish

- Fuera de un proyecto SDD Lite (sin `.agents/skills/lavish/` en el repo), usa solo la última sección.
- En cada llamada a Lavish, también las que sugiere su propia salida (`next_step`), fija en ese mismo comando
  `LAVISH_AXI_STATE_DIR` (ruta absoluta de `.runtime/lavish` de este repo) y `LAVISH_AXI_TELEMETRY=0`:
  Lavish envía por defecto estadísticas de uso, los proyectos de PEIA son privados y el shell no conserva variables entre llamadas.
- Sin `package.json` con lavish-axi (repos que no son Node), sustituye `npm run lavish --` por `npx -y lavish-axi@0.1.76` en lo que sigue.
- Consulta `npm run lavish -- --help` y el playbook pertinente antes de crear HTML.

## Cuándo
- Lavish es para decidir y comentar: preguntas con opciones, alternativas, planes que el usuario debe aprobar o anotar.
- Los informes, resúmenes y explicaciones van en el chat: el usuario los lee mejor ahí.
- Una página por ronda de decisiones: una o dos frases de contexto, el estado imprescindible y las preguntas.
- Cada cifra o etiqueta se entiende sola, sin conocer el plan: "3 de 5 pasos del plan", nunca "3 / 5". Si no aporta, no se pone.
- Al recibir el feedback, confirma en el panel con `--agent-reply` qué ha llegado: el usuario no siempre sabe si se envió.

## Aspecto
- El usuario fijó el estilo: parte siempre de `.agents/skills/lavish/references/plantilla.html`
  y copia `.agents/skills/lavish/references/estilo.css` junto al HTML en `.lavish/`.
- No uses el tema por defecto de Lavish ("luxury"), DaisyUI ni colores propios: solo las clases de `estilo.css`.
  Esto responde al paso 1 de `design` (estilo pedido por el usuario); no hace falta consultarlo.
- Excepción: la maqueta de una pantalla de un producto se pinta con el sistema de diseño de ese producto (paso 2 de `design`);
  la hoja de SDD Lite queda para el marco de la página y las preguntas.
- Preguntas con el playbook `input`: opción recomendada primero y marcada, un botón «Añadir respuesta» por pregunta con `queueKey`,
  y al final recuerda al usuario que pulse Send to Agent. Las respuestas llegan por `poll`.

## Sesión
- Guarda el artefacto en `.lavish/`; abre con `npm run lavish -- .lavish/plan.html` y recoge feedback con `npm run lavish -- poll .lavish/plan.html`.
- Mantén el poll ligado a la sesión; no prometas vigilancia después de cerrarla ni interpretes silencio como aprobación.
- El HTML es temporal: decisiones aceptadas y criterios viven en la spec de fase o en el cambio, sin mantener dos planes.
- No uses `share` ni recursos externos con información privada; cerrar o exportar se rige por la ayuda instalada.

## Fuera de un proyecto SDD Lite
Con `/sdd-lavish`, en un repositorio sin el marco. `<plantilla>` es la carpeta de SDD Lite.
Todo pasa por `node "<plantilla>/scripts/lavish-suelto.mjs"`, que sustituye a `npm run lavish --` en las secciones anteriores:
fija telemetría apagada, un puerto y un estado propios, y ejecuta Lavish desde la carpeta de trabajo, fuera del repo.
- Antes de nada, guarda `git status --porcelain --ignored` del repo; al terminar debe ser idéntico.
- `... lavish-suelto.mjs carpeta` imprime la carpeta de trabajo (`~/.sdd-lite/lavish/<repo>-<huella>/`) y deja allí
  `estilo.css` y `plantilla.html`. Escribe ahí la página, nunca en `.lavish/` del repo.
- Abre, recoge feedback y cierra con `... lavish-suelto.mjs plan.html`, `... poll plan.html` y `... end plan.html`:
  los `.html` relativos se resuelven en la carpeta de trabajo. Rutas siempre entre comillas.
- Ni `.lavish/`, ni `.runtime/`, ni menciones del marco en el repo, sus commits o sus PR.
- No hay documento de cambio: resume lo decidido en el chat.
