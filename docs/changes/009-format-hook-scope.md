# 009 - Format hook: project files only, never invalid JSON

Status: ready locally

## Contract
Goal and reason:
The `PostToolUse` hook `.claude/hooks/format.mjs` runs `npm run format`, which is `ruff format`, on every file the agent edits.
On 2026-10-13 an edit to `C:/Python Projects/relevo-contexto/.claude-plugin/plugin.json`, outside this project, was reformatted by ruff as if it were Python: trailing commas, 4-space indent and inline objects, so `JSON.parse` failed at line 13 column 9.
The same hook reported "El formateador fallo" on `relevo-contexto/tests/relevo.test.ts`.
Root cause: ruff formats any explicitly passed path as Python, whatever its extension, and the hook never checked that the file belongs to the project.
Out of scope:
Which file types the project `format` script accepts; ruff still fails, noisily but harmlessly, on non-Python files inside the project (`.mjs`, `.ts`, `.md`).
Constraints / decisions that need the owner:
None.
Current authorization and limits:
Owner request on 2026-10-13; delivery is the work branch and a PR to `main`.
`relevo-contexto` is not touched; the reproduction uses a scratch copy outside the repo.

| ID | Observable criterion | Evidence when verifying |
|---|---|---|
| C1 | A file outside `$CLAUDE_PROJECT_DIR` is not formatted and the hook exits 0 | `test-hooks.mjs` cases "archivo fuera del proyecto"; scratch copy of `plugin.json` byte-identical after the hook |
| C2 | After formatting a `.json` file, the result is kept only if it parses to the same value; otherwise the edited content is restored and the agent is told (exit 2) | `test-hooks.mjs` cases "JSON roto" and "JSON valido y equivalente"; real ruff on an in-project copy of `plugin.json` |
| C3 | `npm run check` exits 0 | Command output |

## Plan
- [x] Reproduce on a scratch copy: the `origin/main` hook breaks `plugin.json` (same error, line 13 column 9) and fails on the `.ts` file.
- [x] Add failing hook tests, then fix `format.mjs`.
- [x] Run `npm run check`.

## Decisions
The JSON guard compares parsed values, not only validity, so a formatter that rewrites values is also undone.
If the edited `.json` was itself invalid (for example JSONC), the formatter output is discarded too: the hook never writes JSON that the edit did not.
Two existing format tests passed a path from another temporary repo; they now use a path inside their own repo, since that path is now ignored.

## Adversarial review
| Round | Backend | Range | Lenses | Findings | Status |
|---|---|---|---|---|---|
| 1 | local | this change | correctness, Windows paths, regressions | `$PWD` in Git Bash is a POSIX path and made a manual check pass for the wrong reason; redone with `pwd -W`. No code findings | closed |

## Delivery
C1: new hook tests pass; with the fixed hook, the scratch `plugin.json` and `relevo.test.ts` give exit 0 and `plugin.json` stays byte-identical, while the `origin/main` hook on the same copy produces the reported `SyntaxError` at line 13 column 9.
C2: new hook tests pass; real ruff on an in-project copy of `plugin.json` now prints the restore notice, exits 2 and leaves the file byte-identical.
C3: `npm run check` on this branch exited 0 on 2026-10-13 (hook suite, ruff, pytest and the replay eval with 0 failures), after `npm ci` in the fresh worktree.
