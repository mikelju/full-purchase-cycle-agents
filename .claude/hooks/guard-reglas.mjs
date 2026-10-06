// Reglas de la guardia PreToolUse de SDD Lite: lo que nunca debe ocurrir, no ocurre.
// El hook es guard.mjs, que solo lee la entrada y llama a `evaluar`; la suite importa este modulo.
//
// Contrato (docs oficiales de hooks): salir con 2 bloquea SIEMPRE y gana sobre el JSON.
// Por eso se emite el JSON estructurado y ademas se sale con 2: si el esquema del JSON
// cambiase, el bloqueo sigue en pie.
//
// Ante un fallo interno o una duda sobre la rama, bloquea en vez de dejar pasar.
// El matcher solo cubre Bash y PowerShell, asi que Edit y Write siguen disponibles
// para arreglar este archivo si alguna vez se pasa de estricto.
//
// El analisis es lexico: cubre las formas naturales de un agente, no la ofuscacion
// deliberada. Los limites conocidos estan en README.md.

import { execFileSync } from "node:child_process";
import { homedir } from "node:os";
import { resolve } from "node:path";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import {
  comandosAnalizables, configProyecto, cuerposDeTexto, ramaRemotaPorDefecto, dirAporte, nombreArchivo, soloEncadenadoConY, verboDe,
  LENGUAJES, FLAGS_CODIGO, RASTRO,
} from "./lib.mjs";

// Se anade la rama por defecto real del repositorio (p. ej. `develop`) si la declara la
// configuracion del aporte o el `.sdd-lite.json` del proyecto.
const RAMAS_PROTEGIDAS = new Set(["main", "master"]);

// Rama tras un `git switch` o `git checkout -b` anterior en el mismo comando, por directorio.
// null significa desconocida, y bloquea como si no se supiera la rama.
const ramasSimuladas = new Map();
let encadenadoSeguro = true;
let aporteActivo = false;

// Archivos locales del marco: nunca entran en un commit.
const LOCALES_DEL_MARCO = new Set(["claude.local.md", "settings.local.json"]);

const VERBOS_LECTURA = new Set([
  "cat", "type", "head", "tail", "more", "less", "bat", "sed", "awk", "grep", "egrep", "fgrep", "rg", "ack",
  "findstr", "strings", "od", "xxd", "hexdump", "base64", "sort", "uniq", "nl", "tac", "cut", "paste",
  "diff", "cmp", "source", ".", "cp", "mv", "copy", "move", "scp", "rsync", "curl", "wget",
  "nc", "ncat", "get-content", "gc", "select-string", "sls", "copy-item", "cpi", "move-item", "mi",
  "import-csv", "invoke-webrequest", "iwr", "invoke-restmethod", "irm", "jq", "yq", "openssl", "__codigo__",
  ...LENGUAJES,
]);

// Verbos que cambian el directorio de los comandos siguientes.
const VERBOS_CD = new Set(["cd", "chdir", "pushd", "set-location", "sl", "push-location"]);

// Verbos cuyo primer argumento posicional es un patron de busqueda, no un archivo.
const VERBOS_PATRON = new Set(["grep", "egrep", "fgrep", "rg", "ack", "findstr", "select-string", "sls"]);
// Flags que llevan un patron como valor.
const FLAGS_PATRON = new Set(["-e", "--regexp", "-pattern", "--pattern"]);

// Subcomandos de git que escriben en la rama actual.
// `pull` y `reset` quedan fuera: actualizar main o sacar archivos del stage es trabajo normal.
const GIT_ESCRIBE_RAMA = new Set(["commit", "merge", "rebase", "cherry-pick", "revert", "am"]);

// Verbos de copia: el ultimo argumento es el destino, no se lee.
const VERBOS_COPIA = new Set(["cp", "mv", "copy", "move", "scp", "rsync", "copy-item", "cpi", "move-item", "mi"]);

/** Bloqueo en curso: lo lanza `bloquear` y solo lo recoge `evaluar`. */
class Bloqueo extends Error {}

function bloquear(motivo, salida) {
  throw new Bloqueo(`[SDD Lite] Bloqueado por .claude/hooks/guard.mjs\n${motivo}\n${salida}`);
}

/** Salida del hook que bloquea: JSON estructurado en stdout, texto en stderr y codigo 2. */
function respuestaBloqueo(texto) {
  return {
    codigo: 2,
    stdout: JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: texto,
      },
    }),
    stderr: `${texto}\n`,
  };
}

function respuestaFallo(error) {
  return respuestaBloqueo(`[SDD Lite] Bloqueado por .claude/hooks/guard.mjs\nEl guardia no ha podido evaluar el comando: ${error.message}\n`
    + "Se bloquea por precaucion. Revisa .claude/hooks/guard.mjs o desactiva el hook en .claude/settings.json"
    + " (en modo aporte, en .claude/settings.local.json).");
}

function esSecreto(token) {
  // `@` adjunta un archivo en curl (`-d@x`, `-F f=@x`); `rev:ruta` en git show.
  // `!patron` es una exclusion y `https://...` una URL publica, no archivos locales.
  let t = String(token);
  if (t.startsWith("!") || /^[a-z][a-z0-9+.-]*:\/\//i.test(t)) return false;
  if (t.includes("@")) t = t.slice(t.lastIndexOf("@") + 1);
  const n = nombreArchivo(t.includes(":") ? t.slice(t.lastIndexOf(":") + 1) : t)
    .toLowerCase().replace(/[*?]+$/, "");
  if (/^\.env\.(example|sample|template|dist)$/.test(n)) return false;
  if (/^\.env(\..+)?$/.test(n)) return true;
  if (["credentials", "credentials.json", "token.json", ".netrc", ".npmrc", ".pgpass"].includes(n)) return true;
  if (/^id_(rsa|ed25519|ecdsa|dsa)$/.test(n)) return true;
  if (/\.(pem|p12|pfx|key|keystore|jks)$/.test(n)) return true;
  if (/^service[-_]?account.*\.json$/.test(n) || /(^|[-_])sa[-_]?key.*\.json$/.test(n)) return true;
  if (/^secrets?\.(json|ya?ml|toml|txt|env)$/.test(n)) return true;
  return false;
}

function git(cwd, args) {
  return execFileSync("git", args, { cwd, encoding: "utf8", timeout: 4000, stdio: ["ignore", "pipe", "ignore"] }).trim();
}
// Alias para las funciones donde `git` es el resultado de analizarGit.
const git_ = git;

/**
 * Rama actual; funciona tambien en una rama sin commits. Con HEAD separado
 * (rebase o cherry-pick en curso) devuelve "(separado)", que no es protegida.
 * null si no se sabe, por ejemplo fuera de un repositorio.
 */
function ramaActual(cwd) {
  // La rama simulada vale para el directorio donde se cambio y sus subcarpetas (`cd src`, `-C src`).
  for (let dir = resolve(cwd); ; dir = dirname(dir)) {
    if (ramasSimuladas.has(dir)) return ramasSimuladas.get(dir);
    if (dirname(dir) === dir) break;
  }
  try {
    return git(cwd, ["symbolic-ref", "--short", "-q", "HEAD"]).toLowerCase() || null;
  } catch {
    try {
      git(cwd, ["rev-parse", "--git-dir"]);
      return "(separado)";
    } catch {
      return null;
    }
  }
}

function exigirRamaNoProtegida(cwd, accion, args = []) {
  // Terminar o abortar una operacion en curso no escribe trabajo nuevo.
  if (args.some((a) => ["--continue", "--abort", "--skip", "--quit"].includes(a))) return null;
  const rama = ramaActual(cwd);
  if (rama === null) {
    bloquear(`No se ha podido determinar la rama actual antes de ${accion}.`,
      "Comprueba que estas en una rama de trabajo (git switch -c <rama>) y repite.");
  }
  if (RAMAS_PROTEGIDAS.has(rama)) {
    bloquear(`HEAD esta en la rama protegida ${rama}: ${accion} la modificaria directamente.`,
      "Crea una rama de trabajo antes de seguir (git switch -c <rama>); la integracion la hace el autor.");
  }
  return rama;
}

/** Separa los flags globales de git del subcomando real, resuelve alias en linea y `-C`. */
function analizarGit(t, cwdBase) {
  if (verboDe(t[0]) !== "git") return null;
  const alias = {};
  let cwd = cwdBase;
  let i = 1;
  while (i < t.length) {
    const a = t[i];
    if (a === "-c") {
      const m = (t[i + 1] || "").match(/^alias\.([^=]+)=(.*)$/i);
      if (m) alias[m[1].toLowerCase()] = m[2].trim().split(/\s+/)[0].replace(/^!/, "");
      i += 2;
      continue;
    }
    if (a === "-C") { cwd = resolve(cwd, t[i + 1] || "."); i += 2; continue; }
    if (["--git-dir", "--work-tree", "--namespace", "--exec-path"].includes(a)) { i += 2; continue; }
    if (a.startsWith("-")) { i += 1; continue; }
    break;
  }
  let sub = (t[i] || "").toLowerCase();
  if (alias[sub]) sub = alias[sub].toLowerCase();
  return { sub, args: t.slice(i + 1), cwd };
}

/** Destino de un refspec: `+HEAD:refs/heads/main` -> `main`; `HEAD` y `@` -> rama actual. */
function destinoRefspec(token, cwd) {
  const sinMas = token.replace(/^\+/, "");
  const trozos = sinMas.split(":");
  const destino = (trozos.length > 1 ? trozos[trozos.length - 1] : sinMas)
    .replace(/^refs\/heads\//, "").toLowerCase();
  if (destino === "head" || destino === "@") return ramaActual(cwd);
  return destino;
}

function revisarPush(args, cwd) {
  const conValor = new Set(["-o", "--push-option", "--repo", "--receive-pack", "--exec"]);
  const flags = [];
  const libres = [];
  for (let i = 0; i < args.length; i += 1) {
    const a = args[i];
    if (a.startsWith("-")) { flags.push(a); if (conValor.has(a)) i += 1; continue; }
    libres.push(a);
  }
  if (flags.some((f) => /^--force(-with-lease|-if-includes)?(=|$)/.test(f) || /^-[a-zA-Z]*f/.test(f))) {
    bloquear("Un push forzado reescribe historial ya publicado.",
      "Si de verdad hace falta, ejecutalo tu mismo fuera de la sesion.");
  }
  if (flags.some((f) => f === "--mirror" || f === "--all")) {
    bloquear("--mirror y --all publican todas las ramas, incluida la protegida.",
      "Publica una rama concreta con su refspec.");
  }
  // El primer argumento libre es el remoto; el resto, refspecs. `--tags` solo publica etiquetas.
  if (libres.length <= 1 && flags.includes("--tags")) return;
  if (libres.length <= 1) {
    exigirRamaNoProtegida(cwd, "este push sin refspec");
    return;
  }
  for (const ref of libres.slice(1)) {
    const destino = destinoRefspec(ref, cwd);
    if (destino === null) {
      bloquear(`No se ha podido resolver el destino de ${ref}.`, "Repite el push con un refspec explicito a tu rama de trabajo.");
    }
    if (RAMAS_PROTEGIDAS.has(destino)) {
      bloquear(`Este push apunta a una rama protegida (${destino}).`,
        "El trabajo se entrega en una rama y se integra mediante PR revisada por el autor.");
    }
  }
}

/**
 * Normaliza gh, gh-axi y el script `github` del proyecto a { sub, accion, args }.
 * pnpm y yarn ya llegan sin envoltorio: `pnpm run github` -> `run github`.
 */
function analizarGh(t) {
  let resto;
  const verbo = verboDe(t[0]);
  const bajo = (k) => (t[k] || "").toLowerCase();
  if (verbo === "gh" || verbo === "gh-axi" || verbo === "github") {
    resto = t.slice(1);
  } else if (verbo === "npm" && bajo(1) === "run" && bajo(2) === "github") {
    resto = t.slice(3);
  } else if (verbo === "run" && bajo(1) === "github") {
    resto = t.slice(2);
  } else {
    return null;
  }
  const libres = [];
  for (let i = 0; i < resto.length; i += 1) {
    const a = resto[i];
    if (a === "--") continue;
    if (a === "-R" || a === "--repo") { i += 1; continue; }
    if (a.startsWith("--repo=")) continue;
    libres.push(a);
  }
  return { sub: (libres[0] || "").toLowerCase(), accion: (libres[1] || "").toLowerCase(), args: libres.slice(1) };
}

function revisarGh(gh) {
  if (gh.sub === "pr" && gh.accion === "merge") {
    bloquear("Fusionar una PR es una decision del autor.", "Entrega la PR con su evidencia y espera la revision.");
  }
  if (gh.sub === "pr" && gh.accion === "review" && gh.args.some((a) => /^(--approve|-a)(=.*)?$/.test(a))) {
    bloquear("Un agente no aprueba su propio trabajo.", "Entrega los hallazgos; la aprobacion es del autor.");
  }
  if (gh.sub === "auth" && gh.accion === "token") {
    bloquear("gh auth token imprime una credencial.", "Las credenciales no pasan por la conversacion.");
  }
  if (gh.sub === "api") {
    // Sin metodo ni campos, `gh api` hace GET: consultar fusiones y aprobaciones es legitimo.
    const texto = gh.args.join(" ");
    const escribe = gh.args.some((a, i) => /^(-X|--method)$/i.test(a) && !/^get$/i.test(gh.args[i + 1] || ""))
      || gh.args.some((a) => /^(-f|-F|--field|--raw-field|--input)$/.test(a));
    if (escribe && /\/pulls\/[^/\s]+\/merge|mergePullRequest|enablePullRequestAutoMerge|event=APPROVE/i.test(texto)) {
      bloquear("Esta llamada a la API fusiona o aprueba una PR.", "Fusionar y aprobar son decisiones del autor.");
    }
    if (/git\/refs/i.test(texto) && escribe) {
      bloquear("Esta llamada a la API modifica referencias de git directamente.", "Publica con git push en una rama de trabajo.");
    }
  }
}

/** Argumentos que un verbo de lectura trataria como archivos. */
function argumentosArchivo(verbo, args) {
  const bajos = args.map((a) => a.toLowerCase());
  const esPs = verbo === "select-string" || verbo === "sls";
  // Con el patron dado por flag, todos los posicionales son archivos.
  let patronVisto = !VERBOS_PATRON.has(verbo) || bajos.some((a) => FLAGS_PATRON.has(a));
  const salida = [];
  for (let i = 0; i < args.length; i += 1) {
    const a = args[i];
    if (FLAGS_PATRON.has(bajos[i])) { i += 1; continue; }
    // El codigo en linea se analiza aparte por sus literales.
    if (LENGUAJES.has(verbo) && FLAGS_CODIGO.has(a)) { i += 1; continue; }
    if (esPs && (bajos[i] === "-path" || bajos[i] === "-literalpath")) {
      if (args[i + 1]) salida.push(args[i + 1]);
      i += 1;
      continue;
    }
    if (a.startsWith("-") && !esSecreto(a)) continue;
    if (!patronVisto) { patronVisto = true; continue; }
    salida.push(a);
  }
  if (VERBOS_COPIA.has(verbo) && salida.length > 1) salida.pop();
  return salida;
}

function esArchivoDelMarco(ruta) {
  return /(^|[\\/])\.sdd([\\/]|$)/.test(ruta) || LOCALES_DEL_MARCO.has(nombreArchivo(ruta).toLowerCase());
}

const existeRef = (cwd, ref) => {
  try { git_(cwd, ["rev-parse", "--verify", "--quiet", ref]); return true; } catch { return false; }
};

/**
 * Rama que queda tras un `git switch` o `git checkout`: su nombre, null si no se puede
 * saber (bloquea) o undefined si el comando no cambia de rama (checkout de archivos).
 * Desconocida: `switch -`, crear una rama que ya existe (el switch fallaria) o un
 * comando encadenado con algo distinto de `&&` (el switch podria fallar y seguir).
 */
function destinoDelCambio(git, cwd) {
  const crear = git.args.findIndex((a) => /^(-c|-C|--create|--force-create|-b|-B)(=|$)/.test(a));
  if (git.sub === "checkout" && crear < 0 && !git.args.includes("--detach")) {
    if (git.args.includes("--")) return undefined;
    const x = git.args.find((a) => !a.startsWith("-"));
    if (!x) return undefined;
    if (x === "-") return null;
    // `checkout develop` sin rama local crea una desde origin/develop: tambien es un cambio de rama.
    const esRama = existeRef(cwd, `refs/heads/${x}`)
      || /\S/.test((() => { try { return git_(cwd, ["for-each-ref", "--format=%(refname)", `refs/remotes/*/${x}`]); } catch { return ""; } })());
    if (!esRama) return undefined;
    return encadenadoSeguro ? x.toLowerCase() : null;
  }
  if (!encadenadoSeguro) return null;
  if (crear >= 0) {
    const pegado = git.args[crear].split("=")[1];
    const nombre = pegado || git.args[crear + 1];
    if (!nombre || existeRef(cwd, `refs/heads/${nombre}`)) return null;
    return nombre.toLowerCase();
  }
  if (git.args.includes("--detach")) return "(separado)";
  const destino = git.args.find((a) => !a.startsWith("-"));
  return destino && destino !== "-" ? destino.toLowerCase() : null;
}

/** Valores de opciones de texto: separadas (`-m x`), combinadas (`-am x`) o con `=` (`--body=x`). */
function valoresDe(args, cortas, largas) {
  const valores = [];
  for (let i = 0; i < args.length; i += 1) {
    const a = args[i];
    const larga = largas.find((l) => a === l || a.startsWith(`${l}=`));
    if (larga) {
      if (a === larga) { valores.push(args[i + 1] ?? ""); i += 1; } else valores.push(a.slice(larga.length + 1));
      continue;
    }
    const corta = a.match(/^-([a-zA-Z]+)$/);
    if (corta && cortas.includes(corta[1].slice(-1))) { valores.push(args[i + 1] ?? ""); i += 1; }
  }
  return valores;
}

function revisarCommitAporte(args, cwd) {
  let preparados = "";
  try { preparados = git_(cwd, ["diff", "--cached", "--name-only"]); } catch { /* sin repo: lo decide otra regla */ }
  const local = preparados.split(/\r?\n/).find((f) => f && esArchivoDelMarco(f));
  if (local) bloquear(`${local} esta preparado para commit y es un archivo del marco.`, "Sacalo del stage: git restore --staged <ruta>.");
  const textos = [
    ...valoresDe(args, ["m"], ["--message", "--trailer"]),
    ...valoresDe(args, ["F"], ["--file"]).filter((f) => f !== "-").map((f) => leerSiExiste(resolve(cwd, f))),
  ];
  if (textos.some((t) => RASTRO.test(t))) {
    bloquear("El mensaje del commit menciona el marco o sus archivos.", "En un repo ajeno el commit solo describe el cambio.");
  }
}

function revisarTextoPr(args, cwd) {
  const textos = [
    ...valoresDe(args, ["t", "b"], ["--title", "--body"]),
    ...valoresDe(args, ["F"], ["--body-file"]).filter((f) => f !== "-").map((f) => leerSiExiste(resolve(cwd, f))),
  ];
  if (textos.some((t) => RASTRO.test(t))) {
    bloquear("El titulo o el cuerpo de la PR mencionan el marco o sus archivos.", "Describe solo el cambio, con la plantilla del repo.");
  }
}

function leerSiExiste(ruta) {
  try { return readFileSync(ruta, "utf8"); } catch { return ""; }
}

function revisarComando(t, cwdBase) {
  if (t.length === 0) return;
  const verbo = verboDe(t[0]);
  const git = analizarGit(t, cwdBase);
  const cwd = git ? git.cwd : cwdBase;

  // Un secreto como verbo es una redireccion de entrada: `done < .env`.
  if (esSecreto(t[0])) {
    bloquear(`Este comando redirige un archivo de secretos (${nombreArchivo(t[0])}).`,
      "Las credenciales se cargan en tiempo de ejecucion; nunca pasan por la conversacion.");
  }

  // R1. Publicacion en rama protegida y reescritura de historial.
  if (git && git.sub === "push") revisarPush(git.args, cwd);

  // R2. Escribir en la rama por defecto, fusionar y aprobar: lo decide el autor.
  if (git && GIT_ESCRIBE_RAMA.has(git.sub)) exigirRamaNoProtegida(cwd, `git ${git.sub}`, git.args);
  const gh = analizarGh(t);
  if (gh) revisarGh(gh);

  // R3. Secretos: ni se imprimen, ni se copian, ni se envian, ni se preparan para commit.
  if (VERBOS_LECTURA.has(verbo)) {
    const secreto = argumentosArchivo(verbo, t.slice(1)).find(esSecreto);
    if (secreto) {
      bloquear(`Este comando leeria, copiaria o enviaria un archivo de secretos (${nombreArchivo(secreto)}).`,
        "Las credenciales se cargan en tiempo de ejecucion; nunca pasan por la conversacion.");
    }
  }
  const conParche = git && git.sub === "log" && git.args.some((a) => ["-p", "-u", "--patch"].includes(a));
  if (git && (["add", "show", "cat-file", "diff"].includes(git.sub) || conParche)) {
    const secreto = git.args.filter((a) => !a.startsWith("-") || esSecreto(a)).find(esSecreto);
    if (secreto) {
      bloquear(`git ${git.sub} sobre ${nombreArchivo(secreto)} expondria un secreto.`,
        "Comprueba .gitignore y trabaja solo con rutas explicitas del cambio.");
    }
  }

  if (git && git.sub === "add") {
    const local = git.args.find(esArchivoDelMarco);
    if (local) {
      bloquear(`${local} es un archivo local del marco y no debe entrar en un commit.`,
        "Prepara solo los archivos del cambio.");
    }
    // Los archivos del marco estan ignorados: solo `-f` con rutas amplias los prepararia.
    const forzado = git.args.some((a) => a === "-f" || a === "--force" || /^-[a-zA-Z]*f[a-zA-Z]*$/.test(a));
    if (aporteActivo && forzado && git.args.some((a) => [".", "-A", "--all", "*", ":/", ":"].includes(a))) {
      bloquear("git add -f con rutas amplias prepararia los archivos locales del marco.", "Prepara con -f solo rutas explicitas.");
    }
  }

  // Escritura directa de la referencia de una rama protegida.
  if (git && git.sub === "branch" && git.args.some((a) => /^(-f|--force|-m|-M|-c|-C)$/.test(a))) {
    const objetivo = git.args.find((a) => RAMAS_PROTEGIDAS.has(a.toLowerCase()));
    if (objetivo) bloquear(`git branch sobre la rama protegida ${objetivo} la reescribe.`, "Trabaja en una rama propia.");
  }
  if (git && git.sub === "update-ref" && git.args.some((a) => RAMAS_PROTEGIDAS.has(a.replace(/^refs\/heads\//, "").toLowerCase()))) {
    bloquear("git update-ref sobre una rama protegida la reescribe.", "Trabaja en una rama propia.");
  }

  // Modo aporte: nada del marco llega a un commit ni a una PR del repo ajeno.
  if (aporteActivo && git && git.sub === "commit") revisarCommitAporte(git.args, cwd);
  if (aporteActivo && gh && gh.sub === "pr" && ["create", "edit"].includes(gh.accion)) revisarTextoPr(gh.args, cwd);

  // Cambio de rama dentro del mismo comando: lo que venga despues se evalua en la rama nueva.
  // Si el resultado no es seguro, la rama pasa a desconocida (null) y bloquea.
  if (git && (git.sub === "switch" || git.sub === "checkout")) {
    const destino = destinoDelCambio(git, cwd);
    if (destino !== undefined) ramasSimuladas.set(resolve(cwd), destino);
  }

  // R4. Borrado de lo que git no puede devolver.
  if (git && git.sub === "clean" && git.args.some((a) => /^-[a-zA-Z]*[xX]/.test(a))) {
    bloquear("git clean -x borra tambien lo ignorado: .env, caches y artefactos locales.",
      "Limpia rutas concretas o usa git clean sin -x.");
  }
}

/**
 * Evalua una entrada de PreToolUse y devuelve lo que el hook emite: `{ codigo, stdout, stderr }`.
 * Exportada para que la suite evalue en el mismo proceso: en Windows, con antivirus, cada
 * arranque de Node cuesta cientos de milisegundos (cambio 007). Cada llamada parte de cero.
 */
export function evaluar(entrada, dirProyecto = process.env.CLAUDE_PROJECT_DIR) {
  RAMAS_PROTEGIDAS.clear();
  RAMAS_PROTEGIDAS.add("main");
  RAMAS_PROTEGIDAS.add("master");
  ramasSimuladas.clear();
  encadenadoSeguro = true;
  aporteActivo = false;
  try {
    const comando = entrada?.tool_input?.command;
    if (typeof comando === "string" && comando.trim()) {
      // `cd` cambia el directorio de lo que viene despues: la rama se evalua alli.
      let cwd = entrada.cwd || process.cwd();
      const proyecto = dirProyecto || cwd;
      let config = null;
      try {
        config = configProyecto(proyecto);
      } catch {
        // Config ilegible: no se bloquea todo (habria que poder salir), pero se protege
        // la rama que se pueda leer del texto crudo ademas de main y master.
        const rutaAporte = join(dirAporte(proyecto) || "", "config.json");
        // Un config del aporte vacio o bloqueado sigue siendo un aporte: se falla cerrado.
        const enAporte = existsSync(rutaAporte);
        const crudo = enAporte ? leerSiExiste(rutaAporte) : leerSiExiste(join(proyecto, ".sdd-lite.json"));
        config = { ramaPorDefecto: (crudo.match(/"ramaPorDefecto"\s*:\s*"([^"]+)"/) || [])[1], aporte: enAporte };
      }
      aporteActivo = config?.aporte === true;
      if (config?.ramaPorDefecto) RAMAS_PROTEGIDAS.add(String(config.ramaPorDefecto).toLowerCase());
      const remota = ramaRemotaPorDefecto(proyecto);
      if (remota) RAMAS_PROTEGIDAS.add(remota.toLowerCase());
      encadenadoSeguro = soloEncadenadoConY(comando);
      // Mensajes de commit y cuerpos de PR en heredoc o here-string: el analisis los descarta como texto.
      if (aporteActivo && /\bcommit\b|\bpr\b/.test(comando) && cuerposDeTexto(comando).some((c) => RASTRO.test(c))) {
        bloquear("El texto en heredoc de este commit o PR menciona el marco o sus archivos.", "En un repo ajeno solo se describe el cambio.");
      }
      for (const t of comandosAnalizables(comando)) {
        if (VERBOS_CD.has(verboDe(t[0]))) {
          const destino = t.slice(1).find((a) => !a.startsWith("-"));
          if (destino) cwd = destino.startsWith("~") ? resolve(homedir(), destino.slice(1).replace(/^[\\/]/, "")) : resolve(cwd, destino);
          continue;
        }
        revisarComando(t, cwd);
      }
    }
    return { codigo: 0, stdout: "", stderr: "" };
  } catch (error) {
    return error instanceof Bloqueo ? respuestaBloqueo(error.message) : respuestaFallo(error);
  }
}
