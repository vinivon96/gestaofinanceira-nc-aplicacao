"""Importação via upload (tela /importar do dashboard) — mesma lógica do
fluxo manual em scripts/importar_historico.sh (parser -> classificar ->
vincular pagamentos), só que disparada por um arquivo enviado pelo navegador
em vez de um caminho de pasta.

Arquivos enviados são salvos nas mesmas pastas usadas pelo fluxo manual
(dbextratos_reais/, faturas_reais/ — ver CLAUDE.md), pra manter o histórico
de arquivos-fonte num único lugar independente de como foram importados. Em
caso de nome duplicado, um sufixo numérico é adicionado — nunca sobrescreve
um arquivo já existente silenciosamente.

Idempotência: quem garante isso são os parsers (INSERT OR IGNORE / UPSERT por
chave primária) — rodar o mesmo arquivo duas vezes não duplica dado, só o
arquivo salvo em disco ganha um nome novo (ex: extrato_2.csv).
"""
import os
from pathlib import Path

from werkzeug.utils import secure_filename

import classificar
import parse_extrato_inter
import parse_fatura_cartao
import vincular_pagamento_divida
import vincular_pagamento_fatura

ROOT = Path(__file__).resolve().parent.parent
DIR_EXTRATOS = ROOT / "dbextratos_reais"
DIR_FATURAS = ROOT / "faturas_reais"

EXTENSOES_EXTRATO_VALIDAS = {".csv", ".xlsx"}
EXTENSAO_FATURA_VALIDA = ".pdf"


class FormatoInvalidoError(Exception):
    pass


def _salvar_upload(arquivo, diretorio: Path) -> Path:
    diretorio.mkdir(parents=True, exist_ok=True)
    nome = secure_filename(arquivo.filename) or "arquivo"
    base, ext = os.path.splitext(nome)
    destino = diretorio / nome
    sufixo = 2
    while destino.exists():
        destino = diretorio / f"{base}_{sufixo}{ext}"
        sufixo += 1
    arquivo.save(destino)
    return destino


def _rodar_pos_processamento(db_path: Path) -> None:
    classificar.classificar(db_path)
    vincular_pagamento_fatura.vincular(db_path)
    vincular_pagamento_divida.vincular(db_path)


def importar_extrato_upload(arquivo, db_path: Path) -> dict:
    extensao = Path(arquivo.filename).suffix.lower()
    if extensao not in EXTENSOES_EXTRATO_VALIDAS:
        raise FormatoInvalidoError(f"Formato \"{extensao}\" não suportado — envie um arquivo .csv ou .xlsx.")

    caminho = _salvar_upload(arquivo, DIR_EXTRATOS)
    inseridas, ignoradas = parse_extrato_inter.importar(caminho, db_path)
    _rodar_pos_processamento(db_path)
    return {"nome_arquivo": caminho.name, "inseridas": inseridas, "ignoradas": ignoradas}


def importar_fatura_upload(arquivo, db_path: Path, senha_manual: str | None = None) -> dict:
    extensao = Path(arquivo.filename).suffix.lower()
    if extensao != EXTENSAO_FATURA_VALIDA:
        raise FormatoInvalidoError(f"Formato \"{extensao}\" não suportado — envie um arquivo .pdf.")

    caminho = _salvar_upload(arquivo, DIR_FATURAS)
    id_fatura, inseridos, ignorados = parse_fatura_cartao.importar(caminho, db_path, senha_manual=senha_manual or None)
    _rodar_pos_processamento(db_path)
    return {"nome_arquivo": caminho.name, "id_fatura": id_fatura, "inseridos": inseridos, "ignorados": ignorados}
