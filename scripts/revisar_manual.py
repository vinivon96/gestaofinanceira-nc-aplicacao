"""Revisão manual dos itens pendentes de classificação (passo 7 do roadmap).

Lista os registros com `status = 'revisar_manual'` E `categoria IS NULL` (ou
seja, itens que nenhuma regra/heurística resolveu — não inclui itens que já
têm categoria mas foram sinalizados só pra confirmar a natureza, como a
heurística de pessoa física). Agrupa por texto idêntico de
contraparte/descrição — já que o mesmo padrão costuma repetir (ex: "PAYGO"
aparece várias vezes) — e pergunta, uma vez por grupo, qual categoria usar.

A categoria escolhida é aplicada a todos os itens do grupo e também vira uma
nova regra em `regras_classificacao` (padrao = texto do grupo, tabela_alvo =
a tabela de origem do grupo), pra que da próxima vez o motor de classificação
já resolva sozinho.

Uso:
    python scripts/revisar_manual.py [caminho_do_banco]

Modo não interativo (scriptável): respostas podem ser fornecidas via stdin,
uma por linha, na ordem em que os grupos aparecem (contraparte/descrição
alfabética dentro de cada tabela, transacoes antes de lancamentos_fatura).
"""
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "db" / "financeiro.db"


def carregar_categorias(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    return conn.execute(
        "SELECT codigo, categoria FROM plano_de_contas WHERE codigo != 'revisar_manual' ORDER BY categoria"
    ).fetchall()


def grupos_pendentes_transacoes(conn: sqlite3.Connection) -> list[tuple[str, list[tuple]]]:
    rows = conn.execute(
        "SELECT id_transacao, data, contraparte, valor FROM transacoes "
        "WHERE status = 'revisar_manual' AND categoria IS NULL ORDER BY contraparte, data"
    ).fetchall()
    grupos: dict[str, list[tuple]] = {}
    for id_transacao, data, contraparte, valor in rows:
        grupos.setdefault(contraparte, []).append((id_transacao, data, valor))
    return sorted(grupos.items())


def grupos_pendentes_lancamentos(conn: sqlite3.Connection) -> list[tuple[str, list[tuple]]]:
    rows = conn.execute(
        "SELECT id_lancamento, data_compra, descricao, valor FROM lancamentos_fatura "
        "WHERE status = 'revisar_manual' AND categoria IS NULL ORDER BY descricao, data_compra"
    ).fetchall()
    grupos: dict[str, list[tuple]] = {}
    for id_lancamento, data_compra, descricao, valor in rows:
        grupos.setdefault(descricao, []).append((id_lancamento, data_compra, valor))
    return sorted(grupos.items())


def escolher_categoria(padrao: str, itens: list[tuple], categorias: list[tuple[str, str]]) -> str | None:
    print(f"\nGrupo: '{padrao}' ({len(itens)} item(ns))")
    for _id, data_ref, valor in itens:
        print(f"    {data_ref}  R$ {valor:,.2f}".replace(",", "@").replace(".", ",").replace("@", "."))

    print("  Categorias disponíveis:")
    for i, (codigo, nome) in enumerate(categorias, start=1):
        print(f"    {i}. {nome} ({codigo})")
    print("    0. Pular (deixar para revisar depois)")

    while True:
        escolha = input("  Escolha o número da categoria: ").strip()
        if escolha == "0":
            return None
        if escolha.isdigit() and 1 <= int(escolha) <= len(categorias):
            return categorias[int(escolha) - 1][0]
        print("  Opção inválida, tente de novo.")


def slugificar(texto: str) -> str:
    """'Assinatura de Software!' -> 'assinatura_de_software'"""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", sem_acento).strip("_").lower()
    return slug or "categoria"


def criar_categoria(conn: sqlite3.Connection, nome: str, tipo: str) -> str:
    """Cria uma categoria nova em `plano_de_contas` a partir de um nome livre
    digitado na interface (dashboard, ponto 1 da reclassificação inline).
    `tipo` deve ser 'receita' ou 'despesa' — inferido pela origem do item que
    motivou a criação (entrada -> receita, saída -> despesa). Gera um código
    (slug) único, adicionando sufixo numérico se já existir."""
    base = slugificar(nome)
    codigo = base
    existentes = {linha[0] for linha in conn.execute("SELECT codigo FROM plano_de_contas").fetchall()}
    sufixo = 2
    while codigo in existentes:
        codigo = f"{base}_{sufixo}"
        sufixo += 1
    conn.execute(
        "INSERT INTO plano_de_contas (codigo, categoria, tipo) VALUES (?, ?, ?)",
        (codigo, nome.strip(), tipo),
    )
    return codigo


def editar_categoria(conn: sqlite3.Connection, codigo: str, nome: str, tipo: str) -> None:
    """Atualiza nome/tipo de uma categoria já existente — o `codigo` (slug)
    não muda, já que é referenciado como chave estrangeira por transacoes,
    lancamentos_fatura, regras_classificacao e os compromissos financeiros."""
    conn.execute(
        "UPDATE plano_de_contas SET categoria = ?, tipo = ? WHERE codigo = ?",
        (nome.strip(), tipo, codigo),
    )


def criar_regra(conn: sqlite3.Connection, padrao: str, categoria_destino: str, tabela_alvo: str) -> None:
    prioridade = conn.execute(
        "SELECT COALESCE(MAX(prioridade), 0) + 1 FROM regras_classificacao"
    ).fetchone()[0]
    conn.execute(
        "INSERT OR IGNORE INTO regras_classificacao (padrao, categoria_destino, prioridade, tabela_alvo) "
        "VALUES (?, ?, ?, ?)",
        (padrao, categoria_destino, prioridade, tabela_alvo),
    )


def aplicar_transacoes(conn: sqlite3.Connection, padrao: str, categoria_destino: str) -> int:
    cursor = conn.execute(
        "UPDATE transacoes SET categoria = ?, status = 'classificado' WHERE contraparte = ? AND categoria IS NULL",
        (categoria_destino, padrao),
    )
    criar_regra(conn, padrao, categoria_destino, "transacoes")
    return cursor.rowcount


def aplicar_lancamentos(conn: sqlite3.Connection, padrao: str, categoria_destino: str) -> int:
    cursor = conn.execute(
        "UPDATE lancamentos_fatura SET categoria = ?, status = 'classificado' "
        "WHERE descricao = ? AND categoria IS NULL",
        (categoria_destino, padrao),
    )
    criar_regra(conn, padrao, categoria_destino, "lancamentos_fatura")
    return cursor.rowcount


def revisar(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        categorias = carregar_categorias(conn)

        grupos_transacoes = grupos_pendentes_transacoes(conn)
        grupos_lancamentos = grupos_pendentes_lancamentos(conn)

        if not grupos_transacoes and not grupos_lancamentos:
            print("Nenhum item pendente de revisão manual.")
            return

        if grupos_transacoes:
            print("\n### transacoes ###")
        for contraparte, itens in grupos_transacoes:
            categoria_destino = escolher_categoria(contraparte, itens, categorias)
            if categoria_destino is None:
                print("  Pulado.")
                continue
            aplicar_transacoes(conn, contraparte, categoria_destino)
            conn.commit()
            print(f"  Aplicado '{categoria_destino}' a {len(itens)} transação(ões) e regra criada.")

        if grupos_lancamentos:
            print("\n### lancamentos_fatura ###")
        for descricao, itens in grupos_lancamentos:
            categoria_destino = escolher_categoria(descricao, itens, categorias)
            if categoria_destino is None:
                print("  Pulado.")
                continue
            aplicar_lancamentos(conn, descricao, categoria_destino)
            conn.commit()
            print(f"  Aplicado '{categoria_destino}' a {len(itens)} lançamento(s) e regra criada.")
    finally:
        conn.close()


if __name__ == "__main__":
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB_PATH
    revisar(db_path)
