"""Parser da fatura de cartão em PDF -> tabelas `faturas_cartao` e `lancamentos_fatura`.

Formato de entrada (spec, seção 3b): fatura consolidada Mercado Pago,
protegida por senha, com um bloco de metadados no topo:

    Total a pagar: R$ 5.341,35
    Vencimento: 22/06/2026
    Fechamento da fatura: 15/06/2026
    Próximo fechamento: 15/07/2026
    Melhor dia de compra: 16/06/2026
    Limite total: R$ 10.500,00
    Limite utilizado: R$ 10.464,88
    Limite disponível: R$ 35,12
    Compras parceladas a vencer (compromisso futuro): R$ 5.123,53

... seguido de seções por cartão físico ("Cartão ****7111" / "Cartão final 7111")
com uma linha por lançamento:

    16/06/2026  INNER AI                       199,90
    17/06/2026  CLAUDE.AI SUBSCRIPTION  2/6      89,90

ATENÇÃO: assim como aconteceu com o CSV do Inter (que trazia linhas de
metadados que a spec não previa), o layout exato do texto extraído de um PDF
real pode divergir do que está descrito na especificação. As regexes abaixo
implementam a leitura conforme o formato documentado na seção 3b; ao rodar
contra uma fatura real pela primeira vez, valide a saída e ajuste os padrões
se necessário.

Senha do PDF: o script tenta abrir o arquivo sem senha primeiro (nem toda
fatura vem protegida). Só se o PDF exigir senha é que a variável de ambiente
FATURA_CARTAO_SENHA é consultada — nunca hardcoded no código.

Uso:
    python scripts/parse_fatura_cartao.py caminho/fatura.pdf [caminho_do_banco]
    # se o PDF for protegido:
    export FATURA_CARTAO_SENHA="sua_senha"
    python scripts/parse_fatura_cartao.py caminho/fatura.pdf [caminho_do_banco]
"""
import hashlib
import os
import re
import sqlite3
import sys
from pathlib import Path

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect
from pdfplumber.utils.exceptions import PdfminerException

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "db" / "financeiro.db"
SENHA_ENV_VAR = "FATURA_CARTAO_SENHA"

# O texto real extraído do PDF do Mercado Pago usa rótulos sem ":" e a data de
# vencimento/limite total aparecem na mesma linha que o total a pagar, ex:
#     Total a pagar Vence em Limite total Saque total
#     R$ 5.341,35 22/06/2026 R$ 10.500,00 R$ 50,00
#     ...
#     Fechamento da fatura 15/06/2026
#     Próximo fechamento 15/07/2026
#     Limite utilizado R$ 10.464,88
#     Limite disponível R$ 35,12
#     Compras parceladas R$ 5.123,53
RE_RESUMO_TOPO = re.compile(
    r"R\$\s*([\d.,]+)\s+(\d{2}/\d{2}/\d{4})\s+R\$\s*([\d.,]+)\s+R\$\s*[\d.,]+"
)  # valor_total_fatura, data_vencimento, limite_total (saque_total descartado)

RE_METADADOS = {
    "emitida_em": r"Emitida em:\s*(\d{2}/\d{2}/\d{4})",
    "data_fechamento": r"Fechamento da fatura\s+(\d{2}/\d{2}/\d{4})",
    "data_proximo_fechamento": r"Pr[oó]ximo fechamento\s+(\d{2}/\d{2}/\d{4})",
    "limite_utilizado": r"Limite utilizado\s+R\$\s*([\d.,]+)",
    "limite_disponivel": r"Limite dispon[ií]vel\s+R\$\s*([\d.,]+)",
    "compras_parceladas_futuras": r"Compras parceladas\s+R\$\s*([\d.,]+)",
}

RE_CARTAO_SECAO = re.compile(r"Cart[ãa]o\s+\S+\s*\[\*+(\d{4})\]", re.IGNORECASE)

# Lançamentos vêm sem ano na data ("DD/MM"); o ano é inferido a partir do
# fechamento da fatura (ver `inferir_ano`).
RE_LANCAMENTO = re.compile(
    r"^(\d{2})/(\d{2})\s+"                              # dia/mes da compra
    r"(.+?)"                                             # descricao (non-greedy)
    r"(?:\s+Parcela\s+(\d{1,2})\s+de\s+(\d{1,2}))?"      # parcela_atual/parcela_total (opcional)
    r"\s+R\$\s*([\d]{1,3}(?:\.\d{3})*,\d{2})$"           # valor
)


def parse_valor_br(texto: str) -> float:
    texto = texto.strip().replace(".", "").replace(",", ".")
    return float(texto)


def inferir_ano(dia: str, mes: str, data_fechamento_iso: str) -> str:
    """Lançamentos trazem só dia/mês; o ano é inferido a partir do mês de
    fechamento da fatura (o ciclo de consumo termina no fechamento, então um
    lançamento com mês posterior ao do fechamento pertence ao ano anterior)."""
    ano_fechamento, mes_fechamento = data_fechamento_iso[:4], int(data_fechamento_iso[5:7])
    ano = int(ano_fechamento) if int(mes) <= mes_fechamento else int(ano_fechamento) - 1
    return f"{ano:04d}-{mes}-{dia}"


def parse_data_br(texto: str) -> str:
    dia, mes, ano = texto.strip().split("/")
    return f"{ano}-{mes}-{dia}"


def extrair_texto(pdf_path: Path) -> str:
    """Tenta abrir o PDF sem senha primeiro; só recorre a FATURA_CARTAO_SENHA
    se o próprio arquivo exigir senha (PDFPasswordIncorrect/PDFEncryptionError)."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except (PDFPasswordIncorrect, PDFEncryptionError, PdfminerException) as erro:
        causa = erro.__cause__ or erro.__context__ if isinstance(erro, PdfminerException) else erro
        if not isinstance(causa, (PDFPasswordIncorrect, PDFEncryptionError)):
            raise
        senha = os.environ.get(SENHA_ENV_VAR)
        if not senha:
            print(
                f"O PDF {pdf_path} é protegido por senha. "
                f"Defina a variável de ambiente {SENHA_ENV_VAR} com a senha antes de rodar."
            )
            sys.exit(1)
        with pdfplumber.open(pdf_path, password=senha) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)


CAMPOS_DATA = {"emitida_em", "data_fechamento", "data_proximo_fechamento", "data_vencimento"}


def extrair_metadados(texto: str) -> dict:
    metadados = {}
    for campo, padrao in RE_METADADOS.items():
        match = re.search(padrao, texto)
        if not match:
            continue
        valor = match.group(1)
        metadados[campo] = parse_data_br(valor) if campo in CAMPOS_DATA else parse_valor_br(valor)

    resumo = RE_RESUMO_TOPO.search(texto)
    if resumo:
        metadados["valor_total_fatura"] = parse_valor_br(resumo.group(1))
        metadados["data_vencimento"] = parse_data_br(resumo.group(2))
        metadados["limite_total"] = parse_valor_br(resumo.group(3))

    return metadados


def gerar_id_fatura(data_fechamento_iso: str) -> str:
    ano_mes = data_fechamento_iso[:7]  # 'YYYY-MM'
    return f"mp_{ano_mes}"


def gerar_id_lancamento(id_fatura: str, cartao_final: str, data_compra: str, descricao: str, valor: float) -> str:
    chave = f"{id_fatura}|{cartao_final}|{data_compra}|{descricao.strip()}|{valor:.2f}"
    return hashlib.sha256(chave.encode("utf-8")).hexdigest()[:16]


def extrair_lancamentos(texto: str, id_fatura: str, data_fechamento_iso: str) -> list[dict]:
    lancamentos = []
    cartao_atual = None
    for linha in texto.splitlines():
        linha = linha.strip()
        if not linha:
            continue

        secao = RE_CARTAO_SECAO.search(linha)
        if secao:
            cartao_atual = secao.group(1)
            continue

        match = RE_LANCAMENTO.match(linha)
        if match and cartao_atual:
            dia, mes, descricao, parcela_atual, parcela_total, valor_str = match.groups()
            data_compra = inferir_ano(dia, mes, data_fechamento_iso)
            valor = parse_valor_br(valor_str)
            lancamentos.append(
                {
                    "id_lancamento": gerar_id_lancamento(id_fatura, cartao_atual, data_compra, descricao, valor),
                    "id_fatura": id_fatura,
                    "cartao_final": cartao_atual,
                    "data_compra": data_compra,
                    "descricao": descricao.strip(),
                    "valor": valor,
                    "parcela_atual": int(parcela_atual) if parcela_atual else None,
                    "parcela_total": int(parcela_total) if parcela_total else None,
                }
            )
    return lancamentos


def importar(pdf_path: Path, db_path: Path) -> tuple[str, int, int]:
    texto = extrair_texto(pdf_path)
    metadados = extrair_metadados(texto)

    if "data_fechamento" not in metadados:
        raise ValueError("Não foi possível localizar 'Fechamento da fatura' no PDF — verifique o layout extraído.")

    id_fatura = gerar_id_fatura(metadados["data_fechamento"])
    lancamentos = extrair_lancamentos(texto, id_fatura, metadados["data_fechamento"])

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO faturas_cartao
                (id_fatura, emitida_em, data_fechamento, data_proximo_fechamento,
                 data_vencimento, limite_total, limite_utilizado, limite_disponivel,
                 valor_total_fatura, compras_parceladas_futuras, status_pagamento)
            VALUES
                (:id_fatura, :emitida_em, :data_fechamento, :data_proximo_fechamento,
                 :data_vencimento, :limite_total, :limite_utilizado, :limite_disponivel,
                 :valor_total_fatura, :compras_parceladas_futuras, 'aberta')
            ON CONFLICT(id_fatura) DO UPDATE SET
                emitida_em = excluded.emitida_em,
                data_fechamento = excluded.data_fechamento,
                data_proximo_fechamento = excluded.data_proximo_fechamento,
                data_vencimento = excluded.data_vencimento,
                limite_total = excluded.limite_total,
                limite_utilizado = excluded.limite_utilizado,
                limite_disponivel = excluded.limite_disponivel,
                valor_total_fatura = excluded.valor_total_fatura,
                compras_parceladas_futuras = excluded.compras_parceladas_futuras
            """,
            {
                "id_fatura": id_fatura,
                "emitida_em": metadados.get("data_fechamento"),
                "data_fechamento": metadados.get("data_fechamento"),
                "data_proximo_fechamento": metadados.get("data_proximo_fechamento"),
                "data_vencimento": metadados.get("data_vencimento"),
                "limite_total": metadados.get("limite_total"),
                "limite_utilizado": metadados.get("limite_utilizado"),
                "limite_disponivel": metadados.get("limite_disponivel"),
                "valor_total_fatura": metadados.get("valor_total_fatura"),
                "compras_parceladas_futuras": metadados.get("compras_parceladas_futuras"),
            },
        )

        inseridos = 0
        ignorados = 0
        for lancamento in lancamentos:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO lancamentos_fatura
                    (id_lancamento, id_fatura, cartao_final, data_compra, descricao,
                     valor, parcela_atual, parcela_total, status)
                VALUES
                    (:id_lancamento, :id_fatura, :cartao_final, :data_compra, :descricao,
                     :valor, :parcela_atual, :parcela_total, 'revisar_manual')
                """,
                lancamento,
            )
            if cursor.rowcount:
                inseridos += 1
            else:
                ignorados += 1

        conn.commit()
    finally:
        conn.close()

    return id_fatura, inseridos, ignorados


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python scripts/parse_fatura_cartao.py caminho/fatura.pdf [caminho_do_banco]")
        sys.exit(1)

    pdf_path = Path(sys.argv[1])
    db_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_DB_PATH

    id_fatura, inseridos, ignorados = importar(pdf_path, db_path)
    print(f"Fatura {id_fatura}: {inseridos} lançamentos inseridos, {ignorados} já existiam, em {db_path}")
