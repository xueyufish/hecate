#!/usr/bin/env bash
# OpenSpec + git worktree session helper.
#
# Without `--worktree` flag in opencode (issue #35471), we manually
# orchestrate worktrees so concurrent sessions don't pollute each other.
#
# Usage:
#   ./scripts/worktree-help.sh start <name> [--tools <list>|--no-tools]
#                                       # create worktree + openspec init (no auto-launch)
#   ./scripts/worktree-help.sh launch <name> [--tool <name>] [--background]
#                                       # bind worktree to an AI tool (opencode by default)
#   ./scripts/worktree-help.sh list   # list active worktrees
#   ./scripts/worktree-help.sh push <name>
#                                       # push worktree branch to origin
#   ./scripts/worktree-help.sh delete <name>[,<name>...]
#                                       # remove worktree(s) + branch(es), comma-separated
#   ./scripts/worktree-help.sh remove <name>[,<name>...]
#                                       # remove worktree(s) only, keep branches
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

    [[ -d "${src}" ]] || return 1

    if [[ -d "${dst}" ]]; then
        echo "openspec/changes/${name} already present in worktree — skipping sync"
        return 1
    fi

    local porcelain
    porcelain="$(cd "${REPO_ROOT}" && git status --porcelain -- "openspec/changes/${name}" 2>/dev/null || true)"
    [[ -n "${porcelain}" ]] || return 1

    echo "Detected uncommitted OpenSpec change '${name}' in main repo."
    echo "  Syncing to worktree..."
    mkdir -p "$(dirname "${dst}")"
    cp -R "${src}" "${dst}"
    echo "  Synced: ${src} → ${dst}"
    return 0
}

cleanup_main_openspec_change() {
    local name="$1"
    local wt_path="$2"
    local src="${REPO_ROOT}/openspec/changes/${name}"
    local dst="${wt_path}/openspec/changes/${name}"

    [[ -d "${src}" ]] || return 0

    if [[ ! -d "${dst}" ]]; then
        echo "  [cleanup] Skipped: worktree copy missing at ${dst}"
        return 1
    fi

    if [[ ! -f "${src}/.openspec.yaml" || ! -f "${dst}/.openspec.yaml" ]]; then
        echo "  [cleanup] Skipped: missing .openspec.yaml in src or dst (corrupt sync?)"
        return 1
    fi

    if ! diff -rq "${src}" "${dst}" >/dev/null 2>&1; then
        echo "  [cleanup] Skipped: src and dst differ:"
        diff -rq "${src}" "${dst}" | sed 's/^/    /' | head -10
        return 1
    fi

    echo "  [cleanup] Verified sync — removing main checkout copy..."
    rm -rf "${src}"
    echo "  [cleanup] Removed: ${src}"
    return 0
}

init_openspec_tools() {
    local wt_path="$1"
    local tools="$2"

    [[ -n "${tools}" ]] || { echo "Skipping openspec init (no tools specified)"; return 0; }
    [[ "${tools}" != "none" ]] || { echo "Skipping openspec init (--tools none)"; return 0; }

    if ! command -v openspec >/dev/null 2>&1; then
        echo "Warning: 'openspec' CLI not found in PATH; skipping init." >&2
        echo "  Install: https://github.com/Fission-AI/OpenSpec" >&2
        return 1
    fi

    echo "Initializing openspec for tools: ${tools}"
    if (cd "${wt_path}" && openspec init \
            --tools "${tools}" \
            --force \
            --no-animation \
            --no-copilot-cloud \
            .); then
        echo "  openspec initialized — reload your AI tool to pick up /opsx-* commands."
    else
        echo "Warning: openspec init failed for some tools; worktree is still usable." >&2
    fi
}

launch_ai_tool() {
    local wt_path="$1"
    local tool="${2:-opencode}"
    local background="${3:-false}"

    if ! command -v "${tool}" >/dev/null 2>&1; then
        echo "Error: AI tool '${tool}' not found in PATH." >&2
        echo "  Install it, or pick another tool:" >&2
        echo "    --tool opencode      # default" >&2
        echo "    --tool zcode         # ZCode CLI" >&2
        echo "    --tool workbuddy     # WorkBuddy CLI (Tencent)" >&2
        echo "    --tool codex         # OpenAI Codex CLI" >&2
        exit 1
    fi

    if [[ "${background}" == "true" ]]; then
        (cd "${wt_path}" && nohup "${tool}" >"${wt_path}/.${tool}.log" 2>&1 &)
        echo "${tool} started in background, logs: ${wt_path}/.${tool}.log"
    else
        cd "${wt_path}"
        exec "${tool}"
    fi
}

case "${cmd}" in
    start)
        shift
        name=""
        tools="opencode,zcode,codex"
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --tools)
                    [[ $# -ge 2 ]] || { echo "Error: --tools requires a value" >&2; exit 1; }
                    tools="$2"
                    shift 2
                    ;;
                --tools=*)
                    tools="${1#--tools=}"
                    shift
                    ;;
                --no-tools)
                    tools=""
                    shift
                    ;;
                -h|--help)
                    sed -n '/^    help|\*) /,/^            ;;$/p' "$0"
                    exit 0
                    ;;
                --*)
                    echo "Error: unknown flag: $1" >&2
                    echo "Usage: $0 start <name> [--tools <list>|--no-tools]" >&2
                    exit 1
                    ;;
                *)
                    if [[ -z "${name}" ]]; then
                        name="$1"
                        shift
                    else
                        echo "Error: unexpected positional arg: $1" >&2
                        exit 1
                    fi
                    ;;
            esac
        done
        if [[ -z "${name}" ]]; then
            echo "Usage: $0 start <name> [--tools <list>|--no-tools]" >&2
            exit 1
        fi
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
            if sync_openspec_change "${name}" "${wt_path}"; then
                cleanup_main_openspec_change "${name}" "${wt_path}"
            fi
            echo "Created worktree: ${wt_path} (branch: ${branch})"
        fi

        init_openspec_tools "${wt_path}" "${tools}"

        echo ""
        echo "Worktree ready: ${wt_path}"
        echo "Next steps:"
        echo "  cd ${wt_path}"
        echo "  $0 launch ${name}                       # launch default AI tool (opencode)"
        echo "  $0 launch ${name} --tool zcode          # launch zcode"
        echo "  $0 launch ${name} --tool workbuddy      # launch workbuddy"
        echo "  $0 launch ${name} --tool codex          # launch codex"
        echo "  $0 launch ${name} --background          # launch in background"
        ;;

    launch)
        shift
        name=""
        tool="opencode"
        background="false"
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --tool)
                    [[ $# -ge 2 ]] || { echo "Error: --tool requires a value" >&2; exit 1; }
                    tool="$2"
                    shift 2
                    ;;
                --tool=*)
                    tool="${1#--tool=}"
                    shift
                    ;;
                --background)
                    background="true"
                    shift
                    ;;
                -h|--help)
                    sed -n '/^    help|\*) /,/^            ;;$/p' "$0"
                    exit 0
                    ;;
                --*)
                    echo "Error: unknown flag: $1" >&2
                    echo "Usage: $0 launch <name> [--tool <name>] [--background]" >&2
                    exit 1
                    ;;
                *)
                    if [[ -z "${name}" ]]; then
                        name="$1"
                        shift
                    else
                        echo "Error: unexpected positional arg: $1" >&2
                        exit 1
                    fi
                    ;;
            esac
        done
        if [[ -z "${name}" ]]; then
            echo "Usage: $0 launch <name> [--tool <name>] [--background]" >&2
            exit 1
        fi
        wt_path="${WT_ROOT}/${name}"
        if [[ ! -d "${wt_path}" ]]; then
            echo "Error: worktree not found: ${wt_path}" >&2
            echo "Run: $0 start ${name}" >&2
            exit 1
        fi
        launch_ai_tool "${wt_path}" "${tool}" "${background}"
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
  $0 start <name> [--tools <list>|--no-tools]
                                      # create worktree + init openspec (no auto-launch)
  $0 launch <name> [--tool <name>] [--background]
                                      # bind worktree to an AI tool (opencode by default)
  $0 list                             # list active worktrees
  $0 push <name>                      # push worktree branch to origin
  $0 delete <name>[,<name>...]        # remove worktree(s) + branch(es)
  $0 remove <name>[,<name>...]        # remove worktree(s), keep branches

start --tools flags:
  --tools <list>   comma-separated list passed to 'openspec init --tools'
                   (default: opencode,zcode,codex)
                   use "all" or "none" to forward to openspec verbatim
  --no-tools       skip 'openspec init' entirely

launch --tool flags:
  --tool <name>    AI CLI to launch in the worktree (default: opencode)
                   common values: opencode, zcode, workbuddy, codex
  --background     launch detached; logs go to <wt>/.<tool>.log

Workflow:
  $0 start feat-my-change               # creates worktree + openspec init, no launch
  $0 launch feat-my-change              # bind to opencode (foreground)
  $0 launch feat-my-change --tool codex # bind to codex
  # ... do work in worktree ...
  $0 push feat-my-change                # pushes feat/feat-my-change to origin
  $0 delete feat-my-change              # removes worktree + branch after merge
  $0 remove feat-my-change              # removes worktree, keeps branch
EOF
        ;;
esac
