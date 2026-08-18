#!/usr/bin/env bash
# Deploy do dashboard na VPS: atualiza o código (se for um repo git) e
# reconstrói/reinicia o container via docker compose, preservando o banco
# (db/ é montado como volume — ver docker-compose.yml).
#
# Uso, na VPS, dentro da pasta do projeto:
#   ./deploy.sh
set -euo pipefail

cd "$(dirname "$0")"

if [ -d .git ]; then
  echo "==> Atualizando código (git pull)..."
  git pull
else
  echo "==> Aviso: esta pasta não é um repo git — pulando git pull."
  echo "    Garanta que o código já foi sincronizado pra VPS (rsync/scp) antes de continuar."
fi

if [ ! -f .env ]; then
  echo "ERRO: arquivo .env não encontrado. Copie .env.example para .env e preencha DASHBOARD_USER/DASHBOARD_SENHA." >&2
  exit 1
fi

echo "==> Reconstruindo e subindo o container..."
docker compose up -d --build

echo "==> Aguardando o container responder..."
porta=$(grep -E '^DASHBOARD_PORT=' .env | cut -d= -f2)
porta=${porta:-5000}
for _ in $(seq 1 15); do
  codigo=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${porta}/" 2>/dev/null || echo "000")
  if [ "$codigo" = "200" ] || [ "$codigo" = "401" ]; then
    echo "==> Dashboard no ar em http://localhost:${porta}/ (HTTP ${codigo})"
    exit 0
  fi
  sleep 1
done

echo "AVISO: não confirmei resposta do container em 15s — confira com: docker compose logs -f" >&2
