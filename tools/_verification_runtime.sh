#!/usr/bin/env bash
# Mechanical cleanup and digest helpers for Hormuz verification entry points.
# Proof assertions and container launch arguments intentionally stay in the
# independently executable scripts that source this file.

hormuz_is_sha256_digest() {
  [[ "${1:-}" =~ ^sha256:[0-9a-f]{64}$ ]]
}

hormuz_remove_temporary_file() {
  local path="${1:-}"
  if [[ -n "${path}" ]]; then
    rm -f -- "${path}"
  fi
}

hormuz_remove_disposable_container() {
  local container="${1:-}"
  local label_name="${2:-}"
  local label_value
  if [[ ! "${container}" =~ ^hormuz-[A-Za-z0-9_.-]+$ ]]; then
    return 0
  fi
  if [[ ! "${label_name}" =~ ^io\.hormuz\.[a-z0-9.-]+$ ]]; then
    return 0
  fi
  label_value="$(docker inspect --format "{{ index .Config.Labels \"${label_name}\" }}" "${container}" 2>/dev/null || true)"
  if [[ "${label_value}" == "true" ]]; then
    docker rm --force "${container}" >/dev/null 2>&1 || true
  fi
}

hormuz_remove_disposable_network() {
  local network="${1:-}"
  local label_name="${2:-}"
  local label_value
  if [[ ! "${network}" =~ ^hormuz-[A-Za-z0-9_.-]+$ ]]; then
    return 0
  fi
  if [[ ! "${label_name}" =~ ^io\.hormuz\.[a-z0-9.-]+$ ]]; then
    return 0
  fi
  label_value="$(docker network inspect --format "{{ index .Labels \"${label_name}\" }}" "${network}" 2>/dev/null || true)"
  if [[ "${label_value}" == "true" ]]; then
    docker network rm "${network}" >/dev/null 2>&1 || true
  fi
}
