---
name: github
description: Consultar y gestionar issues, pull requests y comprobaciones de GitHub mediante gh-axi. Usar para operaciones GitHub; no sustituye Git local ni otorga permiso para publicar.
---
# GitHub mediante AXI

- Con `package.json` y gh-axi instalado, usa `npm run github -- --help` y la ayuda del subcomando; gh-axi usa el GitHub CLI autenticado.
  En repos que no son Node (adoptados o ajenos), usa `gh` directamente con salidas acotadas (`--json` con los campos justos, `--limit`).
- Confirma repositorio y host desde el remoto; usa destino explícito cuando el contexto sea ambiguo.
- Consulta issues, PR y checks con salidas acotadas; conserva `gh` como fallback si falta una operación.
- La PR de entrega de la rama de trabajo a `main` en `origin` forma parte del flujo (`../sdd-delivery/SKILL.md`).
  Comentar, publicar en otro destino o cualquier otra escritura requiere autorización vigente; fusionar y aprobar, nunca.
- Para textos largos usa un archivo UTF-8 y `--body-file`, sin interpolarlos en comandos.
- No instales hooks globales; no dupliques los PR o comentarios que ya gestione No Mistakes.
