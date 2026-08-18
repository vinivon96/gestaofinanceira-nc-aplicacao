"""Script utilitário (não faz parte do pipeline) para gerar um PDF de fatura
fictício e protegido por senha, usado só para testar o parser.
"""
from pathlib import Path

from fpdf import FPDF
from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "db" / "exemplos"
OUT_PATH = OUT_DIR / "fatura_cartao_exemplo.pdf"
SENHA_EXEMPLO = "teste123"

# Layout espelha o texto extraído de uma fatura real do Mercado Pago
# (ver faturas_reais/credit-card-mp-statement.pdf): rótulos sem ":",
# resumo do topo com valores numa linha separada dos rótulos, datas de
# lançamento sem ano (dd/mm) e "Parcela X de Y" em vez de "X/Y".
LINHAS = [
    "Fatura Mercado Pago",
    "Emitida em: 16/06/2026",
    "Total a pagar Vence em Limite total Saque total",
    "R$ 5.341,35 22/06/2026 R$ 10.500,00 R$ 50,00",
    "",
    "Movimentacoes na fatura",
    "",
    "Cartao Visa [************7111]",
    "16/06 INNER AI R$ 199,90",
    "17/06 CLAUDE.AI SUBSCRIPTION Parcela 2 de 6 R$ 89,90",
    "18/06 HOSTINGER R$ 45,00",
    "19/06 UBER R$ 32,50",
    "Total R$ 367,30",
    "",
    "Cartao Visa [************7787]",
    "20/06 PAYGO R$ 120,00",
    "21/06 CASA DAS FITAS E PERFI Parcela 3 de 6 R$ 78,20",
    "22/06 DEBORA APARECIDA FIJOS R$ 300,00",
    "Total R$ 498,20",
    "",
    "Datas importantes",
    "Melhor dia de compra 16/06/2026",
    "Fechamento da fatura 15/06/2026",
    "Proximo fechamento 15/07/2026",
    "Limite utilizado R$ 10.464,88",
    "Limite disponivel R$ 35,12",
    "Compras parceladas R$ 5.123,53",
]


def gerar():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    for linha in LINHAS:
        pdf.cell(0, 6, text=linha, new_x="LMARGIN", new_y="NEXT")

    tmp_path = OUT_DIR / "_tmp_sem_senha.pdf"
    pdf.output(str(tmp_path))

    reader = PdfReader(str(tmp_path))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(SENHA_EXEMPLO)
    with open(OUT_PATH, "wb") as f:
        writer.write(f)

    tmp_path.unlink()
    print(f"Gerado {OUT_PATH} (senha: {SENHA_EXEMPLO})")


if __name__ == "__main__":
    gerar()
