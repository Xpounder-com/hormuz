#!/bin/sh

set -eu
umask 077

compose_root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
project_name=${HORMUZ_COMPOSE_PROJECT_NAME:-hormuz-pilot}
backup_root="${compose_root}/runtime/backups"
requested_path=${1:-}

if [ ! -d "${backup_root}" ] || [ -L "${backup_root}" ]; then
    printf 'protected backup directory is unavailable; run prepare first\n' >&2
    exit 2
fi
if [ -n "${requested_path}" ]; then
    output_path=${requested_path}
else
    output_path="${backup_root}/hormuz-$(date -u +%Y%m%dT%H%M%SZ).dump"
fi
case "${output_path}" in
    /*) ;;
    *) output_path="${PWD}/${output_path}" ;;
esac
output_directory=$(dirname -- "${output_path}")
output_name=$(basename -- "${output_path}")
if [ ! -d "${output_directory}" ] || [ -L "${output_directory}" ]; then
    printf 'backup output directory must be an existing non-symlink directory\n' >&2
    exit 2
fi
physical_output_directory=$(CDPATH= cd -- "${output_directory}" && pwd -P)
physical_backup_root=$(CDPATH= cd -- "${backup_root}" && pwd -P)
if [ "${physical_output_directory}" != "${physical_backup_root}" ]; then
    printf 'pilot backups must be created directly inside the protected runtime backup directory\n' >&2
    exit 2
fi
output_path="${physical_output_directory}/${output_name}"
if [ -e "${output_path}" ] || [ -L "${output_path}" ]; then
    printf 'backup output already exists and was not replaced: %s\n' "${output_path}" >&2
    exit 2
fi

temporary_path="${output_path}.partial.$$"
cleanup() {
    rm -f -- "${temporary_path}"
}
trap cleanup EXIT HUP INT TERM

docker compose --project-directory "${compose_root}" --project-name "${project_name}" \
    -f "${compose_root}/compose.yaml" \
    exec -T --user postgres postgres \
    pg_dump --username postgres --dbname hormuz --format=custom --no-owner \
    >"${temporary_path}"
if [ ! -s "${temporary_path}" ]; then
    printf 'PostgreSQL backup was empty\n' >&2
    exit 1
fi
chmod 0600 "${temporary_path}"
mv "${temporary_path}" "${output_path}"
trap - EXIT HUP INT TERM

digest=$(openssl dgst -sha256 -r "${output_path}" | awk '{print $1}')
size=$(wc -c <"${output_path}" | tr -d ' ')
printf 'created protected pilot backup: %s bytes=%s sha256=%s\n' \
    "${output_path}" "${size}" "${digest}"
