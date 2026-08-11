#!/usr/bin/env bash
# OpenSpec + git worktree session helper.
#
# Without `--worktree` flag in opencode (issue #35471), we manually
# orchestrate worktrees so concurrent sessions don't pollute each other.
#
# Usage:
#   ./scripts/worktree-help.sh start <name>        # create worktree + start opencode
#   ./scripts/worktree-help.sh list               # list active worktrees
#   ./scripts/worktree-help.sh push <name>        # push worktree branch to origin
#   ./scripts/worktree-help.sh delete <name>      # remove worktree(s) + branch(es), comma-separated
#   ./scripts/worktree-help.sh remove <name>      # remove worktree(s) only, keep branches
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
    local include_file="${REPO_ROOT}/.worktreeinclude"
    [[ -f "${include_file}" ]] || return 0
    while IFS= read -r path; do
        [[ -z "${path}" || "${path}" == \#* ]] && continue
        [[ -e "${REPO_ROOT}/${path}" ]] && mkdir -p "${wt_path}/$(dirname "${path}")" && cp -a "${REPO_ROOT}/${path}" "${wt_path}/${path}"
    done < "${include_file}"
}

sync_openspec_change() {
    local name="$1"
    local wt_path="$2"
    local src="${REPO_ROOT}/openspec/changes/${name}"
    local dst="${wt_path}/openspec/changes/${name}"

    [[ -d "${src}" ]] || return 0

    if [[ -d "${dst}" ]]; then
        echo "openspec/changes/${name} already present in worktree — skipping sync"
        return 0
    fi

    local porcelain
    porcelain="$(cd "${REPO_ROOT}" && git status --porcelain -- "openspec/changes/${name}" 2>/dev/null || true)"
    [[ -n "${porcelain}" ]] || return 0

    echo "Detected uncommitted OpenSpec change '${name}' in main repo."
    echo "  Syncing to worktree (clean up main with: rm -rf openspec/changes/${name})..."
    mkdir -p "$(dirname "${dst}")"
    cp -R "${src}" "${dst}"
    echo "  Synced: ${src} → ${dst}"
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
            sync_openspec_change "${name}" "${wt_path}"
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

        stale=""
        while IFS= read -r dir; do
            [[ -z "${dir}" ]] && continue
            if ! git worktree list --porcelain | awk -v d="${dir}" '$1=="worktree" && $2==d {found=1} END{exit !found}'; then
                stale="${stale}${dir}\n"
            fi
        done < <(find "${WT_ROOT}" -maxdepth 1 -mindepth 1 -type d 2>/dev/null)
        if [[ -n "${stale}" ]]; then
            echo ""
            echo "Stale directories (not registered as git worktrees):"
            printf "${stale}" | sed 's/^/  /'
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

    delete|remove)
        names="${2:-}"
        if [[ -z "${names}" ]]; then
            echo "Error: missing worktree name" >&2
            echo "Usage: $0 ${cmd} <name>[,<name>...]" >&2
            echo "Example: $0 ${cmd} change-a,change-b" >&2
            exit 1
        fi

        IFS=',' read -ra name_list <<< "${names}"
        for name in "${name_list[@]}"; do
            name="${name#"${name%%[![:space:]]*}"}"
            name="${name%"${name##*[![:space:]]}"}"
            [[ -z "${name}" ]] && continue

            wt_path="${WT_ROOT}/${name}"
            branch="feat/${name}"

            if [[ ! -d "${wt_path}" ]]; then
                echo "Warning: worktree not found: ${wt_path} (skipped)" >&2
            else
                if git worktree remove --force "${wt_path}" 2>/dev/null; then
                    echo "Removed worktree: ${wt_path}"
                else
                    echo "Warning: failed to remove worktree: ${wt_path}" >&2
                fi
            fi

            if [[ "${cmd}" == "delete" ]]; then
                if git rev-parse --verify "${branch}" >/dev/null 2>&1; then
                    if git branch -D "${branch}" >/dev/null 2>&1; then
                        echo "Deleted branch: ${branch}"
                    else
                        echo "Warning: failed to delete branch: ${branch}" >&2
                    fi
                else
                    echo "Warning: branch not found: ${branch} (skipped)" >&2
                fi
            fi
        done
        ;;

    clean)
        echo "Error: 'clean' is not a valid command" >&2
        echo "Use '$0 delete <name>' to remove worktree + branch" >&2
        echo "Use '$0 remove <name>' to remove worktree only" >&2
        exit 1
        ;;

    help|*)
        cat <<EOF
Usage:
  $0 start <name> [--background]  # create worktree + start opencode
  $0 list                         # list active worktrees
  $0 push <name>                  # push worktree branch to origin
  $0 delete <name>[,<name>...]    # remove worktree(s) + branch(es)
  $0 remove <name>[,<name>...]    # remove worktree(s), keep branches

Workflow:
  $0 start feat-my-change         # creates worktree + branch feat/feat-my-change
  # ... do work in worktree ...
  $0 push feat-my-change          # pushes feat/feat-my-change to origin
  $0 delete feat-my-change        # removes worktree + branch after merge
  $0 remove feat-my-change        # removes worktree, keeps branch
EOF
        ;;
esac
