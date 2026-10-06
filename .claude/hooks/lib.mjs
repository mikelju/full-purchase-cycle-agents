// Utilidades compartidas por los hooks de SDD Lite.
// Todo aqui es determinista: sin LLM, sin red, sin estado.
//
// El analisis es lexico y nunca sera completo frente a ofuscacion deliberada.
// Su objetivo es cubrir las formas que un agente produce de forma natural:
// encadenar, anidar en otro interprete, sustituir comandos y usar rutas de Windows.

import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

/** Lee el JSON que Claude Code entrega por stdin. */
export async function leerEntrada() {
  const trozos = [];
  for await (const t of process.stdin) trozos.push(t);
  const bruto = Buffer.concat(trozos).toString("utf8").trim();
  return bruto ? JSON.parse(bruto) : {};
}

/**
 * Directorio de estado del modo aporte: `<git dir>/sdd-lite/`.
 * Vive dentro del directorio de git, donde un checkout nunca escribe: una rama del
 * repo ajeno no puede sustituirlo, como si podria con un archivo ignorado del arbol.
 * Resuelve `.git` como directorio o como archivo `gitdir:` (worktrees) sin llamar a git.
 */
export function dirAporte(raiz) {
  const punto = join(raiz, ".git");
  if (!existsSync(punto)) return null;
  let gitdir = punto;
  if (statSync(punto).isFile()) {
    const m = readFileSync(punto, "utf8").match(/^gitdir:\s*(.+?)\s*$/m);
    if (!m) return null;
    gitdir = resolve(raiz, m[1]);
  }
  return join(gitdir, "sdd-lite");
}

/**
 * Configuracion del modo aporte, o null si el proyecto no esta en modo aporte.
 * Con ella los hooks usan los comandos y la rama por defecto del repo ajeno,
 * nunca los scripts de su package.json. Lanza si el JSON es invalido.
 */
export function configAporte(raiz) {
  const dir = dirAporte(raiz);
  const ruta = dir && join(dir, "config.json");
  if (!ruta || !existsSync(ruta)) return null;
  return JSON.parse(readFileSync(ruta, "utf8"));
}

/**
 * Configuracion efectiva del proyecto, o null: la del modo aporte si hay uno en curso
 * (`aporte: true`) o, si no, `.sdd-lite.json` versionado en la raiz (`aporte: false`).
 * En modo aporte nunca se lee `.sdd-lite.json`: seria del repo ajeno, no del usuario.
 * Lanza si el JSON es invalido.
 */
export function configProyecto(raiz) {
  const aporte = configAporte(raiz);
  if (aporte) return { ...aporte, aporte: true };
  const ruta = join(raiz, ".sdd-lite.json");
  if (!existsSync(ruta)) return null;
  // Sin BOM: PowerShell 5.1 lo anade al guardar en UTF-8.
  return { ...leerSddLite(readFileSync(ruta, "utf8")), aporte: false };
}

/** Parsea `.sdd-lite.json`: sin BOM (PowerShell 5.1 lo anade) y con tipos validados. */
function leerSddLite(texto) {
  const config = JSON.parse(texto.replace(/^\uFEFF/, ""));
  // Un tipo inesperado no debe desactivar en silencio la puerta o el formateo: se trata como ilegible.
  for (const clave of ["ramaPorDefecto", "comprobar", "formatear"]) {
    if (config[clave] != null && typeof config[clave] !== "string") throw new Error(`${clave} debe ser texto`);
  }
  return config;
}

/**
 * Configuracion con `comprobar` y `formatear` fijados a los de la rama por defecto.
 * Los hooks ejecutan esos comandos sin preguntar: una rama o PR ajena que los cambie en su
 * `.sdd-lite.json` no debe poder ejecutar nada. Si difieren, se usan los de la rama por defecto
 * (ninguno, si alli aun no hay archivo) y se devuelve un aviso. El modo aporte no lo necesita:
 * su configuracion vive en el directorio de git, donde un checkout no escribe.
 */
export function conComandosFiables(raiz, config) {
  if (!config || config.aporte) return { config, aviso: null };
  const rama = ramaRemotaPorDefecto(raiz) || config.ramaPorDefecto || "main";
  let base = {};
  for (const ref of [rama, `origin/${rama}`]) {
    const r = spawnSync("git", ["show", `${ref}:.sdd-lite.json`], { cwd: raiz, encoding: "utf8", timeout: 10000 });
    if (r.status !== 0) continue;
    try { base = leerSddLite(r.stdout); } catch { base = {}; }
    break;
  }
  const distintos = ["comprobar", "formatear"].filter((c) => (config[c] ?? null) !== (base[c] ?? null));
  if (!distintos.length) return { config, aviso: null };
  const fiable = { ...config };
  for (const c of distintos) fiable[c] = base[c] ?? null;
  return {
    config: fiable,
    aviso: `[SDD Lite] ${distintos.join(" y ")} de .sdd-lite.json ${distintos.length > 1 ? "difieren" : "difiere"}`
      + ` de la rama ${rama}: se usa lo de ${rama}.`
      + " Un cambio de estos comandos se aplica al fusionarse por PR.\n",
  };
}

/**
 * Rama que `origin` anuncia como por defecto (`refs/remotes/origin/HEAD`), o null.
 * Protege la rama real aunque `.sdd-lite.json` aun no este en ella (antes de fusionar la adopcion).
 */
export function ramaRemotaPorDefecto(raiz) {
  const dir = dirAporte(raiz);
  if (!dir) return null;
  let gitdir = dirname(dir);
  const comun = join(gitdir, "commondir");
  if (existsSync(comun)) gitdir = resolve(gitdir, readFileSync(comun, "utf8").trim());
  const ref = join(gitdir, "refs", "remotes", "origin", "HEAD");
  if (!existsSync(ref)) return null;
  const m = readFileSync(ref, "utf8").match(/^ref:\s*refs\/remotes\/origin\/(.+?)\s*$/m);
  return m ? m[1] : null;
}

/** Menciones del marco que no deben llegar a un commit ni a una PR de un repo ajeno. */
export const RASTRO = /(^|[\s/"'`(])\.sdd[\\/]|CLAUDE\.local\.md|settings\.local\.json|sdd-lite|SDD Lite/i;

/** Nombre de archivo de un token, con separadores POSIX o Windows. */
export function nombreArchivo(token) {
  const limpio = String(token).replace(/^["']|["']$/g, "");
  const partes = limpio.split(/[\\/]/);
  return partes[partes.length - 1];
}

/** Verbo normalizado: `C:/Git/cmd/git.exe` -> `git`. */
export function verboDe(token) {
  return nombreArchivo(token || "").toLowerCase().replace(/\.exe$/, "");
}

/** Tokeniza respetando comillas y descarta el envoltorio de comillas. */
export function tokens(segmento) {
  // Una palabra puede mezclar partes con y sin comillas, como en el shell: `--body="a b"`.
  const crudos = segmento.match(/(?:[^\s"']+|"(?:\\.|[^"\\])*"|'[^']*')+|\S+/g) || [];
  return crudos.map((t) => t.replace(/"((?:\\.|[^"\\])*)"|'([^']*)'/g, (_, doble, simple) => doble ?? simple).replace(/^["']|["']$/g, ""));
}

/** Envoltorios y palabras de control que no son el comando real. */
const ENVOLTORIOS = new Set([
  "sudo", "env", "command", "npx", "pnpm", "yarn", "bunx", "time", "nohup", "exec",
  "if", "then", "else", "elif", "do", "while", "until", "!", "fi", "done",
]);

/** Devuelve los tokens saltando envoltorios y asignaciones iniciales. */
export function tokensUtiles(segmento) {
  const t = tokens(segmento);
  let i = 0;
  while (i < t.length && (ENVOLTORIOS.has(t[i].toLowerCase()) || /^[A-Za-z_][A-Za-z0-9_]*=/.test(t[i]))) i += 1;
  return t.slice(i);
}

/**
 * Trocea por operadores fuera de comillas: ; & | ( ) { } ` y salto de linea.
 * Respeta `\"` y `\'` escapadas. Las redirecciones se tratan como separador de
 * token: `cat<.env` -> `cat .env`.
 */
export function trocear(texto) {
  const segs = [];
  let actual = "";
  let comilla = null;
  const s = String(texto);
  for (let i = 0; i < s.length; i += 1) {
    const c = s[i];
    if (c === "\\" && comilla !== "'" && i + 1 < s.length) { actual += c + s[i + 1]; i += 1; continue; }
    if (comilla) {
      actual += c;
      if (c === comilla) comilla = null;
      continue;
    }
    if (c === '"' || c === "'") { comilla = c; actual += c; continue; }
    if (";&|(){}`\n\r".includes(c)) { segs.push(actual); actual = ""; continue; }
    if (c === "<" || c === ">") { actual += " "; continue; }
    actual += c;
  }
  segs.push(actual);
  return segs.map((s2) => s2.trim()).filter(Boolean);
}

/**
 * Contenido de `$(...)` y `` `...` `` que el shell ejecutaria: fuera de comillas
 * simples, tambien dentro de comillas dobles.
 */
function sustituciones(texto) {
  const salida = [];
  const s = String(texto);
  let simple = false;
  for (let i = 0; i < s.length; i += 1) {
    const c = s[i];
    if (c === "\\" && !simple) { i += 1; continue; }
    if (c === "'") { simple = !simple; continue; }
    if (simple) continue;
    if (c === "$" && s[i + 1] === "(") {
      let nivel = 1;
      let j = i + 2;
      while (j < s.length && nivel > 0) { if (s[j] === "(") nivel += 1; if (s[j] === ")") nivel -= 1; j += 1; }
      salida.push(s.slice(i + 2, j - 1));
      i = j - 1;
    } else if (c === "`") {
      const fin = s.indexOf("`", i + 1);
      if (fin < 0) break;
      salida.push(s.slice(i + 1, fin));
      i = fin;
    }
  }
  return salida;
}

export const SHELLS = new Set(["bash", "sh", "zsh", "dash", "ksh", "pwsh", "powershell", "cmd"]);
export const LENGUAJES = new Set(["node", "deno", "bun", "python", "python3", "py", "ruby", "perl", "php"]);
export const FLAGS_CODIGO = new Set(["-e", "-c", "-p", "--eval", "--print", "-r"]);

/** Texto que otro interprete ejecutara como comandos de shell, o null. */
function comandoInterior(verbo, t) {
  const resto = (i) => t.slice(i).join(" ");
  if (["bash", "sh", "zsh", "dash", "ksh"].includes(verbo)) {
    const i = t.findIndex((a, k) => k > 0 && /^-[a-z]*c$/.test(a));
    return i > 0 ? (t[i + 1] || "") : null;
  }
  if (verbo === "pwsh" || verbo === "powershell") {
    const i = t.findIndex((a, k) => k > 0 && /^-(c|command)$/i.test(a));
    return i > 0 ? resto(i + 1) : null;
  }
  if (verbo === "cmd") {
    const i = t.findIndex((a, k) => k > 0 && /^\/[ck]$/i.test(a));
    return i > 0 ? resto(i + 1) : null;
  }
  if (["eval", "invoke-expression", "iex"].includes(verbo)) return resto(1);
  return null;
}

/** Codigo de un lenguaje pasado en linea (`node -e`, `python -c`), o null. */
function codigoEnLinea(verbo, t) {
  if (!LENGUAJES.has(verbo)) return null;
  const i = t.findIndex((a, k) => k > 0 && FLAGS_CODIGO.has(a));
  return i > 0 ? (t[i + 1] || "") : null;
}

/**
 * Palabras de los literales de cadena de un fragmento de codigo.
 * Solo los literales pueden nombrar un archivo; `o.key` es una propiedad.
 */
function literalesDeCodigo(codigo) {
  const palabras = [];
  for (const m of String(codigo).matchAll(/"((?:\\.|[^"\\])*)"|'((?:\\.|[^'\\])*)'/g)) {
    palabras.push(...(m[1] ?? m[2]).split(/[\s,;]+/).filter(Boolean));
  }
  return palabras;
}

/** Tipo de un cuerpo de heredoc segun quien lo recibe: shell, codigo o texto. */
function tipoCuerpo(antes, despues) {
  const previos = trocear(antes);
  const verbo = verboDe(tokensUtiles(previos[previos.length - 1] || "")[0]);
  const tuberia = String(despues).match(/\|\s*([^\s|;&]+)/);
  const destino = tuberia ? verboDe(tuberia[1]) : "";
  if (SHELLS.has(verbo) || SHELLS.has(destino)) return "shell";
  if (LENGUAJES.has(verbo) || LENGUAJES.has(destino)) return "codigo";
  return "texto";
}

/**
 * Separa los cuerpos de heredoc y here-strings de PowerShell del resto del comando.
 * Un cuerpo de texto (`cat > nota.md <<'EOF'`, un mensaje de commit) no dispara reglas;
 * uno que recibe un shell o un lenguaje se analiza. Lo que sigue al marcador en la
 * misma linea se conserva.
 */
function separarCuerpos(comando) {
  const fuera = [];
  const cuerpos = [];
  let abierto = null;
  for (const linea of String(comando).split(/\r?\n/)) {
    if (abierto) {
      if (linea.trim() === abierto.cierre || (abierto.ps && linea.startsWith(abierto.cierre))) {
        cuerpos.push({ tipo: abierto.tipo, texto: abierto.cuerpo.join("\n") });
        abierto = null;
      } else {
        abierto.cuerpo.push(linea);
      }
      continue;
    }
    const heredoc = linea.match(/(?<!<)<<(?!<)-?\s*['"]?([A-Za-z_][A-Za-z0-9_]*)['"]?/);
    if (heredoc) {
      const antes = linea.slice(0, heredoc.index);
      const despues = linea.slice(heredoc.index + heredoc[0].length);
      fuera.push(`${antes} ${despues}`);
      abierto = { cierre: heredoc[1], tipo: tipoCuerpo(antes, despues), cuerpo: [], ps: false };
    } else {
      fuera.push(linea);
      if (/@['"]\s*$/.test(linea)) abierto = { cierre: `${linea.trim().slice(-1)}@`, tipo: "texto", cuerpo: [], ps: true };
    }
  }
  return { fuera: fuera.join("\n"), cuerpos };
}

/**
 * true si todos los comandos del texto se encadenan con `&&`, de modo que uno solo se
 * ejecuta si el anterior tuvo exito. Ignora cuerpos de heredoc, texto entre comillas y
 * redirecciones como `2>&1`; `;`, `||`, `&` suelto o un salto de linea lo hacen falso.
 */
export function soloEncadenadoConY(comando) {
  const { fuera } = separarCuerpos(comando);
  const limpio = fuera
    .replace(/"(?:\\.|[^"\\])*"|'[^']*'/g, "\"\"")
    .replace(/\d*>&\d*|&>|>&/g, " ")
    .replace(/&&/g, " ");
  return !/[;&\n]|\|\|/.test(limpio.trim());
}

/** Textos de heredocs y here-strings que no ejecuta nadie: mensajes de commit, cuerpos de PR. */
export function cuerposDeTexto(comando) {
  return separarCuerpos(comando).cuerpos.filter((c) => c.tipo === "texto").map((c) => c.texto);
}

/**
 * Lista de comandos simples (arrays de tokens) que ejecutaria este texto, en orden
 * y con los anidados antes que su contenedor. El marcador `__codigo__` agrupa las
 * palabras de los literales de un fragmento de codigo para las reglas de secretos.
 */
export function comandosAnalizables(comando, profundidad = 0) {
  if (profundidad > 5) throw new Error("anidamiento de comandos demasiado profundo");
  const salida = [];
  const { fuera, cuerpos } = separarCuerpos(comando);
  for (const c of cuerpos) {
    if (c.tipo === "shell") salida.push(...comandosAnalizables(c.texto, profundidad + 1));
    if (c.tipo === "codigo") salida.push(["__codigo__", ...literalesDeCodigo(c.texto)]);
  }
  for (const interior of sustituciones(fuera)) salida.push(...comandosAnalizables(interior, profundidad + 1));

  for (const seg of trocear(fuera)) {
    const t = tokensUtiles(seg);
    if (t.length === 0) continue;
    const verbo = verboDe(t[0]);
    const interior = comandoInterior(verbo, t);
    if (interior !== null) {
      salida.push(...comandosAnalizables(interior, profundidad + 1));
      // PowerShell tambien lee archivos con APIs de .NET: `[IO.File]::ReadAllText('.env')`.
      if (verbo === "pwsh" || verbo === "powershell") salida.push(["__codigo__", ...literalesDeCodigo(interior)]);
    }
    const codigo = codigoEnLinea(verbo, t);
    if (codigo !== null) salida.push(["__codigo__", ...literalesDeCodigo(codigo)]);
    salida.push(t);
  }
  return salida;
}
