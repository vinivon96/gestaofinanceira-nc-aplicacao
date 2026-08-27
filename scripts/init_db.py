"""Cria/atualiza o banco SQLite a partir de db/schema.sql e popula o plano de
contas e as regras de classificação iniciais.

Uso: python scripts/init_db.py [caminho_do_banco]
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "db" / "schema.sql"
SEED_PATHS = [
    ROOT / "db" / "seed_plano_de_contas.sql",
    ROOT / "db" / "seed_regras_classificacao.sql",
]
DEFAULT_DB_PATH = ROOT / "db" / "financeiro.db"


def _garantir_coluna(conn: sqlite3.Connection, tabela: str, coluna: str, definicao_sql: str) -> None:
    """Adiciona uma coluna a uma tabela já existente, se ainda não existir.
    `CREATE TABLE IF NOT EXISTS` não altera tabelas já criadas, então bancos
    antigos (como o de produção) precisam desse shim pra ganhar colunas
    novas sem perder dado — rodar de novo é seguro (coluna já existe, pula)."""
    colunas = {linha[1] for linha in conn.execute(f"PRAGMA table_info({tabela})")}
    if coluna not in colunas:
        conn.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {definicao_sql}")


def init_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _garantir_coluna(conn, "transacoes", "id_divida", "TEXT REFERENCES dividas(id_divida)")
        for seed_path in SEED_PATHS:
            conn.executescript(seed_path.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB_PATH
    init_db(db_path)
    print(f"Banco inicializado em {db_path}")
