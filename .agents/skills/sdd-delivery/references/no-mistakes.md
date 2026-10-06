# No Mistakes

Pipeline de Kun Chen: rebase, revisión en contexto limpio, tests con evidencia, documentación, lint, push, PR y vigilancia de CI.
Repositorio: https://github.com/kunchenguid/no-mistakes.
Versión probada en esta plantilla: 1.79.0, solo el binario; el pipeline nunca se ha inicializado aquí.

## Instalación local
Binarios y estado no se versionan.
Descarga el binario de tu plataforma desde las releases oficiales, verifica `checksums.txt` y extráelo en `.runtime/bin/`.

```powershell
$env:NO_MISTAKES_TELEMETRY = '0'
$env:NO_MISTAKES_NO_UPDATE_CHECK = '1'
.\.runtime\bin\no-mistakes.exe --version
```

## Activación en un proyecto
1. Configura el remoto autorizado y adapta `.no-mistakes.yaml`: preparación, tests y lint reales, y `agent` según el runner que uses.
2. El usuario integra esa configuración en la rama por defecto del remoto, porque el gate lee sus comandos de esa rama de confianza.
   El agente no puede hacerlo: la guardia bloquea escribir en `main`.
3. Sigue la instalación oficial y ejecuta `doctor` para comprobar el runner.
4. Ejecuta `init` solo con autorización: registra el repo, instala skills de usuario y arranca un daemon fuera de la carpeta.
5. `no_ci: true` describe una plantilla sin CI; retíralo cuando el producto tenga CI real.

## Operación
1. Con el gate activo, carga la skill oficial `~/.agents/skills/no-mistakes/SKILL.md` que instala `init` y consulta la ayuda del binario.
2. Implementa y pasa las comprobaciones locales antes del gate: valida y corrige, no define el objetivo.
3. Lanza con `no-mistakes axi run --intent "<objetivo original>" --wait 45s`; `axi` es un subcomando del binario.
   No uses `--yes`: resolvería decisiones de intención sin consultar.
4. Sigue el ID con `no-mistakes axi status`; una espera vencida no es un fallo. Reutiliza el run, no crees duplicados.
5. Resuelve hallazgos dentro del contrato; cambios de intención o permisos vuelven al usuario.
6. Registra ID, commit validado, checks, evidencia y omisiones en el plan de fase o el cambio; tras nuevos cambios, revalida.

La configuración limita a una ronda de reparación automática por etapa; no es un tope de tokens.
El gate puede sincronizar ramas y escribir en GitHub: una autorización para editar en local no autoriza ese circuito.
