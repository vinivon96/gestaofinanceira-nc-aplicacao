"""Cadastro de contas bancárias e cartões — dado cadastral de referência
(banco, agência/número da conta, final do cartão, dia de fechamento/
vencimento habituais), não movimentação. Usado pela tela /importar do
dashboard para exibir rótulos amigáveis e dar contexto na hora de importar
extratos/faturas.

Mesmo estilo de compromissos.py/revisar_manual.py: funções soltas recebendo
sqlite3.Connection, sem ORM. Nenhuma função aqui commita — quem chama decide
quando.
"""
import sqlite3


def listar_contas_bancarias(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        "SELECT id, banco, agencia, numero_conta, apelido, ativa FROM contas_bancarias "
        "ORDER BY banco, apelido"
    ).fetchall()


def criar_conta_bancaria(
    conn: sqlite3.Connection, banco: str, agencia: str, numero_conta: str, apelido: str
) -> int:
    cursor = conn.execute(
        "INSERT INTO contas_bancarias (banco, agencia, numero_conta, apelido) VALUES (?, ?, ?, ?)",
        (banco.strip(), agencia.strip() or None, numero_conta.strip() or None, apelido.strip() or None),
    )
    return cursor.lastrowid


def excluir_conta_bancaria(conn: sqlite3.Connection, id_: int) -> None:
    conn.execute("DELETE FROM contas_bancarias WHERE id = ?", (id_,))


def listar_cartoes(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        "SELECT id, banco, apelido, final_cartao, dia_fechamento, dia_vencimento, ativo FROM cartoes "
        "ORDER BY banco, apelido"
    ).fetchall()


def criar_cartao(
    conn: sqlite3.Connection, banco: str, apelido: str, final_cartao: str,
    dia_fechamento: int | None, dia_vencimento: int | None,
) -> int:
    cursor = conn.execute(
        "INSERT INTO cartoes (banco, apelido, final_cartao, dia_fechamento, dia_vencimento) "
        "VALUES (?, ?, ?, ?, ?)",
        (banco.strip(), apelido.strip() or None, final_cartao.strip(), dia_fechamento, dia_vencimento),
    )
    return cursor.lastrowid


def excluir_cartao(conn: sqlite3.Connection, id_: int) -> None:
    conn.execute("DELETE FROM cartoes WHERE id = ?", (id_,))


def nome_cartao(final_cartao: str | None, cartoes: dict[str, tuple[str, str | None]]) -> str:
    """Rótulo amigável pra um `cartao_final` (ex: '7111') a partir do cadastro
    — usado em telas que hoje só mostram os 4 últimos dígitos crus (fatura,
    parcelamentos). `cartoes` é {final_cartao: (banco, apelido)}. Sem
    cadastro correspondente, cai de volta pro final cru."""
    if not final_cartao:
        return "—"
    info = cartoes.get(final_cartao)
    if not info:
        return f"****{final_cartao}"
    banco, apelido = info
    rotulo = f"{banco} ****{final_cartao}"
    return f"{rotulo} ({apelido})" if apelido else rotulo


def mapa_cartoes_por_final(conn: sqlite3.Connection) -> dict[str, tuple[str, str | None]]:
    return {
        final_cartao: (banco, apelido)
        for _id, banco, apelido, final_cartao, _fech, _venc, _ativo in listar_cartoes(conn)
    }


def linhas_unificadas_cadastros(conn: sqlite3.Connection) -> list[dict]:
    """Combina listar_contas_bancarias e listar_cartoes num formato único de
    linha, pra tela /importar mostrar os dois cadastros de referência numa
    tabela só (mesmo racional do redesign de /compromissos — ver spec seção
    16). Não reimplementa nenhuma consulta: só reformata o que essas duas
    funções já devolvem. Formatação de exibição fica em dashboard.py."""
    linhas = []

    for id_, banco, agencia, numero_conta, apelido, _ativa in listar_contas_bancarias(conn):
        linhas.append({
            "tipo": "conta_bancaria",
            "id": id_,
            "banco": banco,
            "apelido": apelido,
            "detalhe": f"Ag. {agencia or '—'} / Conta {numero_conta or '—'}",
            "final_cartao": None,
            "fechamento": None,
            "vencimento": None,
        })

    for id_, banco, apelido, final_cartao, dia_fechamento, dia_vencimento, _ativo in listar_cartoes(conn):
        linhas.append({
            "tipo": "cartao",
            "id": id_,
            "banco": banco,
            "apelido": apelido,
            "detalhe": f"****{final_cartao}",
            "final_cartao": final_cartao,
            "fechamento": f"Dia {dia_fechamento}" if dia_fechamento else None,
            "vencimento": f"Dia {dia_vencimento}" if dia_vencimento else None,
        })

    return linhas
