#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker/docker-compose.blue-green.yml}"
STATE_DIR=".blue-green"
ACTIVE_FILE="${STATE_DIR}/active"
PREV_FILE="${STATE_DIR}/previous"

mkdir -p "${STATE_DIR}"

get_active() {
    if [[ -f "${ACTIVE_FILE}" ]]; then
        cat "${ACTIVE_FILE}"
    else
        echo "blue"
    fi
}

get_prev() {
    if [[ -f "${PREV_FILE}" ]]; then
        cat "${PREV_FILE}"
    else
        echo ""
    fi
}

set_active() {
    local new_color="$1"
    local old_color
    old_color=$(get_active)
    echo "${old_color}" > "${PREV_FILE}"
    echo "${new_color}" > "${ACTIVE_FILE}"
    docker compose -f "${COMPOSE_FILE}" exec -T nginx sh -c \
        "echo 'set \$ACTIVE_COLOR ${new_color};' > /etc/nginx/templates/default.conf.template && nginx -s reload" 2>/dev/null || true
    echo "Active instance: ${new_color} (was ${old_color})"
}

case "${1:-status}" in
    active)
        color="${2:?Usage: blue-green-switch.sh active <blue|green>}"
        set_active "${color}"
        ;;
    status)
        echo "Active: $(get_active)"
        echo "Previous: $(get_prev)"
        ;;
    rollback)
        prev=$(get_prev)
        if [[ -z "${prev}" ]]; then
            echo "No previous instance recorded. Cannot rollback."
            exit 1
        fi
        set_active "${prev}"
        ;;
    *)
        echo "Usage: $0 {active <blue|green>|status|rollback}"
        exit 1
        ;;
esac
