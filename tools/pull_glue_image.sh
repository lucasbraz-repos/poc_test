#!/usr/bin/env bash
set -euo pipefail

image="public.ecr.aws/glue/aws-glue-libs:5"
delay=10

for attempt in 1 2 3 4 5; do
  if docker pull "$image"; then
    exit 0
  fi
  if [[ "$attempt" -eq 5 ]]; then
    printf 'Nao foi possivel baixar a imagem Glue apos %s tentativas.\n' "$attempt" >&2
    exit 1
  fi
  printf 'Download Glue falhou (tentativa %s/5); nova tentativa em %ss.\n' "$attempt" "$delay" >&2
  sleep "$delay"
  delay=$((delay * 2))
done
