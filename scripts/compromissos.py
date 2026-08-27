"""Consultas e cadastro para o painel de compromissos financeiros (contas
recorrentes, parcelamentos e dívidas) — ver spec, seção 15.

Mesmo estilo de relatorio.py/revisar_manual.py: funções soltas recebendo
sqlite3.Connection, sem ORM. Nenhuma função aqui commita — quem chama decide
quando (mesma convenção de criar_categoria/criar_regra em revisar_manual.py).

Uso via dashboard.py (rotas /compromissos, /contas_recorrentes, /parcelamentos,
/dividas) e via scripts/vincular_pagamento_divida.py.
"""
import sqlite3
from calendar import monthrange
from datetime import date

from revisar_manual import slugificar


def contas_recorrentes_do_mes(conn: sqlite3.Connection, competencia: str) -> list[tuple]:
    """Todas as contas ativas, com o status calculado da competência (mês
    'YYYY-MM'): 'paga'/'pulada' se existe ocorrência registrada, senão
    'pendente' (nenhuma linha precisa existir pra um mês estar pendente)."""
    return conn.execute(
        """
        SELECT c.id, c.nome, c.categoria, c.valor_esperado, c.dia_vencimento,
               COALESCE(o.status, 'pendente'), o.valor_pago, o.data_pagamento
        FROM contas_recorrentes c
        LEFT JOIN ocorrencias_conta_recorrente o
          ON o.conta_recorrente_id = c.id AND o.competencia = ?
        WHERE c.ativa = 1
        ORDER BY c.dia_vencimento, c.nome
        """,
        (competencia,),
    ).fetchall()


def data_vencimento_no_mes(competencia: str, dia_vencimento: int) -> str:
    """Data efetiva do vencimento numa competência 'YYYY-MM', ajustando dias
    que não existem no mês (ex: dia 31 em fevereiro -> último dia do mês)."""
    ano, mes = int(competencia[:4]), int(competencia[5:7])
    ultimo_dia = monthrange(ano, mes)[1]
    dia = min(dia_vencimento, ultimo_dia)
    return f"{ano:04d}-{mes:02d}-{dia:02d}"


def diferenca_em_meses(competencia_a: str, competencia_b: str) -> int:
    """Quantos meses vão de `competencia_b` até `competencia_a` (ambas 'YYYY-MM')."""
    ano_a, mes_a = int(competencia_a[:4]), int(competencia_a[5:7])
    ano_b, mes_b = int(competencia_b[:4]), int(competencia_b[5:7])
    return (ano_a - ano_b) * 12 + (mes_a - mes_b)


def somar_meses_competencia(competencia: str, quantidade: int) -> str:
    """Soma `quantidade` meses a uma competência 'YYYY-MM'."""
    ano, mes = int(competencia[:4]), int(competencia[5:7])
    total = (mes - 1) + quantidade
    ano += total // 12
    mes = total % 12 + 1
    return f"{ano:04d}-{mes:02d}"


def _parcela_projetada(
    historico: list[tuple[str, int, str | None]], parcela_total: int, competencia_alvo: str
) -> tuple[int, bool, str | None] | None:
    """`historico`: lista de (competencia_conhecida, parcela_atual_naquela_competencia,
    data_vencimento_da_fatura), ordenada por competencia crescente — o estado real do
    parcelamento em cada mês em que ele apareceu numa fatura importada (ou, pra cadastro
    manual, um único ponto de referência: hoje).

    Projeta o número da parcela em `competencia_alvo`, assumindo cadência mensal (1 parcela
    por mês) a partir do ponto conhecido mais recente que não seja posterior ao alvo.
    Devolve (parcela_na_competencia, projetado, termina_em) ou None se o parcelamento ainda
    não existia, ou já tinha terminado, naquele mês."""
    candidatos = [ponto for ponto in historico if ponto[0] <= competencia_alvo]
    if not candidatos:
        return None

    competencia_ref, parcela_atual, data_vencimento = candidatos[-1]

    if competencia_ref == competencia_alvo:
        parcela_na_competencia, projetado = parcela_atual, False
    else:
        if parcela_atual >= parcela_total:
            return None  # já tinha terminado antes desse mês
        diferenca = diferenca_em_meses(competencia_alvo, competencia_ref)
        meses_para_terminar = parcela_total - parcela_atual
        if diferenca > meses_para_terminar:
            return None  # já teria terminado antes desse mês
        parcela_na_competencia = parcela_atual + diferenca
        projetado = True

    termina_em = None
    if data_vencimento:
        meses_restantes = max(parcela_total - parcela_atual, 0)
        termina_em = somar_meses_competencia(competencia_ref, meses_restantes)

    return parcela_na_competencia, projetado, termina_em


def parcelamentos_em_andamento(conn: sqlite3.Connection, competencia: str) -> list[dict]:
    """Combina (em Python, mesmo espírito de combinar_saidas em relatorio.py)
    parcelamentos ainda em aberto vindos de duas fontes: lançamentos já
    importados de fatura (agrupados pela compra original) e cadastros
    manuais. Cada item: origem, descricao, cartao_final, parcela_atual,
    parcela_total, valor_parcela, restante, termina_em, projetado.

    A parcela mostrada é a da competência selecionada: se já existe uma fatura
    importada que fechou exatamente nesse mês, usa o valor real; senão projeta
    a partir do último ponto conhecido (cadência mensal), marcando `projetado`
    — isso é o que faz o filtro de mês da tela /compromissos também mexer
    nessa seção (antes ficava sempre travada no estado mais recente
    importado, ver spec seção 15)."""
    itens = []

    historico_por_compra: dict[tuple, list[tuple[str, int, str | None]]] = {}
    parcela_total_por_compra: dict[tuple, int] = {}
    descricao_cartao_por_compra: dict[tuple, tuple[str, str | None]] = {}

    linhas_fatura = conn.execute(
        """
        SELECT lf.cartao_final, lf.data_compra, lf.descricao, lf.valor, lf.parcela_atual,
               lf.parcela_total, strftime('%Y-%m', fc.data_fechamento), fc.data_vencimento
        FROM lancamentos_fatura lf
        JOIN faturas_cartao fc ON fc.id_fatura = lf.id_fatura
        WHERE lf.parcela_total IS NOT NULL
        ORDER BY fc.data_fechamento
        """
    ).fetchall()
    for cartao_final, data_compra, descricao, valor, parcela_atual, parcela_total, competencia_fatura, data_vencimento in (
        linhas_fatura
    ):
        chave = (cartao_final, data_compra, descricao, valor, parcela_total)
        historico_por_compra.setdefault(chave, []).append((competencia_fatura, parcela_atual, data_vencimento))
        parcela_total_por_compra[chave] = parcela_total
        descricao_cartao_por_compra[chave] = (descricao, cartao_final)

    for chave, historico in historico_por_compra.items():
        parcela_total = parcela_total_por_compra[chave]
        descricao, cartao_final = descricao_cartao_por_compra[chave]
        resultado = _parcela_projetada(historico, parcela_total, competencia)
        if resultado is None:
            continue
        parcela_na_competencia, projetado, termina_em = resultado
        valor_parcela = chave[3]
        itens.append({
            "origem": "fatura",
            "id": None,
            "descricao": descricao,
            "cartao_final": cartao_final,
            "parcela_atual": parcela_na_competencia,
            "parcela_total": parcela_total,
            "valor_parcela": valor_parcela,
            "restante": valor_parcela * (parcela_total - parcela_na_competencia),
            "termina_em": termina_em,
            "projetado": projetado,
        })

    hoje = date.today().strftime("%Y-%m")
    for id_, descricao, valor_parcela, parcela_atual, parcela_total, cartao_final in conn.execute(
        """
        SELECT id, descricao, valor_parcela, parcela_atual, parcela_total, cartao_final
        FROM parcelamentos
        WHERE ativo = 1 AND parcela_atual <= parcela_total
        ORDER BY descricao
        """
    ).fetchall():
        resultado = _parcela_projetada([(hoje, parcela_atual, None)], parcela_total, competencia)
        if resultado is None:
            continue
        parcela_na_competencia, projetado, termina_em = resultado
        itens.append({
            "origem": "manual",
            "id": id_,
            "descricao": descricao,
            "cartao_final": cartao_final,
            "parcela_atual": parcela_na_competencia,
            "parcela_total": parcela_total,
            "valor_parcela": valor_parcela,
            "restante": valor_parcela * (parcela_total - parcela_na_competencia),
            "termina_em": termina_em,
            "projetado": projetado,
        })

    itens.sort(key=lambda item: item["descricao"])
    return itens


def dividas_em_aberto(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        """
        SELECT id_divida, credor, descricao, categoria, valor_parcela,
               parcelas_restantes, data_vencimento_proxima
        FROM dividas
        WHERE status = 'aberta'
        ORDER BY data_vencimento_proxima IS NULL, data_vencimento_proxima
        """
    ).fetchall()


def linhas_unificadas_compromissos(conn: sqlite3.Connection, competencia: str) -> list[dict]:
    """Combina contas_recorrentes_do_mes, parcelamentos_em_andamento e
    dividas_em_aberto num formato único de linha, pra tela /compromissos
    mostrar tudo numa tabela só (ver spec seção 15/16 — redesign). Não
    reimplementa nenhuma lógica de negócio (projeção de parcela, cálculo de
    status): só reformata o que essas três funções já devolvem em campos
    comuns. Formatação de exibição (categoria_nome, valor_fmt, cartao_rotulo,
    status_rotulo) fica em dashboard.py, mesmo padrão usado pelas outras
    views do app.

    Campos de cada linha: tipo, id, nome (exibição), busca (texto usado pelo
    filtro — pode incluir mais que `nome`, ex: dívida busca em credor+descrição),
    categoria (código ou None), cartao_final (código ou None), parcela (texto
    ou None), parcela_total (int ou None — só parcelamentos, usado pelo
    filtro "Total de parcelas"), vencimento (texto ou None), vencimento_bruto
    (data ISO ou None — só dívida, usado pra preencher o form de "Salvar"),
    valor (numérico), status (código), valor_pago (só recorrente)."""
    linhas = []

    for id_, nome, categoria, valor_esperado, dia_vencimento, status, valor_pago, _data_pagamento in (
        contas_recorrentes_do_mes(conn, competencia)
    ):
        linhas.append({
            "tipo": "recorrente",
            "id": id_,
            "nome": nome,
            "busca": nome,
            "categoria": categoria,
            "cartao_final": None,
            "parcela": None,
            "parcela_total": None,
            "vencimento": f"Todo dia {dia_vencimento}",
            "vencimento_bruto": None,
            "valor": valor_esperado,
            "valor_pago": valor_pago,
            "status": status,
        })

    for p in parcelamentos_em_andamento(conn, competencia):
        linhas.append({
            "tipo": "parcelamento",
            "id": p["id"],
            "nome": p["descricao"],
            "busca": p["descricao"],
            "categoria": None,
            "cartao_final": p["cartao_final"],
            "parcela": f"{p['parcela_atual']}/{p['parcela_total']}",
            "parcela_total": p["parcela_total"],
            "vencimento": p["termina_em"],
            "vencimento_bruto": None,
            "valor": p["valor_parcela"],
            "status": "projetado" if p["projetado"] else "aberta",
        })

    for id_divida, credor, descricao, categoria, valor_parcela, parcelas_restantes, data_vencimento_proxima in (
        dividas_em_aberto(conn)
    ):
        linhas.append({
            "tipo": "divida",
            "id": id_divida,
            "nome": f"{credor} — {descricao}" if descricao else credor,
            "busca": f"{credor} {descricao or ''}".strip(),
            "categoria": categoria,
            "cartao_final": None,
            "parcela": f"{parcelas_restantes} restante(s)",
            "parcela_total": None,
            "vencimento": data_vencimento_proxima,
            "vencimento_bruto": data_vencimento_proxima,
            "valor": valor_parcela,
            "status": "aberta",
        })

    return linhas


def criar_conta_recorrente(
    conn: sqlite3.Connection, nome: str, categoria: str | None, valor_esperado: float, dia_vencimento: int,
    observacao: str,
) -> int:
    cursor = conn.execute(
        "INSERT INTO contas_recorrentes (nome, categoria, valor_esperado, dia_vencimento, observacao) "
        "VALUES (?, ?, ?, ?, ?)",
        (nome.strip(), categoria or None, valor_esperado, dia_vencimento, observacao.strip() or None),
    )
    return cursor.lastrowid


def marcar_ocorrencia(
    conn: sqlite3.Connection, conta_recorrente_id: int, competencia: str, status: str, valor_pago: float,
    data_pagamento: str,
) -> None:
    conn.execute(
        """
        INSERT INTO ocorrencias_conta_recorrente
            (conta_recorrente_id, competencia, status, valor_pago, data_pagamento)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(conta_recorrente_id, competencia) DO UPDATE SET
            status = excluded.status, valor_pago = excluded.valor_pago, data_pagamento = excluded.data_pagamento
        """,
        (conta_recorrente_id, competencia, status, valor_pago, data_pagamento),
    )


def desmarcar_ocorrencia(conn: sqlite3.Connection, conta_recorrente_id: int, competencia: str) -> None:
    conn.execute(
        "DELETE FROM ocorrencias_conta_recorrente WHERE conta_recorrente_id = ? AND competencia = ?",
        (conta_recorrente_id, competencia),
    )


def excluir_conta_recorrente(conn: sqlite3.Connection, conta_recorrente_id: int) -> None:
    """Apaga a conta recorrente inteira e todo o histórico de ocorrências
    (paga/pulada) registrado pra ela. Não pode ser desfeito — quem chama
    decide se confirma com o usuário antes (ver compromissos.html)."""
    conn.execute("DELETE FROM ocorrencias_conta_recorrente WHERE conta_recorrente_id = ?", (conta_recorrente_id,))
    conn.execute("DELETE FROM contas_recorrentes WHERE id = ?", (conta_recorrente_id,))


def remover_ocorrencias_periodo(
    conn: sqlite3.Connection, conta_recorrente_id: int, competencia_inicio: str, competencia_fim: str
) -> None:
    """Apaga só as ocorrências (paga/pulada) dentro de um intervalo de
    competências — a conta recorrente em si continua ativa, os meses do
    intervalo voltam a ficar pendentes."""
    conn.execute(
        "DELETE FROM ocorrencias_conta_recorrente "
        "WHERE conta_recorrente_id = ? AND competencia BETWEEN ? AND ?",
        (conta_recorrente_id, competencia_inicio, competencia_fim),
    )


def criar_parcelamento(
    conn: sqlite3.Connection, descricao: str, valor_parcela: float, parcela_atual: int, parcela_total: int,
    cartao_final: str, categoria: str | None, observacao: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO parcelamentos
            (descricao, valor_parcela, parcela_atual, parcela_total, cartao_final, categoria, observacao)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            descricao.strip(), valor_parcela, parcela_atual, parcela_total,
            cartao_final.strip() or None, categoria or None, observacao.strip() or None,
        ),
    )
    return cursor.lastrowid


def encerrar_parcelamento(conn: sqlite3.Connection, id_: int) -> None:
    conn.execute("UPDATE parcelamentos SET ativo = 0 WHERE id = ?", (id_,))


def criar_divida(
    conn: sqlite3.Connection, credor: str, descricao: str, valor_parcela: float, parcelas_restantes: int,
    data_vencimento_proxima: str, categoria: str | None, observacao: str,
) -> str:
    """Gera id_divida como slug de `credor`, com sufixo numérico em colisão —
    mesmo padrão de criar_categoria() em revisar_manual.py."""
    base = f"divida_{slugificar(credor)}"
    id_divida = base
    existentes = {linha[0] for linha in conn.execute("SELECT id_divida FROM dividas").fetchall()}
    sufixo = 2
    while id_divida in existentes:
        id_divida = f"{base}_{sufixo}"
        sufixo += 1

    conn.execute(
        """
        INSERT INTO dividas
            (id_divida, credor, descricao, valor_parcela, parcelas_restantes,
             data_vencimento_proxima, categoria, observacao)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            id_divida, credor.strip(), descricao.strip() or None, valor_parcela, parcelas_restantes,
            data_vencimento_proxima or None, categoria or None, observacao.strip() or None,
        ),
    )
    return id_divida


def atualizar_vencimento_divida(conn: sqlite3.Connection, id_divida: str, nova_data: str) -> None:
    conn.execute("UPDATE dividas SET data_vencimento_proxima = ? WHERE id_divida = ?", (nova_data, id_divida))


def prospeccao_custo_fixo(conn: sqlite3.Connection, competencia_inicial: str, meses: int = 6) -> list[dict]:
    """Projeta, mês a mês a partir de `competencia_inicial`, o custo fixo
    total (contas recorrentes + parcelamentos + dívidas). Contas recorrentes
    ativas contam em todo mês do horizonte (não têm fim definido). Dívidas
    contam enquanto o offset em meses a partir de `data_vencimento_proxima`
    ainda estiver dentro de `parcelas_restantes` — mesma cadência mensal
    assumida por scripts/vincular_pagamento_divida.py. Parcelamentos
    reaproveitam parcelamentos_em_andamento (mesma projeção usada na tela)."""
    total_recorrentes = sum(
        valor_esperado
        for (valor_esperado,) in conn.execute(
            "SELECT valor_esperado FROM contas_recorrentes WHERE ativa = 1"
        ).fetchall()
    )

    dividas = conn.execute(
        "SELECT valor_parcela, parcelas_restantes, data_vencimento_proxima FROM dividas WHERE status = 'aberta'"
    ).fetchall()

    linhas = []
    for indice in range(meses):
        competencia = somar_meses_competencia(competencia_inicial, indice)

        total_parcelamentos = sum(
            item["valor_parcela"] for item in parcelamentos_em_andamento(conn, competencia)
        )

        total_dividas = 0.0
        for valor_parcela, parcelas_restantes, data_vencimento_proxima in dividas:
            if not data_vencimento_proxima:
                continue
            offset = diferenca_em_meses(competencia, data_vencimento_proxima[:7])
            if 0 <= offset < parcelas_restantes:
                total_dividas += valor_parcela

        linhas.append({
            "competencia": competencia,
            "recorrentes": total_recorrentes,
            "parcelamentos": total_parcelamentos,
            "dividas": total_dividas,
            "total": total_recorrentes + total_parcelamentos + total_dividas,
        })

    return linhas
