#!/usr/bin/env bash
# OpenSpec + git worktree session helper.
#
# Without `--worktree` flag in opencode (issue #35471), we manually
# orchestrate worktrees so concurrent sessions don't pollute each other.
#
# Usage:
#   ./scripts/dev-worktree.sh start <name>        # create worktree + start opencode
#   ./scripts/dev-worktree.sh list               # list active worktrees
#   ./scripts/dev-worktree.sh push <name>        # push worktree branch to origin
#   ./scripts/dev-worktree.sh clean              # remove finished worktrees
#
# The worktree path is namespaced by OpenSpec change name so multiple
# sessions (code vs docs) for the SAME change don't collide.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
WT_ROOT_RAW="${HECATE_WORKTREE_ROOT:-${REPO_ROOT}/../.worktrees}"
WT_ROOT="$(cd "${REPO_ROOT}" && cd "$(dirname "${WT_ROOT_RAW}")" && mkdir -p "$(basename "${WT_ROOT_RAW}")" && cd "$(basename "${WT_ROOT_RAW}")" && pwd)"
mkdir -p "${WT_ROOT}"

cmd="${1:-help}"

copy_worktreeinclude_files() {
    local wt_path="$1"
    [[ -f .worktreeinclude ]] || return 0
    while IFS= read -r path; do
        [[ -z "${path}" || "${path}" == \#* ]] && continue
        [[ -e "${REPO_ROOT}/${path}" ]] && mkdir -p "${wt_path}/$(dirname "${path}")" && cp -a "${REPO_ROOT}/${path}" "${wt_path}/${path}"
    done < .worktreeinclude
}

case "${cmd}" in
    start)
        name="${2:?Usage: $0 start <change-name>}"
        wt_path="${WT_ROOT}/${name}"
        branch="feat/${name}"

        if [[ -d "${wt_path}" ]]; then
            echo "Worktree already exists: ${wt_path}"
            cd "${wt_path}"
        else
            cd "${REPO_ROOT}"
            if git rev-parse --verify "${branch}" >/dev/null 2>&1; then
                git worktree add "${wt_path}" "${branch}"
            else
                git worktree add -b "${branch}" "${wt_path}" main
            fi
            cd "${wt_path}"
            copy_worktreeinclude_files "${wt_path}"
            echo "Created worktree: ${wt_path} (branch: ${branch})"
        fi

        if [[ "${3:-}" == "--background" ]]; then
            (cd "${wt_path}" && nohup opencode >"${wt_path}/.opencode.log" 2>&1 &)
            echo "opencode started in background, logs: ${wt_path}/.opencode.log"
        else
            cd "${wt_path}"
            exec opencode
        fi
        ;;

    list)
        echo "Active worktrees under ${WT_ROOT}:"
        entries=$(git worktree list --porcelain | awk -v root="${WT_ROOT}/" '
            $1 == "worktree" && index($2, root) == 1 { print $2 }
        ')
        if [[ -n "${entries}" ]]; then
            echo "${entries}" | sed 's/^/  /'
        else
            echo "  (none)"
        fi
        ;;

    push)
        name="${2:?Usage: $0 push <change-name>}"
        wt_path="${WT_ROOT}/${name}"
        if [[ ! -d "${wt_path}" ]]; then
            echo "Error: worktree not found: ${wt_path}" >&2
            echo "Run: $0 start $name" >&2
            exit 1
        fi
        cd "${wt_path}"
        if git remote get-url origin >/dev/null 2>&1; then
            branch="$(git rev-parse --abbrev-ref HEAD)"
            git push -u origin "${branch}"
        else
            echo "Error: no 'origin' remote configured" >&2
            exit 1
        fi
        ;;

    clean)
        name="${2:-}"
        if [[ -n "${name}" ]]; then
            git worktree remove --force "${WT_ROOT}/${name}" || true
            git branch -d "feat/${name}" 2>/dev/null || true
        else
            for wt in $(git worktree list --porcelain | grep "worktree ${WT_ROOT}/" | awk '{print $2}'); do
                git worktree remove --force "${wt}" || true
            done
        fi
        ;;

    help|*)
        cat <<EOF
Usage:
  $0 start <name> [--background]  # create worktree + start opencode
  $0 list                        # list active worktrees
  $0 push <name>                 # push worktree branch to origin
  $0 clean [<name>]              # remove worktree(s)

Workflow:
  $0 start feat-my-change        # creates worktree + branch feat/feat-my-change
  # ... do work in worktree ...
  $0 push feat-my-change         # pushes feat/feat-my-change to origin
  $0 clean feat-my-change        # removes worktree + branch after merge
EOF
        ;;
esac
