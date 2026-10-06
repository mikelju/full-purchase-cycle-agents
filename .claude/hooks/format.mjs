// Formateo tras editar (PostToolUse sobre Write|Edit).
//
// La plantilla no impone formateador. En un proyecto SDD Lite se declara como script
// `format` en package.json y recibe la ruta del archivo editado como ultimo argumento.
// En modo aporte se usa `formatear` de `.sdd/config.json`; `{archivo}` marca donde va
// la ruta. Nunca se usa el `format` de package.json de un repo ajeno: suele formatear
// el repositorio entero y ensuciaria la contribucion.
// Sin comando el hook sale al instante. Un formateador que falla no bloquea nada
// (la edicion ya ocurrio); con codigo 2 su salida llega al agente.

import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { conComandosFiables, configProyecto, leerEntrada } from "./lib.mjs";

const entrada = await leerEntrada().catch(() => ({}));
const archivo = entrada?.tool_input?.file_path;
if (typeof archivo !== "string" || !archivo) process.exit(0);

const raiz = process.env.CLAUDE_PROJECT_DIR || entrada.cwd || process.cwd();

let config = null;
try { config = configProyecto(raiz); } catch { process.exit(0); }
// Solo se ejecuta el `formatear` de la rama por defecto, nunca el que traiga otra rama.
const fiable = conComandosFiables(raiz, config);
config = fiable.config;
if (fiable.aviso) process.stderr.write(fiable.aviso);

// Los documentos de trabajo del modo aporte no son parte del repositorio.
if (config?.aporte && /^\.sdd([\\/]|$)/.test(relative(raiz, archivo))) process.exit(0);

let plantilla;
if (config?.formatear) {
  plantilla = config.formatear.includes("{archivo}") ? config.formatear : `${config.formatear} {archivo}`;
} else if (config) {
  // Aporte o repo adoptado: el script `format` del repo puede formatear todo el arbol.
  process.exit(0);
} else {
  const paquete = join(raiz, "package.json");
  if (!existsSync(paquete)) process.exit(0);
  let scripts = {};
  try {
    scripts = JSON.parse(readFileSync(paquete, "utf8")).scripts || {};
  } catch {
    process.exit(0);
  }
  if (!scripts.format) process.exit(0);
  plantilla = "npm run --silent format -- {archivo}";
}

// La ruta llega a un shell: solo se admiten caracteres sin significado para
// bash, PowerShell o cmd. Cualquier otra cosa se avisa y no se formatea.
// `[ ] ( ) @` son literales dentro de comillas dobles en los tres shells y comunes en rutas web.
if (!/^[\p{L}\p{N} _.,+\-\\/:@()[\]]+$/u.test(archivo)) {
  process.stderr.write(`[SDD Lite] Ruta con caracteres no seguros, no se formatea: ${archivo}\n`);
  process.exit(2);
}

const r = spawnSync(plantilla.replaceAll("{archivo}", `"${archivo}"`), {
  cwd: raiz, encoding: "utf8", shell: true, timeout: 30000,
});
if (r.status === 0) process.exit(0);

const salida = `${r.stdout || ""}\n${r.stderr || ""}`.trim().split(/\r?\n/).slice(-15).join("\n");
process.stderr.write(`[SDD Lite] El formateador fallo sobre ${archivo}:\n${salida}\n`);
process.exit(2);
