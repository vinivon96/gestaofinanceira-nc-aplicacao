"""Relatório de entradas/saídas por período (passo 6 do roadmap, spec seção 6).

Agrega, por um período configurável (mês ou semana, ou intervalo explícito):
    1. Entradas por canal (origem_receita) — extrato Inter
    2. Saídas por categoria (extrato Inter), excluindo 'pagamento_fatura' pra
       não duplicar o gasto já detalhado dentro da fatura
    3. Saídas por categoria dentro da fatura de cartão (lancamentos_fatura)
    4. Total combinado por categoria (soma de 2 + 3 — visão completa de saída)
    5. Saídas por cartão físico dentro da fatura (centro de custo)
    6. Faturas em aberto (limite/vencimento), sem filtro de período

Período das visões de fatura de cartão (3, 4 e 5): uma fatura conta inteira
no mês em que ela FECHA (`faturas_cartao.data_fechamento`), não no mês
calendário de cada compra individual — o ciclo da fatura (ex: 16/05 a 15/06)
não bate com o mês corrido, então filtrar por `data_compra` fatiaria o total
de uma mesma fatura entre dois meses.

Só via query + impressão no terminal, como pede o passo 6 — sem dashboard
visual ainda (isso é o passo 8).

Uso:
    python scripts/relatorio.py --mes 2026-06
    python scripts/relatorio.py --semana 2026-06-16
    python scripts/relatorio.py --inicio 2026-06-01 --fim 2026-06-30
    # opcional: caminho do banco por --db (default db/financeiro.db)
"""
import argparse
import sqlite3
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "db" / "financeiro.db"

CATEGORIA_PAGAMENTO_FATURA = "pagamento_fatura"


def periodo_do_mes(mes: str) -> tuple[str, str]:
    ano, mes_num = (int(p) for p in mes.split("-"))
    inicio = date(ano, mes_num, 1)
    fim = date(ano + 1, 1, 1) - timedelta(days=1) if mes_num == 12 else date(ano, mes_num + 1, 1) - timedelta(days=1)
    return inicio.isoformat(), fim.isoformat()


def periodo_da_semana(qualquer_dia: str) -> tuple[str, str]:
    dia = date.fromisoformat(qualquer_dia)
    inicio = dia - timedelta(days=dia.weekday())  # segunda-feira
    fim = inicio + timedelta(days=6)              # domingo
    return inicio.isoformat(), fim.isoformat()


def nomes_categorias(conn: sqlite3.Connection) -> dict[str, str]:
    return dict(conn.execute("SELECT codigo, categoria FROM plano_de_contas").fetchall())


def meses_disponiveis(conn: sqlite3.Connection, limite: int = 12) -> list[str]:
    """Últimos `limite` meses (YYYY-MM) com algum dado em transacoes ou
    lancamentos_fatura, em ordem crescente (mais antigo primeiro)."""
    linhas = conn.execute(
        """
        SELECT mes FROM (
            SELECT strftime('%Y-%m', data) AS mes FROM transacoes
            UNION
            SELECT strftime('%Y-%m', data_compra) AS mes FROM lancamentos_fatura
        )
        WHERE mes IS NOT NULL
        ORDER BY mes DESC
        LIMIT ?
        """,
        (limite,),
    ).fetchall()
    return sorted(mes for (mes,) in linhas)


def evolucao_mensal(conn: sqlite3.Connection, meses: list[str]) -> list[dict]:
    """Total de entradas e saídas (extrato + fatura combinados) por mês —
    reaproveita as mesmas funções de agregação por período, só chamando uma
    vez por mês em vez de duplicar a lógica de soma."""
    evolucao = []
    for mes in meses:
        inicio, fim = periodo_do_mes(mes)
        total_entradas = sum(valor for _origem, valor in entradas_por_canal(conn, inicio, fim))
        total_saidas = sum(valor for _cat, valor in saidas_extrato_por_categoria(conn, inicio, fim))
        total_saidas += sum(valor for _cat, valor in saidas_fatura_por_categoria(conn, inicio, fim))
        evolucao.append({"mes": mes, "entradas": total_entradas, "saidas": total_saidas})
    return evolucao


def entradas_por_canal(conn: sqlite3.Connection, inicio: str, fim: str) -> list[tuple[str | None, float]]:
    return conn.execute(
        """
        SELECT origem_receita, SUM(valor)
        FROM transacoes
        WHERE tipo = 'entrada' AND data BETWEEN ? AND ?
        GROUP BY origem_receita
        ORDER BY SUM(valor) DESC
        """,
        (inicio, fim),
    ).fetchall()


def saidas_extrato_por_categoria(conn: sqlite3.Connection, inicio: str, fim: str) -> list[tuple[str | None, float]]:
    return conn.execute(
        """
        SELECT categoria, SUM(valor)
        FROM transacoes
        WHERE tipo = 'saida'
          AND (categoria IS NULL OR categoria != ?)
          AND data BETWEEN ? AND ?
        GROUP BY categoria
        ORDER BY SUM(valor) DESC
        """,
        (CATEGORIA_PAGAMENTO_FATURA, inicio, fim),
    ).fetchall()


def saidas_fatura_por_categoria(conn: sqlite3.Connection, inicio: str, fim: str) -> list[tuple[str | None, float]]:
    """Uma fatura pertence inteira ao mês em que fecha (`faturas_cartao.data_fechamento`),
    não ao mês calendário de cada compra — senão o total de uma fatura fica
    fatiado entre dois meses, já que o ciclo (ex: 16/05 a 15/06) não bate com
    o mês corrido. Por isso o filtro de período é pela fatura, não pela
    `data_compra` de cada lançamento."""
    return conn.execute(
        """
        SELECT lf.categoria, SUM(lf.valor)
        FROM lancamentos_fatura lf
        JOIN faturas_cartao fc ON fc.id_fatura = lf.id_fatura
        WHERE fc.data_fechamento BETWEEN ? AND ?
        GROUP BY lf.categoria
        ORDER BY SUM(lf.valor) DESC
        """,
        (inicio, fim),
    ).fetchall()


def entradas_detalhe(conn: sqlite3.Connection, inicio: str, fim: str, origem_receita: str | None) -> list[tuple]:
    """Transações individuais por trás de uma linha de `entradas_por_canal`."""
    clausula = "origem_receita IS NULL" if origem_receita is None else "origem_receita = ?"
    params: tuple = (inicio, fim) if origem_receita is None else (origem_receita, inicio, fim)
    return conn.execute(
        f"""
        SELECT id_transacao, data, contraparte, valor, categoria
        FROM transacoes
        WHERE tipo = 'entrada' AND {clausula} AND data BETWEEN ? AND ?
        ORDER BY data
        """,
        params,
    ).fetchall()


def saidas_extrato_detalhe(conn: sqlite3.Connection, inicio: str, fim: str, categoria: str | None) -> list[tuple]:
    """Transações individuais por trás de uma linha de `saidas_extrato_por_categoria`."""
    clausula = "categoria IS NULL" if categoria is None else "categoria = ?"
    params: tuple = (inicio, fim) if categoria is None else (categoria, inicio, fim)
    return conn.execute(
        f"""
        SELECT id_transacao, data, contraparte, valor, categoria
        FROM transacoes
        WHERE tipo = 'saida' AND {clausula} AND data BETWEEN ? AND ?
        ORDER BY data
        """,
        params,
    ).fetchall()


def saidas_fatura_detalhe(conn: sqlite3.Connection, inicio: str, fim: str, categoria: str | None) -> list[tuple]:
    """Lançamentos individuais por trás de uma linha de `saidas_fatura_por_categoria`
    — mesmo critério de período (fatura inteira no mês em que fecha)."""
    clausula = "lf.categoria IS NULL" if categoria is None else "lf.categoria = ?"
    params: tuple = (inicio, fim) if categoria is None else (categoria, inicio, fim)
    return conn.execute(
        f"""
        SELECT lf.id_lancamento, lf.data_compra, lf.descricao, lf.valor, lf.categoria
        FROM lancamentos_fatura lf
        JOIN faturas_cartao fc ON fc.id_fatura = lf.id_fatura
        WHERE {clausula} AND fc.data_fechamento BETWEEN ? AND ?
        ORDER BY lf.data_compra
        """,
        params,
    ).fetchall()


def saidas_por_cartao(conn: sqlite3.Connection, inicio: str, fim: str) -> list[tuple[str | None, str | None, float]]:
    """Mesmo critério de período de `saidas_fatura_por_categoria` (fatura
    inteira no mês em que fecha) — é a mesma fonte de dado, então fatiar por
    `data_compra` aqui teria o mesmo problema."""
    return conn.execute(
        """
        SELECT lf.cartao_final, lf.categoria, SUM(lf.valor)
        FROM lancamentos_fatura lf
        JOIN faturas_cartao fc ON fc.id_fatura = lf.id_fatura
        WHERE fc.data_fechamento BETWEEN ? AND ?
        GROUP BY lf.cartao_final, lf.categoria
        ORDER BY lf.cartao_final, SUM(lf.valor) DESC
        """,
        (inicio, fim),
    ).fetchall()


def saidas_por_contraparte(conn: sqlite3.Connection, inicio: str, fim: str) -> list[tuple[str, float]]:
    """Ranking de saídas por contraparte/descrição EXATA (não por categoria)
    — combina extrato (excluindo pagamento_fatura, mesmo critério de
    `saidas_extrato_por_categoria`) e fatura (mesmo critério de período de
    `saidas_fatura_por_categoria`: fatura conta no mês em que fecha)."""
    linhas_extrato = conn.execute(
        """
        SELECT contraparte, SUM(valor)
        FROM transacoes
        WHERE tipo = 'saida'
          AND (categoria IS NULL OR categoria != ?)
          AND data BETWEEN ? AND ?
        GROUP BY contraparte
        """,
        (CATEGORIA_PAGAMENTO_FATURA, inicio, fim),
    ).fetchall()
    linhas_fatura = conn.execute(
        """
        SELECT lf.descricao, SUM(lf.valor)
        FROM lancamentos_fatura lf
        JOIN faturas_cartao fc ON fc.id_fatura = lf.id_fatura
        WHERE fc.data_fechamento BETWEEN ? AND ?
        GROUP BY lf.descricao
        """,
        (inicio, fim),
    ).fetchall()

    totais: dict[str, float] = {}
    for contraparte, total in linhas_extrato:
        totais[contraparte] = totais.get(contraparte, 0.0) + total
    for descricao, total in linhas_fatura:
        totais[descricao] = totais.get(descricao, 0.0) + total
    return sorted(totais.items(), key=lambda item: item[1], reverse=True)


def saidas_por_contraparte_detalhe(conn: sqlite3.Connection, inicio: str, fim: str, contraparte: str) -> list[tuple]:
    """Lançamentos individuais por trás de uma linha de `saidas_por_contraparte`."""
    linhas = conn.execute(
        """
        SELECT id_transacao, data, contraparte, valor, categoria, 'saida' AS tipo_transacao, 'extrato' AS origem
        FROM transacoes
        WHERE tipo = 'saida'
          AND (categoria IS NULL OR categoria != ?)
          AND contraparte = ?
          AND data BETWEEN ? AND ?
        """,
        (CATEGORIA_PAGAMENTO_FATURA, contraparte, inicio, fim),
    ).fetchall()
    linhas_fatura = conn.execute(
        """
        SELECT lf.id_lancamento, lf.data_compra, lf.descricao, lf.valor, lf.categoria,
               'saida' AS tipo_transacao, 'fatura' AS origem
        FROM lancamentos_fatura lf
        JOIN faturas_cartao fc ON fc.id_fatura = lf.id_fatura
        WHERE lf.descricao = ? AND fc.data_fechamento BETWEEN ? AND ?
        """,
        (contraparte, inicio, fim),
    ).fetchall()
    return sorted([*linhas, *linhas_fatura], key=lambda linha: linha[1])


def escapar_like(termo: str) -> str:
    """Escapa os curingas do LIKE (%, _) num termo de busca digitado pelo
    usuário, pra que sejam tratados como texto literal."""
    return termo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def buscar_por_termo(conn: sqlite3.Connection, inicio: str, fim: str, termo: str) -> list[tuple]:
    """Busca por contraparte/descrição (contém, case-insensitive) dentro do
    período selecionado, combinando transacoes (entradas e saídas) e
    lancamentos_fatura — mesmo critério de período das demais visões (fatura
    conta no mês em que fecha)."""
    termo_like = f"%{escapar_like(termo)}%"
    linhas = conn.execute(
        """
        SELECT id_transacao, data, contraparte, valor, categoria, tipo AS tipo_transacao, 'extrato' AS origem
        FROM transacoes
        WHERE contraparte LIKE ? ESCAPE '\\' AND data BETWEEN ? AND ?
        """,
        (termo_like, inicio, fim),
    ).fetchall()
    linhas_fatura = conn.execute(
        """
        SELECT lf.id_lancamento, lf.data_compra, lf.descricao, lf.valor, lf.categoria,
               'saida' AS tipo_transacao, 'fatura' AS origem
        FROM lancamentos_fatura lf
        JOIN faturas_cartao fc ON fc.id_fatura = lf.id_fatura
        WHERE lf.descricao LIKE ? ESCAPE '\\' AND fc.data_fechamento BETWEEN ? AND ?
        """,
        (termo_like, inicio, fim),
    ).fetchall()
    return sorted([*linhas, *linhas_fatura], key=lambda linha: linha[1])


def faturas_em_aberto(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        """
        SELECT id_fatura, data_vencimento, limite_total, valor_total_fatura,
               limite_disponivel, compras_parceladas_futuras
        FROM faturas_cartao
        WHERE status_pagamento = 'aberta'
        ORDER BY data_vencimento
        """
    ).fetchall()


def combinar_saidas(
    extrato: list[tuple[str | None, float]], fatura: list[tuple[str | None, float]]
) -> list[tuple[str | None, float]]:
    totais: dict[str | None, float] = {}
    for categoria, total in extrato:
        totais[categoria] = totais.get(categoria, 0.0) + total
    for categoria, total in fatura:
        totais[categoria] = totais.get(categoria, 0.0) + total
    return sorted(totais.items(), key=lambda item: item[1], reverse=True)


def fmt_moeda(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


CATEGORIA_PENDENTE_ROTULO = "Sem categoria / Revisão pendente"


def nome_categoria(codigo: str | None, categorias: dict[str, str]) -> str:
    if codigo is None:
        return CATEGORIA_PENDENTE_ROTULO
    return categorias.get(codigo, codigo)


def imprimir_tabela(titulo: str, linhas: list[tuple], categorias: dict[str, str], indice_categoria: int = 0) -> None:
    print(f"\n== {titulo} ==")
    if not linhas:
        print("  (nenhum registro no período)")
        return
    total_geral = 0.0
    for linha in linhas:
        *chaves, valor = linha
        chaves[indice_categoria] = nome_categoria(chaves[indice_categoria], categorias)
        rotulo = " / ".join(str(c) if c is not None else "(sem valor)" for c in chaves)
        print(f"  {rotulo:<45} {fmt_moeda(valor):>18}")
        total_geral += valor
    print(f"  {'TOTAL':<45} {fmt_moeda(total_geral):>18}")


def gerar_relatorio(db_path: Path, inicio: str, fim: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        categorias = nomes_categorias(conn)

        print(f"Relatório do período {inicio} a {fim}")

        imprimir_tabela(
            "Entradas por canal (extrato Inter)",
            [(origem or "nao_receita", valor) for origem, valor in entradas_por_canal(conn, inicio, fim)],
            {},
        )

        saidas_extrato = saidas_extrato_por_categoria(conn, inicio, fim)
        imprimir_tabela("Saídas por categoria (extrato Inter, sem pagamento de fatura)", saidas_extrato, categorias)

        saidas_fatura = saidas_fatura_por_categoria(conn, inicio, fim)
        imprimir_tabela("Saídas por categoria (dentro da fatura de cartão)", saidas_fatura, categorias)

        imprimir_tabela(
            "Saídas por categoria — visão combinada (extrato + fatura)",
            combinar_saidas(saidas_extrato, saidas_fatura),
            categorias,
        )

        imprimir_tabela(
            "Saídas por cartão físico (dentro da fatura)",
            saidas_por_cartao(conn, inicio, fim),
            categorias,
            indice_categoria=1,
        )

        print("\n== Faturas em aberto (limite/vencimento) ==")
        abertas = faturas_em_aberto(conn)
        if not abertas:
            print("  (nenhuma fatura em aberto)")
        for id_fatura, venc, limite_total, valor_total, limite_disp, parceladas_futuras in abertas:
            print(f"  {id_fatura}: vence em {venc}, total {fmt_moeda(valor_total)}, "
                  f"limite {fmt_moeda(limite_total)}, disponível {fmt_moeda(limite_disp)}, "
                  f"parceladas futuras {fmt_moeda(parceladas_futuras)}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Relatório de entradas/saídas por período")
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--mes", help="Mês no formato YYYY-MM")
    grupo.add_argument("--semana", help="Qualquer data (YYYY-MM-DD) dentro da semana desejada (seg a dom)")
    grupo.add_argument("--inicio", help="Data de início do período (YYYY-MM-DD), usar com --fim")
    parser.add_argument("--fim", help="Data de fim do período (YYYY-MM-DD), usar com --inicio")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Caminho do banco (default db/financeiro.db)")
    args = parser.parse_args()

    if args.mes:
        inicio, fim = periodo_do_mes(args.mes)
    elif args.semana:
        inicio, fim = periodo_da_semana(args.semana)
    else:
        if not args.fim:
            parser.error("--inicio requer --fim")
        inicio, fim = args.inicio, args.fim

    gerar_relatorio(Path(args.db), inicio, fim)


if __name__ == "__main__":
    main()
