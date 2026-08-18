"""Motor de classificação (passo 4 do roadmap, spec seção 5).

Aplica `regras_classificacao` (ordenadas por prioridade, menor primeiro)
contra `transacoes.contraparte` e `lancamentos_fatura.descricao` — mesma
tabela de regras para as duas fontes, casamento por "contém" (case-
insensitive), como descrito no schema. Regras podem opcionalmente restringir
`tabela_alvo` a 'transacoes' ou 'lancamentos_fatura'; regras sem tabela_alvo
valem para as duas.

Regras "revisar_manual" (sentinela): algumas regras da spec reconhecem o
padrão mas decidem propositalmente não classificar automaticamente (ex:
"Mercado Pago Instituição" pode ser ads, fatura ou aporte; "PAYGO" pode ser
tarifa de adquirente ou compra). Isso é representado com
categoria_destino = 'revisar_manual' em regras_classificacao — uma
categoria sentinela em plano_de_contas que nunca é gravada como categoria
real de uma transação/lançamento; só força status = 'revisar_manual'.

Heurística de pessoa física: a spec pede "contraparte/descrição = nome de
pessoa física → Freelancer/prestador (revisar natureza)". Isso não dá pra
expressar como um simples "contém X" em regras_classificacao, então é
tratado à parte, como fallback quando nenhuma regra bate: nomes que não
contêm termos típicos de razão social/instituição financeira e têm pelo
menos duas palavras capitalizadas. Resultado sempre entra como
status = 'revisar_manual' (confirma a categoria, mas pede revisão da
natureza, conforme a spec).

Sem match nenhum (regra, sentinela ou heurística) → status permanece
'revisar_manual' e categoria NULL. O fallback de IA (spec seção 5, passo 2)
ainda não existe — é o próximo passo do roadmap.

Idempotente: só processa registros com categoria IS NULL, então rodar de
novo não reclassifica o que já foi decidido (automaticamente ou por revisão
manual futura, quando essa marcação existir).

Uso:
    python scripts/classificar.py [caminho_do_banco]
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "db" / "financeiro.db"

CATEGORIA_REVISAR_MANUAL = "revisar_manual"  # sentinela — ver docstring do módulo
CATEGORIA_FREELANCER = "freelancer_prestador"

ORIGEM_RECEITA_POR_CATEGORIA = {
    "vendas_mercado_livre": "mercado_livre",
    "vendas_shopee": "shopee",
    "vendas_outros_canais": "outro",
    "outras_receitas": "outro",
}

# Termos que indicam pessoa jurídica/instituição — usados para descartar a
# heurística de "nome de pessoa física" quando não é o caso.
TERMOS_RAZAO_SOCIAL = [
    "ltda", "eireli", " me ", "s.a.", " sa ", "instituição", "instituicao",
    "pagamento", "pagamentos", "serviços", "servicos", "brasil", "singapore",
    "pte", "mercado pago", "mercadopago",
]


def parece_pessoa_fisica(texto: str) -> bool:
    texto_norm = f" {texto.strip().lower()} "
    if any(termo in texto_norm for termo in TERMOS_RAZAO_SOCIAL):
        return False
    palavras_capitalizadas = [p for p in texto.strip().split() if p[:1].isupper()]
    return len(palavras_capitalizadas) >= 2


def carregar_regras(conn: sqlite3.Connection) -> list[tuple[str, str, str | None]]:
    return conn.execute(
        "SELECT padrao, categoria_destino, tabela_alvo FROM regras_classificacao ORDER BY prioridade ASC"
    ).fetchall()


def aplicar_regras(texto: str, regras: list[tuple[str, str, str | None]], tabela: str) -> str | None:
    texto_norm = texto.lower()
    for padrao, categoria_destino, tabela_alvo in regras:
        if tabela_alvo and tabela_alvo != tabela:
            continue
        if padrao.lower() in texto_norm:
            return categoria_destino
    return None


def classificar_transacoes(conn: sqlite3.Connection, regras: list[tuple[str, str, str | None]]) -> tuple[int, int]:
    classificadas = 0
    para_revisar = 0

    rows = conn.execute(
        "SELECT id_transacao, tipo, contraparte FROM transacoes WHERE categoria IS NULL"
    ).fetchall()

    for id_transacao, tipo, contraparte in rows:
        categoria_destino = aplicar_regras(contraparte, regras, "transacoes")
        forcar_revisao = False

        if categoria_destino is None and parece_pessoa_fisica(contraparte):
            categoria_destino = CATEGORIA_FREELANCER
            forcar_revisao = True

        if categoria_destino is None:
            para_revisar += 1
            continue

        if categoria_destino == CATEGORIA_REVISAR_MANUAL:
            conn.execute(
                "UPDATE transacoes SET status = 'revisar_manual' WHERE id_transacao = ?",
                (id_transacao,),
            )
            para_revisar += 1
            continue

        status = "revisar_manual" if forcar_revisao else "classificado"
        origem_receita = ORIGEM_RECEITA_POR_CATEGORIA.get(categoria_destino, "nao_receita") if tipo == "entrada" else None

        conn.execute(
            "UPDATE transacoes SET categoria = ?, origem_receita = ?, status = ? WHERE id_transacao = ?",
            (categoria_destino, origem_receita, status, id_transacao),
        )
        if status == "classificado":
            classificadas += 1
        else:
            para_revisar += 1

    return classificadas, para_revisar


def classificar_lancamentos(conn: sqlite3.Connection, regras: list[tuple[str, str, str | None]]) -> tuple[int, int]:
    classificados = 0
    para_revisar = 0

    rows = conn.execute(
        "SELECT id_lancamento, descricao FROM lancamentos_fatura WHERE categoria IS NULL"
    ).fetchall()

    for id_lancamento, descricao in rows:
        categoria_destino = aplicar_regras(descricao, regras, "lancamentos_fatura")
        forcar_revisao = False

        if categoria_destino is None and descricao.strip().upper().startswith("MP*"):
            categoria_destino = CATEGORIA_FREELANCER
            forcar_revisao = True
        elif categoria_destino is None and parece_pessoa_fisica(descricao):
            categoria_destino = CATEGORIA_FREELANCER
            forcar_revisao = True

        if categoria_destino is None:
            para_revisar += 1
            continue

        if categoria_destino == CATEGORIA_REVISAR_MANUAL:
            conn.execute(
                "UPDATE lancamentos_fatura SET status = 'revisar_manual' WHERE id_lancamento = ?",
                (id_lancamento,),
            )
            para_revisar += 1
            continue

        status = "revisar_manual" if forcar_revisao else "classificado"
        conn.execute(
            "UPDATE lancamentos_fatura SET categoria = ?, status = ? WHERE id_lancamento = ?",
            (categoria_destino, status, id_lancamento),
        )
        if status == "classificado":
            classificados += 1
        else:
            para_revisar += 1

    return classificados, para_revisar


def classificar(db_path: Path) -> tuple[tuple[int, int], tuple[int, int]]:
    conn = sqlite3.connect(db_path)
    try:
        regras = carregar_regras(conn)
        resultado_transacoes = classificar_transacoes(conn, regras)
        resultado_lancamentos = classificar_lancamentos(conn, regras)
        conn.commit()
    finally:
        conn.close()
    return resultado_transacoes, resultado_lancamentos


if __name__ == "__main__":
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB_PATH

    (t_classificadas, t_revisar), (l_classificados, l_revisar) = classificar(db_path)
    print(f"transacoes: {t_classificadas} classificadas, {t_revisar} para revisar manual")
    print(f"lancamentos_fatura: {l_classificados} classificados, {l_revisar} para revisar manual")
