"""Dashboard web local do relatório de entradas/saídas (passos 8 e 9 do roadmap).

Serve, via Flask, as mesmas visões já validadas no terminal por
scripts/relatorio.py (que continua sendo a fonte de verdade das queries de
agregação — este módulo só chama as funções de lá e renderiza HTML por
cima): entradas por canal, saídas por categoria do extrato, saídas por
categoria da fatura, visão combinada, ranking de fornecedores/contrapartes,
e faturas em aberto. Itens sem categoria (`categoria IS NULL`) aparecem em
todo lugar como o grupo "Sem categoria / Revisão pendente" — não são
ocultados nem escondidos num "não-receita" genérico.

Tem seletor de período (mês/semana/intervalo), busca por contraparte/
descrição (/buscar), gráfico de barras simples (HTML/CSS, sem lib externa),
drill-down por clique (itens individuais por trás de cada linha, via
/detalhe) e reclassificação inline por item (via /reclassificar, que
reaproveita scripts/revisar_manual.py: cria a regra em regras_classificacao
respeitando tabela_alvo, e pode criar uma categoria nova em plano_de_contas
na hora, se o usuário escolher "+ Criar nova categoria").

Variáveis de ambiente:
    DASHBOARD_ENV     "production" liga autenticação básica e desliga o modo
                      debug do Flask; qualquer outro valor (ou ausente) roda
                      em modo local de desenvolvimento, sem autenticação.
    DASHBOARD_USER    usuário exigido pela autenticação básica (só checado
                      quando DASHBOARD_ENV=production).
    DASHBOARD_SENHA   senha exigida pela autenticação básica (idem). Nunca
                      hardcoded — sempre vinda do ambiente (ver Dockerfile).

Uso:
    python scripts/dashboard.py
    # abre em http://127.0.0.1:5000
"""
import os
import secrets
import sqlite3
from datetime import date
from pathlib import Path

from flask import Flask, Response, flash, jsonify, redirect, render_template, request, url_for

import cadastros
import compromissos
import importacao
import parse_fatura_cartao
from relatorio import (
    DEFAULT_DB_PATH,
    buscar_por_termo,
    combinar_saidas,
    entradas_detalhe,
    entradas_por_canal,
    evolucao_mensal,
    faturas_em_aberto,
    fmt_moeda,
    meses_disponiveis,
    nome_categoria,
    nomes_categorias,
    periodo_da_semana,
    periodo_do_mes,
    saidas_extrato_detalhe,
    saidas_extrato_por_categoria,
    saidas_fatura_detalhe,
    saidas_fatura_por_categoria,
    saidas_por_contraparte,
    saidas_por_contraparte_detalhe,
)
from revisar_manual import carregar_categorias, criar_categoria, criar_regra

NOVA_CATEGORIA_SENTINELA = "__NOVA__"
TIPO_PLANO_POR_TIPO_TRANSACAO = {"entrada": "receita", "saida": "despesa"}

MESES_ABREV = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

PALETA_DONUT = [
    "#7C5CFC", "#4CC9F0", "#FF6B9D", "#FFB703", "#43AA8B",
    "#F94144", "#577590", "#90BE6D", "#F72585", "#4895EF",
]

app = Flask(__name__)
# Só usado pra assinar o cookie de sessão das mensagens flash da tela de
# importação — não precisa sobreviver a um restart, então gerar um valor
# novo a cada subida do processo é suficiente (não é credencial de acesso).
app.secret_key = secrets.token_hex(32)

DASHBOARD_ENV = os.environ.get("DASHBOARD_ENV", "development")


def autenticacao_exigida() -> bool:
    return DASHBOARD_ENV == "production"


def credenciais_validas(usuario: str | None, senha: str | None) -> bool:
    usuario_esperado = os.environ.get("DASHBOARD_USER", "")
    senha_esperada = os.environ.get("DASHBOARD_SENHA", "")
    return bool(usuario_esperado and senha_esperada
                and secrets.compare_digest(usuario or "", usuario_esperado)
                and secrets.compare_digest(senha or "", senha_esperada))


@app.before_request
def exigir_autenticacao():
    if not autenticacao_exigida():
        return None
    auth = request.authorization
    if auth and credenciais_validas(auth.username, auth.password):
        return None
    return Response(
        "Autenticação necessária.", 401, {"WWW-Authenticate": 'Basic realm="Dashboard financeiro"'}
    )


def resolver_periodo(args) -> tuple[str, str, str, dict]:
    """Lê os parâmetros da query string e devolve (inicio, fim, tipo, valores_do_form)."""
    tipo = args.get("periodo", "mes")

    if tipo == "semana":
        semana = args.get("semana") or date.today().isoformat()
        inicio, fim = periodo_da_semana(semana)
        return inicio, fim, tipo, {"semana": semana}

    if tipo == "intervalo":
        inicio = args.get("inicio") or date.today().replace(day=1).isoformat()
        fim = args.get("fim") or date.today().isoformat()
        return inicio, fim, tipo, {"inicio": inicio, "fim": fim}

    mes = args.get("mes") or date.today().strftime("%Y-%m")
    inicio, fim = periodo_do_mes(mes)
    return inicio, fim, "mes", {"mes": mes}


def montar_linhas(linhas: list[tuple], categorias: dict[str, str], indice_categoria: int = 0) -> list[dict]:
    resultado = []
    for linha in linhas:
        *chaves, valor = linha
        chave_bruta = chaves[indice_categoria]
        chaves[indice_categoria] = nome_categoria(chave_bruta, categorias)
        rotulo = " / ".join(str(c) if c is not None else "(sem valor)" for c in chaves)
        resultado.append({
            "rotulo": rotulo,
            "valor": valor,
            "valor_fmt": fmt_moeda(valor),
            "chave": chave_bruta if chave_bruta is not None else "",
        })
    return resultado


def montar_grafico(entradas: list[dict], saidas_combinadas: list[dict]) -> list[dict]:
    barras = [{"rotulo": e["rotulo"], "valor": e["valor"], "tipo": "entrada"} for e in entradas]
    barras += [{"rotulo": s["rotulo"], "valor": s["valor"], "tipo": "saida"} for s in saidas_combinadas]
    barras.sort(key=lambda b: b["valor"], reverse=True)
    maximo = max((b["valor"] for b in barras), default=0)
    for b in barras:
        b["largura_pct"] = round(b["valor"] / maximo * 100, 1) if maximo else 0
        b["valor_fmt"] = fmt_moeda(b["valor"])
    return barras


def categorias_para_select(categorias: dict[str, str]) -> list[tuple[str, str]]:
    return sorted(
        ((codigo, nome) for codigo, nome in categorias.items() if codigo != "revisar_manual"),
        key=lambda item: item[1],
    )


def rotulo_mes(mes: str) -> str:
    ano, mes_num = mes.split("-")
    return f"{MESES_ABREV[int(mes_num) - 1]}/{ano[2:]}"


def montar_donut(linhas: list[dict], raio: float = 70.0) -> list[dict]:
    """Fatias de um donut SVG (stroke-dasharray/offset) a partir das mesmas
    linhas já agregadas — sem recalcular nada, só a geometria do desenho."""
    total = sum(linha["valor"] for linha in linhas)
    circunferencia = 2 * 3.14159265358979 * raio
    fatias = []
    offset_acumulado = 0.0
    for i, linha in enumerate(linhas):
        pct = (linha["valor"] / total * 100) if total else 0
        comprimento = pct / 100 * circunferencia
        fatias.append({
            "rotulo": linha["rotulo"],
            "valor_fmt": linha["valor_fmt"],
            "pct": round(pct, 1),
            "cor": PALETA_DONUT[i % len(PALETA_DONUT)],
            "dasharray": f"{comprimento:.3f} {circunferencia - comprimento:.3f}",
            "dashoffset": f"{-offset_acumulado:.3f}",
        })
        offset_acumulado += comprimento
    return fatias


def montar_evolucao_svg(pontos: list[dict], largura: float = 640.0, altura: float = 220.0) -> dict:
    """Coordenadas SVG (polyline + marcadores) da evolução mensal — geometria
    calculada em Python pra o gráfico ficar 100% markup (sem JS), assim
    sobrevive à troca de innerHTML no refresh pós-reclassificação sem
    precisar re-executar script nenhum."""
    pad_x, topo, base = 36.0, 16.0, altura - 34.0
    maximo = max((max(p["entradas"], p["saidas"]) for p in pontos), default=0) or 1
    n = len(pontos)

    def coord_x(i: int) -> float:
        if n <= 1:
            return largura / 2
        return pad_x + i * (largura - 2 * pad_x) / (n - 1)

    def coord_y(valor: float) -> float:
        return base - (valor / maximo) * (base - topo)

    marcadores = []
    for i, p in enumerate(pontos):
        marcadores.append({
            "x": round(coord_x(i), 1),
            "y_entradas": round(coord_y(p["entradas"]), 1),
            "y_saidas": round(coord_y(p["saidas"]), 1),
            "y_rotulo": round(base + 18, 1),
            "rotulo": p["rotulo"],
            "entradas_fmt": fmt_moeda(p["entradas"]),
            "saidas_fmt": fmt_moeda(p["saidas"]),
        })

    linha_entradas = " ".join(f"{m['x']},{m['y_entradas']}" for m in marcadores)
    linha_saidas = " ".join(f"{m['x']},{m['y_saidas']}" for m in marcadores)

    return {
        "largura": largura,
        "altura": altura,
        "marcadores": marcadores,
        "linha_entradas": linha_entradas,
        "linha_saidas": linha_saidas,
        "unico_ponto": n == 1,
    }


def montar_evolucao(conn: sqlite3.Connection) -> dict:
    """Evolução mensal (últimos 12 meses com dado) pro gráfico de linha —
    independe do período selecionado nos cards, é uma visão geral."""
    meses = meses_disponiveis(conn, limite=12)
    pontos = evolucao_mensal(conn, meses)
    for ponto in pontos:
        ponto["rotulo"] = rotulo_mes(ponto["mes"])
    return montar_evolucao_svg(pontos)


def montar_contexto(conn: sqlite3.Connection, inicio: str, fim: str) -> dict:
    """Monta o contexto de dados do período — usado tanto pela página cheia
    (index) quanto pelo refresh parcial (conteudo), pra não duplicar."""
    categorias = nomes_categorias(conn)

    # Itens de entrada sem origem_receita definida (nenhuma regra bateu) NÃO
    # são mais empurrados pra dentro de "nao_receita" — ficam com chave None,
    # que `nome_categoria` mostra como "Sem categoria / Revisão pendente"
    # (mesmo tratamento das saídas), em vez de ficarem escondidos ali dentro.
    entradas = montar_linhas(entradas_por_canal(conn, inicio, fim), {})
    saidas_extrato = saidas_extrato_por_categoria(conn, inicio, fim)
    saidas_fatura = saidas_fatura_por_categoria(conn, inicio, fim)

    saidas_combinadas = montar_linhas(combinar_saidas(saidas_extrato, saidas_fatura), categorias)
    fornecedores = montar_linhas(saidas_por_contraparte(conn, inicio, fim), {})

    total_receita = sum(e["valor"] for e in entradas)
    total_saidas = sum(s["valor"] for s in saidas_combinadas)
    margem = total_receita - total_saidas
    margem_pct = (margem / total_receita * 100) if total_receita else 0

    contexto = {
        "inicio": inicio,
        "fim": fim,
        "entradas": entradas,
        "saidas_extrato": montar_linhas(saidas_extrato, categorias),
        "saidas_fatura": montar_linhas(saidas_fatura, categorias),
        "saidas_combinadas": saidas_combinadas,
        "fornecedores": fornecedores,
        "faturas_abertas": faturas_em_aberto(conn),
        "fmt_moeda": fmt_moeda,
        "categorias_lista": categorias_para_select(categorias),
        "kpi_receita": total_receita,
        "kpi_receita_fmt": fmt_moeda(total_receita),
        "kpi_saidas": total_saidas,
        "kpi_saidas_fmt": fmt_moeda(total_saidas),
        "kpi_margem": margem,
        "kpi_margem_fmt": fmt_moeda(margem),
        "kpi_margem_pct": round(margem_pct, 1),
        "donut_saidas": montar_donut(saidas_combinadas),
        "evolucao": montar_evolucao(conn),
    }
    contexto["grafico"] = montar_grafico(contexto["entradas"], contexto["saidas_combinadas"])
    return contexto


@app.route("/")
def index():
    db_path = Path(request.args.get("db", str(DEFAULT_DB_PATH)))
    inicio, fim, tipo_periodo, valores_periodo = resolver_periodo(request.args)

    conn = sqlite3.connect(db_path)
    try:
        contexto = montar_contexto(conn, inicio, fim)
        contexto["tipo_periodo"] = tipo_periodo
        contexto["valores_periodo"] = valores_periodo
        return render_template("dashboard.html", **contexto)
    finally:
        conn.close()


@app.route("/conteudo")
def conteudo():
    """Fragmento HTML (gráfico + cards) usado pra atualizar a tela depois de
    uma reclassificação, sem recarregar a página inteira."""
    db_path = Path(request.args.get("db", str(DEFAULT_DB_PATH)))
    inicio, fim, _tipo, _valores = resolver_periodo(request.args)

    conn = sqlite3.connect(db_path)
    try:
        contexto = montar_contexto(conn, inicio, fim)
        return render_template("conteudo.html", **contexto)
    finally:
        conn.close()


def _linhas_para_itens(linhas: list[tuple]) -> list[dict]:
    """Formata linhas (id, data, texto, valor, categoria, tipo_transacao,
    origem) pro JSON consumido pelo JS — mesmo formato usado por /detalhe,
    /buscar e o drill-down de fornecedores, pra reaproveitar a mesma função
    de montagem de linha no front-end (`montarLinhaDetalhe`)."""
    return [
        {
            "id": i,
            "data": d,
            "texto": texto,
            "valor": v,
            "valor_fmt": fmt_moeda(v),
            "categoria": cat or "",
            "tipo_transacao": tipo_transacao,
            "origem": origem,
        }
        for i, d, texto, v, cat, tipo_transacao, origem in linhas
    ]


@app.route("/detalhe")
def detalhe():
    """Drill-down: itens individuais por trás de uma linha de um dos cards.

    Reaproveita as funções `*_detalhe` de relatorio.py — nenhuma lógica de
    filtro é duplicada aqui, só a montagem da resposta JSON.
    """
    db_path = Path(request.args.get("db", str(DEFAULT_DB_PATH)))
    fonte = request.args.get("fonte", "")
    categoria = request.args.get("categoria") or None
    inicio = request.args.get("inicio", "")
    fim = request.args.get("fim", "")

    conn = sqlite3.connect(db_path)
    try:
        if fonte == "entradas":
            linhas = [
                (i, d, texto, v, cat, "entrada", "extrato")
                for i, d, texto, v, cat in entradas_detalhe(conn, inicio, fim, categoria)
            ]
        elif fonte == "saidas_extrato":
            linhas = [
                (i, d, texto, v, cat, "saida", "extrato")
                for i, d, texto, v, cat in saidas_extrato_detalhe(conn, inicio, fim, categoria)
            ]
        elif fonte == "saidas_fatura":
            linhas = [
                (i, d, texto, v, cat, "saida", "fatura")
                for i, d, texto, v, cat in saidas_fatura_detalhe(conn, inicio, fim, categoria)
            ]
        elif fonte == "combinada":
            linhas = [
                (i, d, texto, v, cat, "saida", "extrato")
                for i, d, texto, v, cat in saidas_extrato_detalhe(conn, inicio, fim, categoria)
            ]
            linhas += [
                (i, d, texto, v, cat, "saida", "fatura")
                for i, d, texto, v, cat in saidas_fatura_detalhe(conn, inicio, fim, categoria)
            ]
            linhas.sort(key=lambda linha: linha[1])
        elif fonte == "fornecedores":
            if categoria is None:
                return jsonify({"erro": "fornecedor não informado"}), 400
            linhas = saidas_por_contraparte_detalhe(conn, inicio, fim, categoria)
        else:
            return jsonify({"erro": f"fonte desconhecida: {fonte}"}), 400

        return jsonify(_linhas_para_itens(linhas))
    finally:
        conn.close()


@app.route("/buscar")
def buscar():
    """Busca por contraparte/descrição (contém, case-insensitive) dentro do
    período selecionado — reaproveita `buscar_por_termo` de relatorio.py."""
    db_path = Path(request.args.get("db", str(DEFAULT_DB_PATH)))
    termo = request.args.get("termo", "").strip()
    inicio = request.args.get("inicio", "")
    fim = request.args.get("fim", "")

    if not termo:
        return jsonify({"itens": [], "total": 0, "total_fmt": fmt_moeda(0)})

    conn = sqlite3.connect(db_path)
    try:
        linhas = buscar_por_termo(conn, inicio, fim, termo)
        itens = _linhas_para_itens(linhas)
        total = sum(item["valor"] for item in itens)
        return jsonify({"itens": itens, "total": total, "total_fmt": fmt_moeda(total)})
    finally:
        conn.close()


@app.route("/reclassificar", methods=["POST"])
def reclassificar():
    """Reclassifica UM item específico (por id) pra uma categoria escolhida
    na interface — existente ou nova (criada na hora em plano_de_contas, com
    tipo inferido por `tipo_transacao`: entrada -> receita, saída -> despesa).
    Também cria/atualiza a regra correspondente em regras_classificacao
    (mesma lógica de scripts/revisar_manual.py, reaproveitada aqui em vez de
    duplicada), respeitando tabela_alvo, pra que itens futuros com o mesmo
    texto já entrem classificados sozinhos."""
    dados = request.get_json(force=True, silent=True) or {}
    db_path = Path(dados.get("db", str(DEFAULT_DB_PATH)))
    item_id = dados.get("id")
    origem = dados.get("origem")
    padrao = dados.get("padrao")
    tipo_transacao = dados.get("tipo_transacao")
    categoria_destino = dados.get("categoria_destino")
    categoria_nome_novo = (dados.get("categoria_nome_novo") or "").strip()

    if origem not in ("extrato", "fatura") or not item_id or not padrao or not categoria_destino:
        return jsonify({"erro": "parâmetros inválidos"}), 400

    tabela = "transacoes" if origem == "extrato" else "lancamentos_fatura"
    coluna_id = "id_transacao" if tabela == "transacoes" else "id_lancamento"

    conn = sqlite3.connect(db_path)
    try:
        if categoria_destino == NOVA_CATEGORIA_SENTINELA:
            if not categoria_nome_novo:
                return jsonify({"erro": "nome da categoria nova não pode ser vazio"}), 400
            tipo_plano = TIPO_PLANO_POR_TIPO_TRANSACAO.get(tipo_transacao)
            if tipo_plano is None:
                return jsonify({"erro": f"tipo_transacao inválido: {tipo_transacao}"}), 400
            categoria_destino = criar_categoria(conn, categoria_nome_novo, tipo_plano)
        else:
            codigos_validos = {codigo for codigo, _nome in carregar_categorias(conn)}
            if categoria_destino not in codigos_validos:
                return jsonify({"erro": f"categoria inválida: {categoria_destino}"}), 400

        cursor = conn.execute(
            f"UPDATE {tabela} SET categoria = ?, status = 'classificado' WHERE {coluna_id} = ?",
            (categoria_destino, item_id),
        )
        linhas_afetadas = cursor.rowcount

        criar_regra(conn, padrao, categoria_destino, tabela)
        conn.commit()

        nome_categoria_destino = dict(carregar_categorias(conn)).get(categoria_destino, categoria_destino)
        return jsonify({
            "ok": True,
            "linhas_afetadas": linhas_afetadas,
            "categoria_destino": categoria_destino,
            "categoria_nome": nome_categoria_destino,
        })
    finally:
        conn.close()


TIPO_COMPROMISSO_ROTULO = {"recorrente": "Recorrente", "parcelamento": "Parcelamento", "divida": "Dívida"}
STATUS_COMPROMISSO_ROTULO = {
    "pendente": "Pendente", "paga": "Paga", "pulada": "Pulada", "projetado": "Projetado", "aberta": "Aberta",
}


def montar_contexto_compromissos(conn: sqlite3.Connection, mes: str) -> dict:
    categorias = nomes_categorias(conn)
    mapa_cartoes = cadastros.mapa_cartoes_por_final(conn)

    linhas = []
    for linha in compromissos.linhas_unificadas_compromissos(conn, mes):
        categoria = linha["categoria"]
        linhas.append({
            **linha,
            "tipo_rotulo": TIPO_COMPROMISSO_ROTULO[linha["tipo"]],
            "categoria_nome": categorias.get(categoria, categoria) if categoria else "(sem categoria)",
            "cartao_rotulo": cadastros.nome_cartao(linha["cartao_final"], mapa_cartoes),
            "valor_fmt": fmt_moeda(linha["valor"]),
            "status_rotulo": STATUS_COMPROMISSO_ROTULO.get(linha["status"], linha["status"]),
        })

    # Totais referem-se só às contas recorrentes do mês selecionado — parcelamentos
    # e dívidas não entram nessa soma (decisão explícita, spec seção 15/16).
    linhas_recorrentes = [l for l in linhas if l["tipo"] == "recorrente"]
    total_esperado = sum(l["valor"] for l in linhas_recorrentes)
    total_pago = sum(l["valor_pago"] or 0 for l in linhas_recorrentes if l["status"] == "paga")
    total_pendente = sum(l["valor"] for l in linhas_recorrentes if l["status"] == "pendente")
    totais_contas = {
        "esperado_fmt": fmt_moeda(total_esperado),
        "pago_fmt": fmt_moeda(total_pago),
        "pendente_fmt": fmt_moeda(total_pendente),
    }

    prospeccao = [
        {**linha, "recorrentes_fmt": fmt_moeda(linha["recorrentes"]), "parcelamentos_fmt": fmt_moeda(linha["parcelamentos"]),
         "dividas_fmt": fmt_moeda(linha["dividas"]), "total_fmt": fmt_moeda(linha["total"])}
        for linha in compromissos.prospeccao_custo_fixo(conn, mes)
    ]

    return {
        "mes": mes,
        "linhas": linhas,
        "totais_contas": totais_contas,
        "prospeccao": prospeccao,
        "categorias_lista": categorias_para_select(categorias),
    }


@app.route("/compromissos", endpoint="compromissos")
def pagina_compromissos():
    db_path = Path(request.args.get("db", str(DEFAULT_DB_PATH)))
    mes = request.args.get("mes") or date.today().strftime("%Y-%m")

    conn = sqlite3.connect(db_path)
    try:
        contexto = montar_contexto_compromissos(conn, mes)
        return render_template("compromissos.html", **contexto)
    finally:
        conn.close()


@app.route("/contas_recorrentes", methods=["POST"], endpoint="criar_conta_recorrente_rota")
def criar_conta_recorrente_rota():
    form = request.form
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    try:
        compromissos.criar_conta_recorrente(
            conn, form["nome"], form.get("categoria") or None, float(form["valor_esperado"]),
            int(form["dia_vencimento"]), form.get("observacao", ""),
        )
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("compromissos"))


@app.route("/contas_recorrentes/<int:id>/marcar", methods=["POST"], endpoint="marcar_conta_recorrente")
def marcar_conta_recorrente(id: int):
    form = request.form
    mes = form.get("mes") or date.today().strftime("%Y-%m")
    status = form.get("status", "paga")
    valor_pago = form.get("valor_pago") or None

    conn = sqlite3.connect(DEFAULT_DB_PATH)
    try:
        compromissos.marcar_ocorrencia(
            conn, id, mes, status, float(valor_pago) if valor_pago else None, date.today().isoformat(),
        )
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("compromissos", mes=mes))


@app.route("/contas_recorrentes/<int:id>/desmarcar", methods=["POST"], endpoint="desmarcar_conta_recorrente")
def desmarcar_conta_recorrente(id: int):
    mes = request.form.get("mes") or date.today().strftime("%Y-%m")

    conn = sqlite3.connect(DEFAULT_DB_PATH)
    try:
        compromissos.desmarcar_ocorrencia(conn, id, mes)
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("compromissos", mes=mes))


@app.route("/contas_recorrentes/<int:id>/excluir", methods=["POST"], endpoint="excluir_conta_recorrente_rota")
def excluir_conta_recorrente_rota(id: int):
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    try:
        compromissos.excluir_conta_recorrente(conn, id)
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("compromissos"))


@app.route(
    "/contas_recorrentes/<int:id>/remover_periodo", methods=["POST"], endpoint="remover_periodo_conta_recorrente_rota"
)
def remover_periodo_conta_recorrente_rota(id: int):
    form = request.form
    mes = form.get("mes") or date.today().strftime("%Y-%m")

    conn = sqlite3.connect(DEFAULT_DB_PATH)
    try:
        compromissos.remover_ocorrencias_periodo(conn, id, form["competencia_inicio"], form["competencia_fim"])
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("compromissos", mes=mes))


@app.route("/parcelamentos", methods=["POST"], endpoint="criar_parcelamento_rota")
def criar_parcelamento_rota():
    form = request.form
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    try:
        compromissos.criar_parcelamento(
            conn, form["descricao"], float(form["valor_parcela"]), int(form["parcela_atual"]),
            int(form["parcela_total"]), form.get("cartao_final", ""), form.get("categoria") or None,
            form.get("observacao", ""),
        )
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("compromissos"))


@app.route("/parcelamentos/<int:id>/encerrar", methods=["POST"], endpoint="encerrar_parcelamento_rota")
def encerrar_parcelamento_rota(id: int):
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    try:
        compromissos.encerrar_parcelamento(conn, id)
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("compromissos"))


@app.route("/dividas", methods=["POST"], endpoint="criar_divida_rota")
def criar_divida_rota():
    form = request.form
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    try:
        compromissos.criar_divida(
            conn, form["credor"], form.get("descricao", ""), float(form["valor_parcela"]),
            int(form["parcelas_restantes"]), form.get("data_vencimento_proxima") or None,
            form.get("categoria") or None, form.get("observacao", ""),
        )
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("compromissos"))


@app.route(
    "/dividas/<id_divida>/atualizar_vencimento", methods=["POST"], endpoint="atualizar_vencimento_divida_rota"
)
def atualizar_vencimento_divida_rota(id_divida: str):
    nova_data = request.form.get("data_vencimento_proxima") or None
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    try:
        compromissos.atualizar_vencimento_divida(conn, id_divida, nova_data)
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("compromissos"))


@app.route("/importar", endpoint="importar")
def pagina_importar():
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    try:
        contas = [
            {"id": id_, "banco": banco, "agencia": agencia, "numero_conta": numero_conta,
             "apelido": apelido, "ativa": bool(ativa)}
            for id_, banco, agencia, numero_conta, apelido, ativa in cadastros.listar_contas_bancarias(conn)
        ]
        cartoes = [
            {"id": id_, "banco": banco, "apelido": apelido, "final_cartao": final_cartao,
             "dia_fechamento": dia_fechamento, "dia_vencimento": dia_vencimento, "ativo": bool(ativo)}
            for id_, banco, apelido, final_cartao, dia_fechamento, dia_vencimento, ativo in cadastros.listar_cartoes(conn)
        ]
        return render_template("importar.html", contas_bancarias=contas, cartoes=cartoes)
    finally:
        conn.close()


@app.route("/importar/extrato", methods=["POST"], endpoint="importar_extrato_rota")
def importar_extrato_rota():
    arquivo = request.files.get("arquivo")
    if not arquivo or not arquivo.filename:
        flash("Selecione um arquivo de extrato (.csv ou .xlsx) antes de importar.", "erro")
        return redirect(url_for("importar"))

    try:
        resultado = importacao.importar_extrato_upload(arquivo, DEFAULT_DB_PATH)
        flash(
            f"Extrato \"{resultado['nome_arquivo']}\" importado: {resultado['inseridas']} transações novas, "
            f"{resultado['ignoradas']} já existiam.",
            "sucesso",
        )
    except importacao.FormatoInvalidoError as erro:
        flash(str(erro), "erro")
    except Exception as erro:
        flash(f"Erro ao importar o extrato: {erro}", "erro")
    return redirect(url_for("importar"))


@app.route("/importar/fatura", methods=["POST"], endpoint="importar_fatura_rota")
def importar_fatura_rota():
    arquivo = request.files.get("arquivo")
    senha_manual = request.form.get("senha") or None
    if not arquivo or not arquivo.filename:
        flash("Selecione um arquivo de fatura (.pdf) antes de importar.", "erro")
        return redirect(url_for("importar"))

    try:
        resultado = importacao.importar_fatura_upload(arquivo, DEFAULT_DB_PATH, senha_manual)
        flash(
            f"Fatura \"{resultado['nome_arquivo']}\" importada ({resultado['id_fatura']}): "
            f"{resultado['inseridos']} lançamentos novos, {resultado['ignorados']} já existiam.",
            "sucesso",
        )
    except importacao.FormatoInvalidoError as erro:
        flash(str(erro), "erro")
    except parse_fatura_cartao.SenhaNecessariaError as erro:
        flash(str(erro), "erro")
    except Exception as erro:
        flash(f"Erro ao importar a fatura: {erro}", "erro")
    return redirect(url_for("importar"))


@app.route("/contas_bancarias", methods=["POST"], endpoint="criar_conta_bancaria_rota")
def criar_conta_bancaria_rota():
    form = request.form
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    try:
        cadastros.criar_conta_bancaria(
            conn, form["banco"], form.get("agencia", ""), form.get("numero_conta", ""), form.get("apelido", ""),
        )
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("importar"))


@app.route("/contas_bancarias/<int:id>/excluir", methods=["POST"], endpoint="excluir_conta_bancaria_rota")
def excluir_conta_bancaria_rota(id: int):
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    try:
        cadastros.excluir_conta_bancaria(conn, id)
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("importar"))


@app.route("/cartoes", methods=["POST"], endpoint="criar_cartao_rota")
def criar_cartao_rota():
    form = request.form
    dia_fechamento = form.get("dia_fechamento") or None
    dia_vencimento = form.get("dia_vencimento") or None
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    try:
        cadastros.criar_cartao(
            conn, form["banco"], form.get("apelido", ""), form["final_cartao"],
            int(dia_fechamento) if dia_fechamento else None,
            int(dia_vencimento) if dia_vencimento else None,
        )
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("importar"))


@app.route("/cartoes/<int:id>/excluir", methods=["POST"], endpoint="excluir_cartao_rota")
def excluir_cartao_rota(id: int):
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    try:
        cadastros.excluir_cartao(conn, id)
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("importar"))


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", "5000"))
    if autenticacao_exigida():
        app.run(host="0.0.0.0", port=porta, debug=False)
    else:
        app.run(host="127.0.0.1", port=porta, debug=True)
