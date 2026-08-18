-- Schema inicial — Classificador de Fluxo de Caixa (E-commerce)
-- Ver spec-automacao-financeira-ecommerce_1.md, seções 2, 3b e 4.
-- Passo 1 do roadmap (seção 8): apenas schema, sem parsers.

PRAGMA foreign_keys = ON;

-- Plano de contas: lista de categorias usada tanto por `transacoes`
-- quanto por `lancamentos_fatura`, e referenciada por `regras_classificacao`.
CREATE TABLE IF NOT EXISTS plano_de_contas (
    codigo   TEXT PRIMARY KEY,
    categoria TEXT NOT NULL,
    tipo     TEXT NOT NULL CHECK (tipo IN ('receita', 'despesa', 'movimentacao_interna'))
);

-- Regras de classificação automática (aplicadas antes do fallback de IA).
CREATE TABLE IF NOT EXISTS regras_classificacao (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    padrao            TEXT NOT NULL,               -- trecho a buscar em `contraparte`/`descricao`
    categoria_destino TEXT NOT NULL,                -- código do plano de contas
    prioridade        INTEGER NOT NULL DEFAULT 0,
    tabela_alvo       TEXT CHECK (tabela_alvo IN ('transacoes', 'lancamentos_fatura')), -- NULL/vazio = vale para as duas
    FOREIGN KEY (categoria_destino) REFERENCES plano_de_contas (codigo)
);

-- Extrato Inter: entradas e saídas de caixa (tabela única).
CREATE TABLE IF NOT EXISTS transacoes (
    id_transacao             TEXT PRIMARY KEY,      -- hash de (data + descrição + valor)
    data                     DATE NOT NULL,
    tipo                     TEXT NOT NULL CHECK (tipo IN ('entrada', 'saida')),
    contraparte              TEXT NOT NULL,
    valor                    NUMERIC NOT NULL CHECK (valor >= 0),
    categoria                TEXT,                  -- código do plano de contas
    origem_receita           TEXT CHECK (origem_receita IN ('mercado_livre', 'shopee', 'outro', 'nao_receita')),
    confianca_classificacao  NUMERIC CHECK (confianca_classificacao BETWEEN 0 AND 1),
    status                   TEXT NOT NULL DEFAULT 'revisar_manual' CHECK (status IN ('classificado', 'revisar_manual')),
    id_fatura                TEXT,                  -- fatura de cartão paga por esta saída (ver scripts/vincular_pagamento_fatura.py)
    FOREIGN KEY (categoria) REFERENCES plano_de_contas (codigo),
    FOREIGN KEY (id_fatura) REFERENCES faturas_cartao (id_fatura)
);

-- Faturas de cartão (ex: fatura Mercado Pago consolidando múltiplos cartões físicos).
-- Campos conforme seção 3b (versão atualizada, mais completa que a seção 2).
CREATE TABLE IF NOT EXISTS faturas_cartao (
    id_fatura                  TEXT PRIMARY KEY,     -- ex: mp_2026-06
    emitida_em                 DATE,
    data_fechamento            DATE,
    data_proximo_fechamento    DATE,
    data_vencimento            DATE,
    limite_total                NUMERIC,
    limite_utilizado             NUMERIC,
    limite_disponivel           NUMERIC,
    valor_total_fatura          NUMERIC,
    compras_parceladas_futuras  NUMERIC,             -- soma de parcelas a vencer em faturas futuras
    status_pagamento           TEXT NOT NULL DEFAULT 'aberta' CHECK (status_pagamento IN ('aberta', 'paga', 'atrasada'))
);

-- Lançamentos individuais dentro de cada fatura.
CREATE TABLE IF NOT EXISTS lancamentos_fatura (
    id_lancamento             TEXT PRIMARY KEY,
    id_fatura                 TEXT NOT NULL,
    cartao_final              TEXT,                  -- últimos 4 dígitos do cartão físico
    data_compra                DATE NOT NULL,
    descricao                  TEXT NOT NULL,
    valor                       NUMERIC NOT NULL,     -- valor da parcela atual, não o total da compra
    parcela_atual               INTEGER,               -- nulo se à vista
    parcela_total                INTEGER,               -- nulo se à vista
    categoria                   TEXT,                  -- código do plano de contas
    confianca_classificacao     NUMERIC CHECK (confianca_classificacao BETWEEN 0 AND 1),
    status                      TEXT NOT NULL DEFAULT 'revisar_manual' CHECK (status IN ('classificado', 'revisar_manual')),
    FOREIGN KEY (id_fatura) REFERENCES faturas_cartao (id_fatura),
    FOREIGN KEY (categoria) REFERENCES plano_de_contas (codigo)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_regras_classificacao_padrao ON regras_classificacao (padrao, tabela_alvo);
CREATE INDEX IF NOT EXISTS idx_transacoes_data ON transacoes (data);
CREATE INDEX IF NOT EXISTS idx_transacoes_tipo ON transacoes (tipo);
CREATE INDEX IF NOT EXISTS idx_lancamentos_fatura_id_fatura ON lancamentos_fatura (id_fatura);
CREATE INDEX IF NOT EXISTS idx_lancamentos_fatura_data_compra ON lancamentos_fatura (data_compra);
