"""Vincula transações de saída ao pagamento da fatura de cartão correspondente.

Para cada fatura em `faturas_cartao`, procura em `transacoes` uma saída com
`valor` igual (± R$ 0,01) a `valor_total_fatura`, dentro de uma janela de até
15 dias antes/depois de `data_vencimento` (pagamentos reais observados vieram
adiantados/atrasados — ver JANELA_DIAS). Se encontrar, marca a transação com
categoria = 'pagamento_fatura' (código do plano de contas — ver
db/seed_plano_de_contas.sql), grava `id_fatura` para linkar as duas tabelas,
e marca `faturas_cartao.status_pagamento = 'paga'`.

Idempotente: só considera transações com `id_fatura IS NULL`, então rodar de
novo não re-vincula o que já foi resolvido.

Uso:
    python scripts/vincular_pagamento_fatura.py [caminho_do_banco]
"""
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "db" / "financeiro.db"

CATEGORIA_PAGAMENTO_FATURA = "pagamento_fatura"
TOLERANCIA_VALOR = 0.01
JANELA_DIAS = 15


def buscar_transacao_correspondente(
    conn: sqlite3.Connection, id_fatura: str, valor_total_fatura: float, data_vencimento: str
) -> str | None:
    vencimento = date.fromisoformat(data_vencimento)
    inicio = vencimento - timedelta(days=JANELA_DIAS)
    fim = vencimento + timedelta(days=JANELA_DIAS)

    row = conn.execute(
        """
        SELECT id_transacao FROM transacoes
        WHERE tipo = 'saida'
          AND id_fatura IS NULL
          AND data BETWEEN ? AND ?
          AND ABS(valor - ?) <= ?
        ORDER BY ABS(valor - ?), data
        LIMIT 1
        """,
        (inicio.isoformat(), fim.isoformat(), valor_total_fatura, TOLERANCIA_VALOR, valor_total_fatura),
    ).fetchone()
    return row[0] if row else None


def vincular_pagamentos(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    vinculados = []

    faturas = conn.execute(
        "SELECT id_fatura, valor_total_fatura, data_vencimento FROM faturas_cartao"
    ).fetchall()

    for id_fatura, valor_total_fatura, data_vencimento in faturas:
        if valor_total_fatura is None or data_vencimento is None:
            continue

        ja_vinculada = conn.execute(
            "SELECT 1 FROM transacoes WHERE id_fatura = ? LIMIT 1", (id_fatura,)
        ).fetchone()
        if ja_vinculada:
            continue

        id_transacao = buscar_transacao_correspondente(
            conn, id_fatura, valor_total_fatura, data_vencimento
        )
        if id_transacao is None:
            continue

        conn.execute(
            "UPDATE transacoes SET categoria = ?, status = 'classificado', id_fatura = ? "
            "WHERE id_transacao = ?",
            (CATEGORIA_PAGAMENTO_FATURA, id_fatura, id_transacao),
        )
        conn.execute(
            "UPDATE faturas_cartao SET status_pagamento = 'paga' WHERE id_fatura = ?",
            (id_fatura,),
        )
        vinculados.append((id_fatura, id_transacao))

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
    for id_fatura, id_transacao in vinculados:
        print(f"fatura {id_fatura} <- transacao {id_transacao}")
