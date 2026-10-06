// Guardia PreToolUse de SDD Lite: punto de entrada del hook.
//
// Contrato (docs oficiales de hooks): salir con 2 bloquea SIEMPRE y gana sobre el JSON.
// Evalua siempre, sin condiciones: un fallo al cargar las reglas o al leer la entrada
// bloquea. Las reglas viven en guard-reglas.mjs para que la suite las evalue en proceso.

let r;
try {
  // Carga dinamica: si falta un modulo, el error cae en el catch y se bloquea.
  const { leerEntrada } = await import("./lib.mjs");
  const { evaluar } = await import("./guard-reglas.mjs");
  r = evaluar(await leerEntrada());
} catch (error) {
  const texto = `[SDD Lite] Bloqueado por .claude/hooks/guard.mjs\nEl guardia no ha podido evaluar el comando: ${error.message}\n`
    + "Se bloquea por precaucion. Revisa .claude/hooks/guard-reglas.mjs o desactiva el hook en .claude/settings.json"
    + " (en modo aporte, en .claude/settings.local.json).";
  r = {
    codigo: 2,
    stdout: JSON.stringify({ hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: texto } }),
    stderr: `${texto}\n`,
  };
}
process.stdout.write(r.stdout);
process.stderr.write(r.stderr);
process.exit(r.codigo);
