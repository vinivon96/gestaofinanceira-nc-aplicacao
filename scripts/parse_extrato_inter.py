"""Parser do extrato do Banco Inter (CSV ou XLSX) -> tabela `transacoes`.

Formato de entrada (spec, seção 3), tanto em .csv quanto em .xlsx com a
mesma estrutura de colunas:
    Data Lançamento;Histórico;Descrição;Valor;Saldo
    30/06/2026;Pix recebido;Nc Decoracoes Ltda;1.636,48;2.487,94
    26/06/2026;Pix enviado ;Airwallex Singapore Pte Ltd;-149,00;851,46

- separador `;` no CSV (no XLSX cada campo já é uma célula)
- números BR no CSV (vírgula decimal, ponto como milhar); no XLSX o Excel já
  guarda o valor como número
- datas `dd/mm/yyyy` no CSV; no XLSX o Excel já guarda a data como datetime
- `Histórico` indica o sentido (recebido/enviado) -> `tipo`
- `Descrição` = contraparte

O export real do Inter traz algumas linhas de metadados (título, conta,
período, saldo) antes do cabeçalho de verdade, nos dois formatos — o parser
localiza dinamicamente a linha/célula que começa com "Data Lançamento" e lê
a partir dali. O formato é detectado pela extensão do arquivo (.csv vs
.xlsx).

Este parser só grava dados brutos: `categoria`, `origem_receita` e
`confianca_classificacao` ficam em aberto para o motor de classificação
(passo 4 do roadmap). Toda linha entra com status = 'revisar_manual'.

Uso:
    python scripts/parse_extrato_inter.py caminho/extrato.csv [caminho_do_banco]
    python scripts/parse_extrato_inter.py caminho/extrato.xlsx [caminho_do_banco]
"""
import csv
import hashlib
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "db" / "financeiro.db"

CSV_ENCODING = "utf-8-sig"  # exports do Inter costumam vir com BOM


def parse_valor_br(texto: str) -> float:
    """'1.636,48' -> 1636.48 ; '-149,00' -> -149.0"""
    texto = texto.strip().replace(".", "").replace(",", ".")
    return float(texto)


def parse_data_br(texto: str) -> str:
    """'30/06/2026' -> '2026-06-30' (formato ISO, ordenável no SQLite)"""
    dia, mes, ano = texto.strip().split("/")
    return f"{ano}-{mes}-{dia}"


def determinar_tipo(historico: str, valor: float) -> str:
    historico_norm = historico.strip().lower()
    if "recebid" in historico_norm:
        return "entrada"
    if "enviad" in historico_norm:
        return "saida"
    # fallback: sinal do valor, caso o histórico não seja um Pix
    return "entrada" if valor >= 0 else "saida"


def gerar_id_transacao(data_iso: str, descricao: str, valor: float) -> str:
    chave = f"{data_iso}|{descricao.strip()}|{valor:.2f}"
    return hashlib.sha256(chave.encode("utf-8")).hexdigest()[:16]


def localizar_inicio_cabecalho(linhas: list[str]) -> int:
    """Retorna o índice da linha que começa o cabeçalho real (contém 'Data Lançamento')."""
    for i, linha in enumerate(linhas):
        if linha.strip().startswith("Data Lançamento"):
            return i
    raise ValueError("Cabeçalho 'Data Lançamento' não encontrado no CSV")


def ler_linhas_csv(csv_path: Path):
    with open(csv_path, encoding=CSV_ENCODING, newline="") as f:
        todas_linhas = f.readlines()

    inicio = localizar_inicio_cabecalho(todas_linhas)
    reader = csv.DictReader(todas_linhas[inicio:], delimiter=";")
    for linha in reader:
        if linha.get("Data Lançamento"):
            data_iso = parse_data_br(linha["Data Lançamento"])
            valor_bruto = parse_valor_br(linha["Valor"])
            valor_abs = abs(valor_bruto)
            contraparte = linha["Descrição"].strip()
            tipo = determinar_tipo(linha["Histórico"], valor_bruto)
            yield {
                "id_transacao": gerar_id_transacao(data_iso, contraparte, valor_bruto),
                "data": data_iso,
                "tipo": tipo,
                "contraparte": contraparte,
                "valor": valor_abs,
            }


def localizar_inicio_cabecalho_xlsx(linhas: list[tuple]) -> int:
    """Mesma ideia de `localizar_inicio_cabecalho`, mas pra linhas do
    openpyxl (tuplas de células, não texto bruto)."""
    for i, linha in enumerate(linhas):
        primeira_celula = linha[0] if linha else None
        if isinstance(primeira_celula, str) and primeira_celula.strip().startswith("Data Lançamento"):
            return i
    raise ValueError("Cabeçalho 'Data Lançamento' não encontrado no XLSX")


def ler_linhas_xlsx(xlsx_path: Path):
    workbook = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)
    try:
        planilha = workbook.active
        linhas = list(planilha.iter_rows(values_only=True))
    finally:
        workbook.close()

    inicio = localizar_inicio_cabecalho_xlsx(linhas)
    cabecalho = [str(celula).strip() if celula is not None else "" for celula in linhas[inicio]]
    idx_data = cabecalho.index("Data Lançamento")
    idx_historico = cabecalho.index("Histórico")
    idx_descricao = cabecalho.index("Descrição")
    idx_valor = cabecalho.index("Valor")

    for linha in linhas[inicio + 1:]:
        data_bruta = linha[idx_data]
        descricao_bruta = linha[idx_descricao]
        if data_bruta is None or not descricao_bruta:
            continue

        if isinstance(data_bruta, datetime):
            data_iso = data_bruta.strftime("%Y-%m-%d")
        else:
            # fallback, caso a célula de data venha como texto "dd/mm/yyyy"
            data_iso = parse_data_br(str(data_bruta))

        valor_bruto = float(linha[idx_valor])
        valor_abs = abs(valor_bruto)
        contraparte = str(descricao_bruta).strip()
        tipo = determinar_tipo(str(linha[idx_historico] or ""), valor_bruto)
        yield {
            "id_transacao": gerar_id_transacao(data_iso, contraparte, valor_bruto),
            "data": data_iso,
            "tipo": tipo,
            "contraparte": contraparte,
            "valor": valor_abs,
        }


def ler_linhas(caminho: Path):
    if caminho.suffix.lower() == ".xlsx":
        return ler_linhas_xlsx(caminho)
    return ler_linhas_csv(caminho)


def importar(caminho: Path, db_path: Path) -> tuple[int, int]:
    conn = sqlite3.connect(db_path)
    inseridas = 0
    ignoradas = 0
    try:
        for transacao in ler_linhas(caminho):
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO transacoes
                    (id_transacao, data, tipo, contraparte, valor, status)
                VALUES (:id_transacao, :data, :tipo, :contraparte, :valor, 'revisar_manual')
                """,
                transacao,
            )
            if cursor.rowcount:
                inseridas += 1
            else:
                ignoradas += 1
        conn.commit()
    finally:
        conn.close()
    return inseridas, ignoradas


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python scripts/parse_extrato_inter.py caminho/extrato.csv|.xlsx [caminho_do_banco]")
        sys.exit(1)

    caminho_arquivo = Path(sys.argv[1])
    db_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_DB_PATH

    inseridas, ignoradas = importar(caminho_arquivo, db_path)
    print(f"{inseridas} transações inseridas, {ignoradas} já existiam (ignoradas) em {db_path}")
