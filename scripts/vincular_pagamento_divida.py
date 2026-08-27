"""Vincula transações de saída ao pagamento de dívidas (fora do cartão).

Para cada dívida em `dividas` com status = 'aberta' e `data_vencimento_proxima`
preenchida, procura em `transacoes` uma saída com `valor` igual (± R$ 0,01) a
`valor_parcela`, dentro de uma janela de até 15 dias antes/depois do
vencimento (mesma janela e tolerância de scripts/vincular_pagamento_fatura.py).
Se encontrar, marca a transação com categoria = 'pagamento_divida', grava
`id_divida`, decrementa `parcelas_restantes` em 1, avança
`data_vencimento_proxima` em +1 mês e marca `dividas.status = 'quitada'`
quando `parcelas_restantes` chega a 0.

Limitação assumida: avanço de vencimento fixo em +1 mês (mesma granularidade
de contas_recorrentes). Dívida com periodicidade diferente de mensal precisa
ter `data_vencimento_proxima` ajustada manualmente no dashboard entre uma
parcela e outra.

Idempotente: só considera transações com `id_divida IS NULL`, então rodar de
novo não re-vincula o que já foi resolvido.

Uso:
    python scripts/vincular_pagamento_divida.py [caminho_do_banco]
"""
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "db" / "financeiro.db"

CATEGORIA_PAGAMENTO_DIVIDA = "pagamento_divida"
TOLERANCIA_VALOR = 0.01
JANELA_DIAS = 15


def somar_um_mes(data_iso: str) -> str:
    d = date.fromisoformat(data_iso)
    ano, mes = d.year, d.month + 1
    if mes > 12:
        ano, mes = ano + 1, 1
    ultimo_dia_mes_seguinte = (date(ano + (mes == 12), mes % 12 + 1, 1) - timedelta(days=1)).day
    dia = min(d.day, ultimo_dia_mes_seguinte)
    return date(ano, mes, dia).isoformat()


def buscar_transacao_correspondente(
    conn: sqlite3.Connection, valor_parcela: float, data_vencimento: str
) -> str | None:
    vencimento = date.fromisoformat(data_vencimento)
    inicio = vencimento - timedelta(days=JANELA_DIAS)
    fim = vencimento + timedelta(days=JANELA_DIAS)

    row = conn.execute(
        """
        SELECT id_transacao FROM transacoes
        WHERE tipo = 'saida'
          AND id_divida IS NULL
          AND data BETWEEN ? AND ?
          AND ABS(valor - ?) <= ?
        ORDER BY ABS(valor - ?), data
        LIMIT 1
        """,
        (inicio.isoformat(), fim.isoformat(), valor_parcela, TOLERANCIA_VALOR, valor_parcela),
    ).fetchone()
    return row[0] if row else None


def vincular_pagamentos(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    vinculados = []

    dividas = conn.execute(
        "SELECT id_divida, valor_parcela, parcelas_restantes, data_vencimento_proxima "
        "FROM dividas WHERE status = 'aberta'"
    ).fetchall()

    for id_divida, valor_parcela, parcelas_restantes, data_vencimento_proxima in dividas:
        if data_vencimento_proxima is None:
            continue

        id_transacao = buscar_transacao_correspondente(conn, valor_parcela, data_vencimento_proxima)
        if id_transacao is None:
            continue

        conn.execute(
            "UPDATE transacoes SET categoria = ?, status = 'classificado', id_divida = ? WHERE id_transacao = ?",
            (CATEGORIA_PAGAMENTO_DIVIDA, id_divida, id_transacao),
        )

        parcelas_restantes -= 1
        if parcelas_restantes <= 0:
            conn.execute(
                "UPDATE dividas SET parcelas_restantes = 0, status = 'quitada' WHERE id_divida = ?",
                (id_divida,),
            )
        else:
            proxima_data = somar_um_mes(data_vencimento_proxima)
            conn.execute(
                "UPDATE dividas SET parcelas_restantes = ?, data_vencimento_proxima = ? WHERE id_divida = ?",
                (parcelas_restantes, proxima_data, id_divida),
            )

        vinculados.append((id_divida, id_transacao))

    return vinculados


def vincular(db_path: Path) -> list[tuple[str, str]]:
    conn = sqlite3.connect(db_path)
    try:
        vinculados = vincular_pagamentos(conn)
        conn.commit()
    finally:
        conn.close()
    return vinculados


if __name__ == "__main__":
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB_PATH

    vinculados = vincular(db_path)
    if not vinculados:
        print("Nenhuma transação vinculada.")
    for id_divida, id_transacao in vinculados:
        print(f"dívida {id_divida} <- transacao {id_transacao}")
