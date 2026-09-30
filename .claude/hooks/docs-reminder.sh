#!/usr/bin/env bash
# Claude Code Stop hook: before an agent finishes, remind it once about docs its code changes may have made
# wrong. Registered in .claude/settings.json, so every Claude Code session in this repository runs it.
#
# What counts as changed: everything on this branch since it left origin/main, plus uncommitted and
# untracked files. For each changed code file (not Markdown, tests, lockfiles, docs/, legacy/,
# experiments/), its describing docs are the README.md / CLAUDE.md of the nearest folder above it that has
# either, plus any docs page that cites its path. The same rules as .github/scripts/docs_guides.py, in
# bash and git only, because a hook must not depend on a Python being on PATH.
#
# If none of a folder's describing docs changed, the hook blocks the stop ONCE with the list. The agent
# then updates the docs, or, if they are still right, records that it looked:
#     bash .claude/hooks/docs-reminder.sh --reviewed <folder>
# which stores the folder with a hash of its current changes in .git/docs-reviewed; a later change to that
# folder's code makes the hash differ and the reminder return. `stop_hook_active` (set while Claude is
# already continuing because of a Stop hook) makes the second stop in a row pass, so it can never loop.
#
# Work is per FOLDER, not per file, and uses shell string handling rather than dirname: on Windows every
# extra process costs tens of milliseconds, and a stop should not wait on a hook.
set -u

paths=$(git rev-parse --show-toplevel --absolute-git-dir 2>/dev/null) || exit 0
root=${paths%%$'
'*}
ack_file="${paths#*$'
'}/docs-reviewed"
cd "$root" || exit 0
base=$(git merge-base HEAD origin/main 2>/dev/null || git rev-parse HEAD 2>/dev/null) || exit 0
NL='
'

is_code() {
  case "$1" in
    *.md | docs/* | legacy/* | experiments/* | frontend/public/* | .github/pr-media/*) return 1 ;;
    *uv.lock | *pnpm-lock.yaml | *package-lock.json) return 1 ;;
    tests/* | */tests/* | */test/* | e2e/* | */e2e/* | test_*.py | */test_*.py) return 1 ;;
    *.test.ts | *.test.tsx | *.spec.ts | *.spec.tsx | *.test.js | *.test.jsx) return 1 ;;
  esac
  return 0
}

# Sets GUIDE_DIR to the folder whose README.md / CLAUDE.md are the nearest guides of $1 ("." for the
# repository root). A variable, not output: capturing output would fork a subshell per file.
guide_folder() {
  GUIDE_DIR=$1
  while :; do
    case "$GUIDE_DIR" in */*) GUIDE_DIR=${GUIDE_DIR%/*} ;; *) GUIDE_DIR=. ;; esac
    if [ -f "$GUIDE_DIR/README.md" ] || [ -f "$GUIDE_DIR/CLAUDE.md" ] || [ "$GUIDE_DIR" = . ]; then
      return
    fi
  done
}

# A hash of everything changed under a folder: the diff against the base, plus each untracked file's
# content (one hash-object for all of them).
folder_hash() {
  {
    git diff "$base" -- "$1" 2>/dev/null
    git ls-files --others --exclude-standard -- "$1" | git hash-object --stdin-paths 2>/dev/null
  } | git hash-object --stdin
}

if [ "${1:-}" = "--reviewed" ]; then
  folder=${2:?usage: docs-reminder.sh --reviewed <folder>}
  folder=${folder%/}
  { grep -v "^$folder	" "$ack_file" 2>/dev/null || true; } > "$ack_file.tmp"
  printf '%s\t%s\n' "$folder" "$(folder_hash "$folder")" >> "$ack_file.tmp"
  mv "$ack_file.tmp" "$ack_file"
  echo "Recorded: the docs for $folder were reviewed against its current changes."
  exit 0
fi

input=$(cat)
case "$input" in *'"stop_hook_active"'*true*) exit 0 ;; esac

changed="$NL$( { git diff --name-only "$base" 2>/dev/null; git ls-files --others --exclude-standard; } | sort -u)$NL"

# Group the changed code files by their guide folder: "folder<TAB>file" lines, sorted by folder.
pairs=""
while IFS= read -r file; do
  [ -n "$file" ] && is_code "$file" || continue
  guide_folder "$file"
  pairs="$pairs$GUIDE_DIR	$file$NL"
done <<< "$changed"
[ -n "$pairs" ] || exit 0
pairs=$(printf '%s' "$pairs" | sort)

acks=""
[ -f "$ack_file" ] && acks=$(<"$ack_file")
report=""
current=""
files=""
flush() {
  [ -n "$current" ] || return 0
  local prefix guides="" doc pages args=()
  if [ "$current" = . ]; then prefix=""; else prefix="$current/"; fi
  for doc in "${prefix}README.md" "${prefix}CLAUDE.md"; do
    [ -f "$doc" ] && guides="$guides $doc"
  done
  # Pages under docs/ that cite any of the folder's changed files by path.
  while IFS= read -r f; do [ -n "$f" ] && args+=(-e "$f"); done <<< "$files"
  pages=$(git grep -l -F "${args[@]}" -- docs ':!docs/plans' ':!docs/backlog.md' 2>/dev/null | tr '\n' ' ')
  for doc in $guides $pages; do
    case "$changed" in *"$NL$doc$NL"*) return 0 ;; esac  # a describing doc was touched: fine
  done
  [ -n "$guides$pages" ] || return 0
  case "$NL$acks$NL" in *"$NL$current	$(folder_hash "$current")$NL"*) return 0 ;; esac
  report="$report\\n- $current: re-read$guides${pages:+ and ${pages% }}"
}
while IFS='	' read -r folder file; do
  if [ "$folder" != "$current" ]; then
    flush
    current=$folder
    files=""
  fi
  files="$files$file$NL"
done <<< "$pairs"
flush

[ -n "$report" ] || exit 0

reason="Code changed without its docs being touched. Before finishing, re-read these and update any that the change made wrong (what each file does in the README, rules and traps in the CLAUDE.md):$report\\nIf a folder's docs are still right, record that and finish: bash .claude/hooks/docs-reminder.sh --reviewed <folder>"
# JSON string: escape double quotes; the report's line breaks are already the two characters \n.
printf '{"decision": "block", "reason": "%s"}\n' "${reason//\"/\\\"}"
exit 0
