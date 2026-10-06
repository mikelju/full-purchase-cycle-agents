# Cómo trabajar con SDD Lite

Guía de uso del marco de trabajo, para la persona que dirige el proyecto.
El README describe el producto; este archivo describe cómo se trabaja con el agente, y es igual en todos los proyectos.

## Reparto de papeles

Tú decides qué se construye y con qué criterios; el agente decide cómo y lo demuestra con evidencia.
Apruebas en tres momentos: el plan maestro, la spec de cada fase y la PR.
Entre medias, el agente trabaja solo.

## Crear un proyecto

1. Crea una carpeta vacía y ábrela en VS Code.
2. Escribe `/nuevo-proyecto` en Claude Code (se instala una vez con `node "<ruta a SDD-lite>/scripts/instalar-comandos.mjs"`).
   Es un comando de usuario (`~/.claude/commands/nuevo-proyecto.md`) que ejecuta `scripts/crear-proyecto.mjs` de la plantilla.
   Hace `git init` en `main`, copia la plantilla, instala las herramientas auxiliares, pasa las comprobaciones y hace el primer commit.
   Sin el comando, desde la carpeta: `node "C:\Python Projects\PEIA\SDD-lite\scripts\crear-proyecto.mjs"`.
3. Abre una sesión nueva, para que cargue las reglas y los hooks del proyecto, y escribe `/iniciar <de qué va el proyecto>`.
4. Opcional: crea el remoto con `gh repo create <usuario>/<proyecto> --private --source . --push` para recibir las entregas como PR.

## Los cuatro comandos

| Comando | Cuándo | Qué obtienes |
|---|---|---|
| `/iniciar <idea>` | Una vez, al empezar | Plan maestro con las fases, para aprobar |
| `/planear <qué>` | Antes de cada fase o de un cambio suelto | Spec de fase o documento de cambio, para aprobar |
| `/ejecutar <qué>` | Con la spec aprobada | Implementación, revisión adversarial y PR |
| `/verificar <qué>` | Cuando quieras una segunda opinión | Solo hallazgos: no corrige ni publica |

Las skills de `.agents/skills/` no se llaman a mano: cada comando carga las que necesita.
Los comandos son atajos; pedirlo en lenguaje natural funciona igual.

## El ciclo de una fase

1. `/planear fase 01`: el agente escribe `docs/plans/fase-01-nombre/spec.md` con criterios de aceptación y dudas abiertas.
   Respondes las dudas y dices "spec aprobada"; desde ese momento los criterios quedan congelados.
2. `/ejecutar fase 01`: crea la rama, implementa por incrementos, pasa las comprobaciones, se somete a revisión adversarial,
   corrige lo confirmado y abre la PR a `main` con la evidencia por criterio.
   Solo te interrumpe si hay una desviación, un coste nuevo o una decisión tuya.
3. Revisas la PR en GitHub.
   Si hay que cambiar algo, díselo en el chat ("en la PR, el criterio C3 no me convence porque...") y actualiza la PR.
4. Fusionas tú; el agente no puede fusionar ni aprobar.
   Después, en local: `git switch main` y `git pull`.
5. Al cerrar la fase propone como máximo cinco aprendizajes para sus reglas; aceptas o descartas.
6. Siguiente fase: `/planear fase 02`.

## Situaciones habituales

- Bug o ajuste pequeño: `/planear arreglar X` y después `/ejecutar cambio NNN`; si es trivial, basta con "arregla X".
- Retomar otro día: "continúa la fase 02" en una sesión nueva; el estado está en `plan.md` y en Git, no en la conversación.
- Cambiar un criterio ya aprobado: el agente abre una desviación en la carpeta de la fase y te la propone; no lo cambia solo.
- Revisar algo sin tocarlo: `/verificar fase 02` o `/verificar rama X`.
- Auditoría de seguridad, por ejemplo antes de publicar o desplegar: `/verificar seguridad completo`.
  Da hallazgos por gravedad y no toca nada; los alcances posibles están en `.agents/skills/sdd-delivery/SKILL.md`.

## Adoptar SDD Lite en un repositorio propio ya empezado

Para seguir con el marco completo un proyecto tuyo que empezó sin él, por ejemplo con el SDD-WAT antiguo.
Al contrario que el modo aporte, el marco se queda en el repo, versionado.

1. Con el árbol limpio, abre la raíz del repo y escribe `/sdd-adoptar`.
   Crea la rama `adoptar-sdd-lite`, copia lo que no choca y fusiona `.claude/settings.json` y `.gitignore`.
   Crea `.sdd-lite.json`, con el comando de comprobación, el de formato y la rama por defecto.
2. El agente reconoce el repo y fusiona tu `CLAUDE.md` en `AGENTS.md`, conservando lo propio del proyecto.
   También sustituye el flujo antiguo: retira los comandos de proceso y conserva las skills de dominio, `workflows/` y `tools/`.
   Y escribe un plan maestro que empieza con la fase 00, "estado heredado".
3. Te propone con qué comando comprobar y formatear, y todo llega como una PR con la tabla de qué se retira y adónde va cada cosa.
4. Tras fusionar, en una sesión nueva: `/planear fase 01` y el ciclo normal.

## Contribuir a un repositorio existente (modo aporte)

Para trabajar con este método en un repo que no se creó con SDD Lite, como un fork o un proyecto ya empezado, en cualquier lenguaje.
No se copia nada al repo: las skills y los hooks se ejecutan desde la plantilla, y al salir el repo queda como estaba.

1. Abre en VS Code la raíz del repo (no una subcarpeta) y escribe `/sdd-entrar`.
   Activa los hooks y crea tres cosas locales que Git no ve: `.sdd/`, `CLAUDE.local.md` y `.claude/settings.local.json`.
   Su configuración y la foto del estado previo van dentro de `.git/`, donde ninguna rama del repo puede tocarlas.
   Si no sabe cuál es la rama por defecto del repo, te la pregunta.
   Subagentes reconocen el repo y dejan un mapa en `.sdd/mapa.md`, y te propone con qué comando comprobar el trabajo.
2. `/sdd-planear <tu aporte>`: contrato en `.sdd/cambio.md` para que lo apruebes.
3. `/sdd-ejecutar`: rama desde la base del proyecto, implementación con sus convenciones, revisión adversarial y push a tu fork.
   Te enseña el texto de la PR y solo la abre, como borrador, cuando lo confirmas; Claude Code también te lo pregunta.
4. `/sdd-verificar` pide una revisión sin tocar nada; `/sdd-verificar seguridad`, una auditoría de seguridad.
5. `/sdd-salir` cuando termines: archiva tus documentos en `~/.sdd-lite/archivo/<repo>/<fecha>/`,
   restaura el repo byte a byte y comprueba que no queda rastro, tampoco en los commits.

Las normas del repo (`CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`) mandan sobre las del marco.
No muevas ni cambies de rama la plantilla con un aporte abierto: los hooks se ejecutan desde ella.
Si ya ha pasado, sal desde una terminal: `node "<ruta nueva>/scripts/salir.mjs"` en la raíz del repo.
Los comandos `/sdd-*` y `/nuevo-proyecto` se instalan con `node "<ruta a SDD-lite>/scripts/instalar-comandos.mjs"`.
Vuelve a ejecutarlo si mueves la plantilla.

## Lavish en cualquier repositorio

Para que el agente te pida decisiones o comentarios en el navegador en un repo que no tiene SDD Lite, sin adoptarlo ni entrar en modo aporte.
Escribe `/sdd-lavish` seguido de lo que quieres decidir o revisar.
Las páginas y su estado se guardan en `~/.sdd-lite/lavish/`, una carpeta por repo, fuera de él: el repo queda como estaba.
En Lavish van decisiones y comentarios; los informes, en el chat.

## Dónde está cada cosa

| Qué | Dónde |
|---|---|
| Plan maestro | `docs/plans/0_plan_maestro.md` |
| Spec, plan y resultados de una fase | `docs/plans/fase-NN-nombre/` |
| Cambios sueltos | `docs/changes/NNN-nombre.md` |
| Reglas que el agente carga siempre | `AGENTS.md` |
| Comando de comprobación, de formato y rama por defecto (proyectos que no son Node) | `.sdd-lite.json` |
| Procedimientos del agente | `.agents/skills/` |
| Hallazgos de seguridad abiertos o aplazados | `docs/security.md` (se crea con el primero) |
| Bloqueos automáticos (push a `main`, secretos, fusionar) | `.claude/settings.json` y `.claude/hooks/` |

## Consejos

- Una fase por sesión: el contexto se mantiene limpio, sale más barato y el agente trabaja mejor.
- Criterios observables en la spec ("el informe marca las facturas con sobrecoste mayor del 5 %"), no deseos ("que funcione bien").
- Si el agente se atasca dos veces, parará y te explicará la decisión que necesita.
- Tu primer commit lo hace el script; a partir de ahí nadie escribe directamente en `main`, solo por PR.
