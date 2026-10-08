// Puerta de calidad en Stop: el agente no da por terminado un turno con las
// comprobaciones del proyecto en rojo. Pasa de "parece hecho" a "comprobado".
//
// Contrato (docs oficiales de hooks): en Stop, salir con 2 impide parar y entrega
// stderr al agente para que corrija. `stop_hook_active` indica que ya se le
// devolvio una vez; entonces se deja parar para no entrar en bucle.
//
// Solo actua si el arbol tiene cambios: un turno de consulta no paga la comprobacion.
// Comando: `comprobar` de la configuracion (aporte o `.sdd-lite.json`); si no, el script
// `check:puerta` del package.json, o `check` si no existe. Sin comando, no hace nada.
// `check:puerta` es la parte rapida; `check` completo se exige antes de entregar.

import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { conComandosFiables, configProyecto, leerEntrada } from "./lib.mjs";

function salirConError(texto) {
  process.stderr.write(`[SDD Lite] ${texto}\n`);
  process.exit(2);
}

const entrada = await leerEntrada().catch(() => ({}));
if (entrada.stop_hook_active) process.exit(0);

const raiz = process.env.CLAUDE_PROJECT_DIR || entrada.cwd || process.cwd();

// Precedencia: configuracion del aporte > `.sdd-lite.json` del proyecto > `check` de package.json.
// En modo aporte nunca se cae a package.json: seria el del repo ajeno.
let comando;
let config;
try {
  config = configProyecto(raiz);
} catch {
  salirConError("La configuracion del proyecto (.sdd-lite.json o la del aporte) es ilegible: la puerta de calidad no puede evaluar.");
}
// Solo se ejecuta el `comprobar` de la rama por defecto, nunca el que traiga otra rama.
const fiable = conComandosFiables(raiz, config);
config = fiable.config;
if (fiable.aviso) process.stderr.write(fiable.aviso);
if (config?.comprobar) {
  comando = config.comprobar;
} else if (config?.aporte) {
  process.exit(0);
} else {
  const paquete = join(raiz, "package.json");
  if (!existsSync(paquete)) process.exit(0);
  let scripts = {};
  try {
    scripts = JSON.parse(readFileSync(paquete, "utf8")).scripts || {};
  } catch {
    salirConError("package.json ilegible: la puerta de calidad no puede evaluar.");
  }
  const script = scripts["check:puerta"] ? "check:puerta" : "check";
  if (!scripts[script]) process.exit(0);
  comando = `npm run --silent ${script}`;
}

try {
  const estado = execFileSync("git", ["status", "--porcelain"], {
    cwd: raiz, encoding: "utf8", timeout: 5000, stdio: ["ignore", "pipe", "ignore"],
  });
  if (!estado.trim()) process.exit(0);
} catch {
  // Sin git no se puede saber si hubo cambios: se comprueba igualmente.
}

const r = spawnSync(comando, { cwd: raiz, encoding: "utf8", shell: true, timeout: 290000 });
if (r.status === 0) process.exit(0);

const salida = `${r.stdout || ""}\n${r.stderr || ""}`.trim().split(/\r?\n/).slice(-25).join("\n");
salirConError(
  `Puerta de calidad: \`${comando}\` falla con cambios sin comprobar.\n`
  + "Corrige la causa o, si el fallo es ajeno al cambio, dejalo registrado como pendiente.\n"
  + `Codigo ${r.status ?? "sin codigo (tiempo agotado)"}. Ultimas lineas:\n${salida}`,
);
