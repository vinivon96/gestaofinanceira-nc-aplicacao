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

-- Dívidas fora do cartão (empréstimo, fornecedor, financiamento). Modeladas
-- como parcela fixa + parcelas restantes (cobre tanto dívida parcelada
-- quanto pagamento único, com parcelas_restantes=1), pelo mesmo motivo de
-- faturas_cartao: permitir o script de vínculo casar valor + janela de
-- vencimento contra o extrato (ver scripts/vincular_pagamento_divida.py).
CREATE TABLE IF NOT EXISTS dividas (
    id_divida                TEXT PRIMARY KEY,      -- slug, ex: divida_banco-xyz
    credor                   TEXT NOT NULL,
    descricao                TEXT,
    valor_parcela             NUMERIC NOT NULL CHECK (valor_parcela >= 0),
    parcelas_restantes        INTEGER NOT NULL CHECK (parcelas_restantes >= 0),
    data_vencimento_proxima   DATE,
    categoria                 TEXT,
    status                    TEXT NOT NULL DEFAULT 'aberta' CHECK (status IN ('aberta', 'quitada')),
    observacao                TEXT,
    FOREIGN KEY (categoria) REFERENCES plano_de_contas (codigo)
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
    id_divida                TEXT,                  -- dívida paga por esta saída (ver scripts/vincular_pagamento_divida.py)
    FOREIGN KEY (categoria) REFERENCES plano_de_contas (codigo),
    FOREIGN KEY (id_fatura) REFERENCES faturas_cartao (id_fatura),
    FOREIGN KEY (id_divida) REFERENCES dividas (id_divida)
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

-- Contas recorrentes (aluguel, assinaturas etc.): cadastro + lembrete
-- manual, sem tentar conciliar automaticamente com o extrato (decisão
-- consciente — ver spec, seção 15).
CREATE TABLE IF NOT EXISTS contas_recorrentes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    nome             TEXT NOT NULL,
    categoria        TEXT,
    valor_esperado   NUMERIC NOT NULL CHECK (valor_esperado >= 0),
    dia_vencimento   INTEGER NOT NULL CHECK (dia_vencimento BETWEEN 1 AND 31),
    ativa            INTEGER NOT NULL DEFAULT 1 CHECK (ativa IN (0, 1)),
    observacao       TEXT,
    FOREIGN KEY (categoria) REFERENCES plano_de_contas (codigo)
);

-- Uma linha só existe quando o usuário marcou aquele mês (paga/pulada) —
-- meses sem linha são tratados como pendentes pelas consultas em
-- scripts/compromissos.py, sem precisar pré-popular ocorrências futuras.
CREATE TABLE IF NOT EXISTS ocorrencias_conta_recorrente (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    conta_recorrente_id   INTEGER NOT NULL,
    competencia           TEXT NOT NULL,            -- 'YYYY-MM'
    status                TEXT NOT NULL DEFAULT 'paga' CHECK (status IN ('paga', 'pulada')),
    valor_pago            NUMERIC,
    data_pagamento        DATE,
    FOREIGN KEY (conta_recorrente_id) REFERENCES contas_recorrentes (id),
    UNIQUE (conta_recorrente_id, competencia)
);

-- Parcelamentos no cartão cadastrados manualmente (compra feita, ainda não
-- refletida em nenhuma fatura importada). Os que já estão em
-- lancamentos_fatura NÃO entram aqui — scripts/compromissos.py combina os
-- dois na consulta de exibição.
CREATE TABLE IF NOT EXISTS parcelamentos (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    descricao             TEXT NOT NULL,
    valor_parcela         NUMERIC NOT NULL CHECK (valor_parcela >= 0),
    parcela_atual         INTEGER NOT NULL CHECK (parcela_atual >= 1),
    parcela_total         INTEGER NOT NULL CHECK (parcela_total >= parcela_atual),
    cartao_final          TEXT,
    categoria             TEXT,
    ativo                 INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
    observacao            TEXT,
    FOREIGN KEY (categoria) REFERENCES plano_de_contas (codigo)
);

-- Contas bancárias cadastradas manualmente — dado cadastral (banco, agência,
-- número da conta), não movimentação. Hoje só a conta Inter é usada pelos
-- parsers, mas o cadastro já suporta mais de uma, útil como referência.
CREATE TABLE IF NOT EXISTS contas_bancarias (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    banco         TEXT NOT NULL,
    agencia       TEXT,
    numero_conta  TEXT,
    apelido       TEXT,
    ativa         INTEGER NOT NULL DEFAULT 1 CHECK (ativa IN (0, 1))
);

-- Cartões cadastrados manualmente — dado cadastral (banco emissor, final do
-- cartão, dia de fechamento/vencimento habituais). Não é a mesma coisa que
-- `faturas_cartao` (que guarda as datas REAIS de cada fatura já importada):
-- aqui é só referência/lembrete e para exibir um rótulo amigável (banco +
-- apelido) em vez de só os 4 últimos dígitos nas telas de fatura/parcelamento.
CREATE TABLE IF NOT EXISTS cartoes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    banco            TEXT NOT NULL,
    apelido          TEXT,
    final_cartao     TEXT NOT NULL,
    dia_fechamento   INTEGER CHECK (dia_fechamento BETWEEN 1 AND 31),
    dia_vencimento   INTEGER CHECK (dia_vencimento BETWEEN 1 AND 31),
    ativo            INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_regras_classificacao_padrao ON regras_classificacao (padrao, tabela_alvo);
CREATE INDEX IF NOT EXISTS idx_transacoes_data ON transacoes (data);
CREATE INDEX IF NOT EXISTS idx_transacoes_tipo ON transacoes (tipo);
CREATE INDEX IF NOT EXISTS idx_lancamentos_fatura_id_fatura ON lancamentos_fatura (id_fatura);
CREATE INDEX IF NOT EXISTS idx_lancamentos_fatura_data_compra ON lancamentos_fatura (data_compra);
CREATE INDEX IF NOT EXISTS idx_dividas_status ON dividas (status);
CREATE INDEX IF NOT EXISTS idx_ocorrencias_competencia ON ocorrencias_conta_recorrente (competencia);
