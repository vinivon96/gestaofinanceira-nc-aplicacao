#!/usr/bin/env bash
# Importa todo o histórico disponível: todos os CSVs e XLSXs de
# dbextratos_reais/ e todos os PDFs de faturas_reais/, depois roda classificar.py e
# vincular_pagamento_fatura.py UMA VEZ cada (eles reprocessam o banco
# inteiro, então rodar por arquivo seria redundante e mais lento).
#
# Idempotente: os parsers usam INSERT OR IGNORE / UPSERT por chave primária
# (id_transacao, id_lancamento, id_fatura), então rodar de novo com arquivos
# já importados misturados com novos não duplica nada — só o que for novo
# entra. classificar.py só toca registros com categoria IS NULL, e
# vincular_pagamento_fatura.py só liga faturas ainda sem transação vinculada.
#
# Uso:
#   ./scripts/importar_historico.sh [caminho_do_banco]
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DB_PATH="${1:-db/financeiro.db}"
DIR_EXTRATOS="dbextratos_reais"
DIR_FATURAS="faturas_reais"

PYTHON="python"

falhas=0

contar() {
  # $1 = tabela
  "$PYTHON" -c "
import sqlite3
conn = sqlite3.connect('$DB_PATH')
print(conn.execute('SELECT COUNT(*) FROM $1').fetchone()[0])
"
}

contar_revisar_manual() {
  # $1 = tabela
  "$PYTHON" -c "
import sqlite3
conn = sqlite3.connect('$DB_PATH')
print(conn.execute(\"SELECT COUNT(*) FROM $1 WHERE status = 'revisar_manual'\").fetchone()[0])
"
}

echo "=== Importação de histórico — $(date '+%Y-%m-%d %H:%M:%S') ==="
echo "Banco: $DB_PATH"
echo

transacoes_antes=$(contar transacoes)
lancamentos_antes=$(contar lancamentos_fatura)

echo "--- 1/4: Extratos Inter (CSV/XLSX) em $DIR_EXTRATOS/ ---"
shopt -s nullglob
extratos=("$DIR_EXTRATOS"/*.csv "$DIR_EXTRATOS"/*.xlsx)
outros=("$DIR_EXTRATOS"/*)
shopt -u nullglob

if [ ${#extratos[@]} -eq 0 ]; then
  echo "  Nenhum CSV/XLSX encontrado."
else
  for arquivo in "${extratos[@]}"; do
    echo "  Processando: $arquivo"
    if ! "$PYTHON" scripts/parse_extrato_inter.py "$arquivo" "$DB_PATH"; then
      echo "  ERRO ao processar $arquivo — pulando." >&2
      falhas=$((falhas + 1))
    fi
  done
fi

# Aviso sobre arquivos em formato não suportado (nem .csv nem .xlsx)
for arquivo in "${outros[@]}"; do
  case "$arquivo" in
    *.csv | *.xlsx) ;;
    *) echo "  Aviso: '$arquivo' não é .csv nem .xlsx — formato não suportado pelo parser, ignorado." ;;
  esac
done

echo
echo "--- 2/4: Faturas de cartão (PDF) em $DIR_FATURAS/ ---"
shopt -s nullglob
pdfs=("$DIR_FATURAS"/*.pdf)
shopt -u nullglob

if [ ${#pdfs[@]} -eq 0 ]; then
  echo "  Nenhum PDF encontrado."
else
  for arquivo in "${pdfs[@]}"; do
    echo "  Processando: $arquivo"
    if ! "$PYTHON" scripts/parse_fatura_cartao.py "$arquivo" "$DB_PATH"; then
      echo "  ERRO ao processar $arquivo — pulando (verifique FATURA_CARTAO_SENHA se o PDF for protegido)." >&2
      falhas=$((falhas + 1))
    fi
  done
fi

echo
echo "--- 3/4: Motor de classificação (roda uma vez sobre o banco inteiro) ---"
"$PYTHON" scripts/classificar.py "$DB_PATH"

echo
echo "--- 4/4: Vínculo de pagamento de fatura (roda uma vez) ---"
"$PYTHON" scripts/vincular_pagamento_fatura.py "$DB_PATH"

transacoes_depois=$(contar transacoes)
lancamentos_depois=$(contar lancamentos_fatura)
transacoes_pendentes=$(contar_revisar_manual transacoes)
lancamentos_pendentes=$(contar_revisar_manual lancamentos_fatura)

echo
echo "=== Resumo ==="
echo "Transações novas:              $((transacoes_depois - transacoes_antes))"
echo "Lançamentos de fatura novos:    $((lancamentos_depois - lancamentos_antes))"
echo "Transações pendentes (revisar_manual):           $transacoes_pendentes"
echo "Lançamentos de fatura pendentes (revisar_manual): $lancamentos_pendentes"

if [ "$falhas" -gt 0 ]; then
  echo
  echo "AVISO: $falhas arquivo(s) falharam durante a importação — veja os erros acima." >&2
  exit 1
fi

echo
echo "Importação concluída sem erros."
