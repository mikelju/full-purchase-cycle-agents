# SDD Lite

Plantilla vacía para arrancar proyectos grandes con agentes de código.
Tú decides qué se construye y con qué criterios; el agente planifica, implementa, se somete a revisión adversarial y entrega con evidencia.
No contiene producto, `tools/` ni `workflows/`: solo el método.

## Cómo se trabaja

La guía de uso completa, con comandos, ciclo de fase y situaciones habituales, está en [docs/como-trabajar.md](docs/como-trabajar.md).
Se copia a cada proyecto y no depende de su README.

| Paso | Quién decide | Dónde queda |
|---|---|---|
| `/iniciar` - plan maestro dividido en fases | Tú apruebas | `docs/plans/0_plan_maestro.md` |
| `/planear fase 01` - objetivo, alcance y criterios observables | Tú apruebas y quedan congelados | `docs/plans/fase-01-nombre/spec.md` |
| Plan por incrementos | El agente | `docs/plans/fase-01-nombre/plan.md` |
| `/ejecutar fase 01` - implementar, comprobar, revisar y corregir | El agente, dentro de la spec | Rama de trabajo y `plan.md` |
| Integrar | Tú | PR revisada por ti |

Lo que no fija criterios para trabajo futuro (arreglos, ajustes) va a `docs/changes/NNN-nombre.md`.
Lo que altera criterios de una fase aprobada es una desviación que vuelve a ti.
`/verificar` pide una revisión adversarial sin correcciones ni publicación; `/verificar seguridad`, una auditoría de seguridad.
La regla de enrutado completa vive en `.agents/skills/sdd-lite/SKILL.md`.

## Contexto con presupuesto

`AGENTS.md` es lo único que se carga siempre (`CLAUDE.md` lo importa) y tiene un tope de 5.000 tokens.
Todo lo condicional vive en skills de `.agents/skills/` que se cargan cuando la tarea las necesita, con el detalle largo en `references/`.
Las reglas nuevas entran con evidencia de al menos dos sesiones y, si no caben, otra sale.

## Garantías

Lo que debe cumplirse siempre no se confía al prompt: lo aplican hooks deterministas configurados en `.claude/settings.json`.

| Hook | Evento | Qué hace |
|---|---|---|
| `guard.mjs` (reglas en `guard-reglas.mjs`) | `PreToolUse` en Bash y PowerShell | Bloquea push a `main`/`master` (también vía `HEAD`), push forzado, commit, merge o rebase con HEAD en `main`, fusionar o aprobar PR (con `gh`, `gh-axi` o `gh api`), leer, copiar o enviar secretos y `git clean -x`. Analiza comandos anidados en `bash -c`, PowerShell, `cmd /c`, `$(...)`, código en línea de Node o Python y heredocs que recibe un shell o un lenguaje; sigue `cd` y `git -C`. Terminar o abortar un rebase o merge en curso está permitido. Si no puede evaluar o no sabe la rama, bloquea |
| `format.mjs` | `PostToolUse` en Write y Edit | Ejecuta el script `format` del `package.json` sobre el archivo editado; sin script no hace nada |
| `stop-gate.mjs` | `Stop` | Con cambios en el árbol, no deja cerrar el turno si `npm run check` falla |

Los `deny` de permisos cubren lectura de `.env*` y credenciales con la herramienta Read, y respaldan a la guardia si `node` no estuviera disponible.
Como los `deny` no admiten excepciones, `.env.example` tampoco se lee con Read; `cat .env.example` sí está permitido.
`node .claude/hooks/test-hooks.mjs` prueba los tres hooks con 130 casos en repos temporales y forma parte de `npm run check`.
La guardia tarda entre 100 y 210 ms por comando en esta máquina; los encadenados que consultan git dos veces rozan el objetivo de 200 ms.
Un guardia que falla en silencio cuesta la rama por defecto: ejecuta la suite en cada copia antes de confiar en ella.
Las notificaciones de escritorio son preferencia personal y van en `.claude/settings.local.json` o en la configuración de usuario.

## Revisión adversarial

El autor no se revisa en su propio contexto.
Hasta cuatro revisores en contexto limpio (corrección, seguridad y datos, evidencia y tests, alcance y mantenimiento) reciben los criterios y el diff, nunca el razonamiento del implementador.
Cada hallazgo exige `archivo:línea` y un escenario de fallo concreto; el coordinador los reproduce antes de corregir, con un máximo de dos rondas.
El backend local funciona sin remoto desde el primer día.
[No Mistakes](https://github.com/kunchenguid/no-mistakes) sustituye a la revisión local cuando se cumplen las precondiciones de `.agents/skills/sdd-delivery/SKILL.md`.
Detalle en `.agents/skills/sdd-review/` y `.agents/skills/sdd-delivery/`.

## Herramientas opcionales

Requisitos: Node.js 20+, Git y GitHub CLI.
Lavish 0.1.76 y gh-axi 0.1.35 están fijados en el lockfile; instala con `npm ci --ignore-scripts --no-audit --no-fund`.

- [gh-axi](https://github.com/kunchenguid/gh-axi): consultas a GitHub con salida compacta, en vez de un servidor MCP. `npm run github -- --help`.
- [Lavish](https://github.com/kunchenguid/lavish-axi): revisar planes complejos en HTML con feedback visual. `npm run lavish -- --help`.
- No Mistakes: binario 1.79.0 en `.runtime/bin/`, sin pipeline inicializado. Activación en `.agents/skills/sdd-delivery/references/no-mistakes.md`.
- backpass: pasada de aprendizaje sobre transcripciones para podar `AGENTS.md`; no instalada ni verificada.

No se usan Firstmate (macOS/Linux y tmux), gnhf, treehouse, WezTerm, tmux ni Neovim.
El harness ya aporta subagentes, worktrees y bucles; no se duplica esa infraestructura.

## Copiar la plantilla para un proyecto

1. Crea la carpeta del proyecto, vacía, y ábrela en VS Code.
2. En Claude Code escribe `/nuevo-proyecto`, o en una terminal dentro de la carpeta ejecuta `node "<ruta a SDD-lite>/scripts/crear-proyecto.mjs"`.
   El script hace `git init` en `main`, copia la plantilla sin su historial de cambios, instala dependencias, pasa `npm run check` y hace el primer commit.
3. Abre una sesión nueva de Claude Code, para que cargue `AGENTS.md` y los hooks, y escribe `/iniciar <descripción del proyecto>`.
   El agente sustituye los textos de la plantilla por los del producto (lista en `.agents/skills/sdd-lite/SKILL.md`) y redacta contigo el plan maestro.
4. Opcional: `gh repo create <usuario>/<proyecto> --private --source . --push` para que las entregas lleguen como PR.
5. Cuando haya código, añade al script `check` las pruebas reales conservando la suite de hooks, y un script `format` si hay formateador.
6. Para No Mistakes, adapta `.no-mistakes.yaml` cuando haya remoto y autorización.

`/nuevo-proyecto` es un comando de usuario en `~/.claude/commands/nuevo-proyecto.md`, fuera de este repositorio; si no lo tienes, usa la línea de `node`.

Si copias el proyecto dentro de una carpeta que tiene su propio `CLAUDE.md` o `AGENTS.md`, el harness también los cargará: comprueba que no contradigan este.

## Adoptar el marco en un repositorio propio ya empezado

`/sdd-adoptar` (o `node scripts/adoptar.mjs` más la skill `.agents/skills/sdd-adoptar/`) deja el marco versionado en un repo existente mediante una rama y una PR.
Sustituye un flujo previo como el SDD-WAT y conserva lo propio del proyecto.
En repos que no son Node, `.sdd-lite.json` guarda `comprobar`, `formatear` y la rama por defecto, y no se añade `package.json`.
`scripts/test-adoptar.mjs` lo prueba en repos temporales y forma parte de `npm run check`.

## Contribuir a un repositorio existente

El modo aporte usa este método en repos ajenos o ya empezados sin copiar nada a ellos y sin dejar huella al salir.
Los scripts `scripts/entrar.mjs` y `scripts/salir.mjs` y la skill `.agents/skills/sdd-aporte/` solo viven en la plantilla.
Los comandos de usuario (`/sdd-entrar`, `/sdd-planear`, `/sdd-ejecutar`, `/sdd-verificar`, `/sdd-salir`, `/sdd-adoptar`, `/sdd-lavish` y `/nuevo-proyecto`)
se instalan con `node scripts/instalar-comandos.mjs`. Uso: [docs/como-trabajar.md](docs/como-trabajar.md).
`scripts/test-aporte.mjs` prueba la entrada y la salida en repos temporales y forma parte de `npm run check`.

## Límites

- La revisión adversarial local está descrita y enrutada, pero su eficacia depende del modelo y del harness; no hay métrica propia todavía.
- Las cifras de eficacia de Kun Chen y del Claude Code Playbook son autopublicadas y sin réplica independiente: guían el diseño, no lo prueban.
- La guardia es léxica: cubre las formas naturales de un agente, no la ofuscación deliberada (por ejemplo, `Start-Process` con argumentos o un script que llame a git).
  Si `node` no arranca, el hook falla abierto y solo quedan los `deny`.
- Hooks y permisos son de Claude Code; otros harness leen `AGENTS.md` y las skills, pero no aplican estas garantías.
- `npm run check` en la plantilla comprueba dependencias y hooks, no comportamiento de producto.

El [cambio 003](docs/changes/003-plantilla-proyectos-grandes.md) registra qué se comprobó y qué queda pendiente; 001 y 002 son históricos.
Fuentes: [Kun Chen](https://github.com/kunchenguid), [Your AGENTS.md is a Neural Net](https://blog.kunchenguid.com), [AXI](https://github.com/kunchenguid/axi) y el resumen del Claude Code Playbook de Jason Zhou en `docs/refs/`.
