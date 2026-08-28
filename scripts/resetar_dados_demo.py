"""Apaga os dados de demonstração usados pra validar o sistema (extrato,
fatura, regras aprendidas, compromissos financeiros) — uso único, antes de
começar a popular o banco com dado real de produção.

Apaga: transacoes, lancamentos_fatura, faturas_cartao,
ocorrencias_conta_recorrente, contas_recorrentes, dividas, parcelamentos,
regras_classificacao (recriada a partir de db/seed_regras_classificacao.sql
logo em seguida — mantém as regras aprendidas em revisões reais anteriores,
que já foram persistidas nesse arquivo, e descarta qualquer regra criada
só durante os testes).

NÃO mexe em: plano_de_contas, cartoes, contas_bancarias (cadastros de
referência, não dado transacional).

Não faz backup sozinho — quem chama decide se quer copiar o banco antes
(recomendado, já que é destrutivo e não tem confirmação interativa).

Uso:
    python scripts/resetar_dados_demo.py [caminho_do_banco]
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "db" / "financeiro.db"
SEED_REGRAS_PATH = ROOT / "db" / "seed_regras_classificacao.sql"

TABELAS_PARA_LIMPAR = [
    "transacoes",
    "lancamentos_fatura",
    "faturas_cartao",
    "ocorrencias_conta_recorrente",
    "contas_recorrentes",
    "dividas",
    "parcelamentos",
    "regras_classificacao",
]


def resetar(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        contagens = {}
        for tabela in TABELAS_PARA_LIMPAR:
            contagens[tabela] = conn.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]
            conn.execute(f"DELETE FROM {tabela}")
            conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (tabela,))

        conn.executescript(SEED_REGRAS_PATH.read_text(encoding="utf-8"))
        contagens["regras_classificacao_recriadas"] = conn.execute(
            "SELECT COUNT(*) FROM regras_classificacao"
        ).fetchone()[0]

        conn.commit()
    finally:
        conn.close()
    return contagens


if __name__ == "__main__":
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB_PATH

    contagens = resetar(db_path)
    print(f"Banco resetado em {db_path}")
    for tabela in TABELAS_PARA_LIMPAR:
        print(f"  {tabela}: {contagens[tabela]} linha(s) apagada(s)")
    print(f"  regras_classificacao: {contagens['regras_classificacao_recriadas']} regra(s) recriada(s) do seed")
