#!/bin/sh
set -eu

PROM_TEMPLATE="${PROM_TEMPLATE:-/etc/prometheus/prometheus.yml.template}"
PROM_OUTPUT="${PROM_OUTPUT:-/etc/prometheus/prometheus.yml}"
PROM_BINARY="${PROM_BINARY:-/bin/prometheus}"

strip_scheme() {
  echo "$1" | sed -e 's#^http://##' -e 's#^https://##'
}

RAG_EXPORTER_HOSTPORT="$(strip_scheme "${RAG_EXPORTER_TARGET}")"
OLLAMA_EXPORTER_HOSTPORT="$(strip_scheme "${OLLAMA_EXPORTER_TARGET}")"
EMBEDDING_EXPORTER_HOSTPORT="$(strip_scheme "${EMBEDDING_EXPORTER_TARGET}")"

export RAG_EXPORTER_HOSTPORT OLLAMA_EXPORTER_HOSTPORT EMBEDDING_EXPORTER_HOSTPORT
export RAG_SERVER_NAME OLLAMA_SERVER_NAME EMBEDDING_SERVER_NAME

sed \
  -e "s|\${RAG_EXPORTER_HOSTPORT}|${RAG_EXPORTER_HOSTPORT}|g" \
  -e "s|\${OLLAMA_EXPORTER_HOSTPORT}|${OLLAMA_EXPORTER_HOSTPORT}|g" \
  -e "s|\${EMBEDDING_EXPORTER_HOSTPORT}|${EMBEDDING_EXPORTER_HOSTPORT}|g" \
  -e "s|\${RAG_SERVER_NAME}|${RAG_SERVER_NAME}|g" \
  -e "s|\${OLLAMA_SERVER_NAME}|${OLLAMA_SERVER_NAME}|g" \
  -e "s|\${EMBEDDING_SERVER_NAME}|${EMBEDDING_SERVER_NAME}|g" \
  "${PROM_TEMPLATE}" > "${PROM_OUTPUT}"
exec "${PROM_BINARY}" --config.file="${PROM_OUTPUT}" --storage.tsdb.path=/prometheus
