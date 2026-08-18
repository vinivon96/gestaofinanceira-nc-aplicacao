-- Regras iniciais de classificação (spec, seção 5), validadas no extrato/
-- fatura reais. `padrao` é buscado (contém, case-insensitive) tanto em
-- `transacoes.contraparte` quanto em `lancamentos_fatura.descricao` — é a
-- mesma tabela de regras para as duas fontes, como definido no schema.
--
-- `prioridade` menor roda primeiro. Regras específicas de uma ferramenta/
-- fornecedor vêm antes de regras genéricas "catch-all" (ex: "Compra
-- internacional" só deve pegar o que nenhuma regra mais específica pegou).
--
-- categoria_destino = 'revisar_manual' é a sentinela: a regra reconhece o
-- padrão mas força revisão humana em vez de classificar automaticamente
-- (ex: "Mercado Pago Instituição" pode ser ads, fatura ou aporte; "PAYGO"
-- pode ser tarifa de adquirente ou compra — spec seção 5).
--
-- `tabela_alvo` restringe a regra a 'transacoes' ou 'lancamentos_fatura';
-- NULL vale para as duas (padrão da maioria das regras).

INSERT OR IGNORE INTO regras_classificacao (padrao, categoria_destino, prioridade, tabela_alvo) VALUES
    -- transacoes (extrato Inter) --
    ('Shpp Brasil',            'vendas_shopee',            10, NULL),
    ('Nc Decoracoes Ltda',     'vendas_mercado_livre',      20, NULL),  -- razão social própria: venda ML via Mercado Pago (ver spec seção 3)
    ('Mercado Pago Institui',  'pagamento_fatura',          30, NULL),  -- pagamento da fatura de cartão (revisão manual de 2026-07-09: valor bate com valor_total_fatura)
    ('Pex Ta Entregue',        'frete_logistica',           40, NULL),
    ('Keeta',                  'frete_logistica',           41, NULL),
    ('Airwallex',              'fornecedor_internacional',  50, NULL),
    ('Suegil',                 'fornecedor_materia_prima',  60, NULL),
    ('Central das Chapas',     'fornecedor_materia_prima',  61, NULL),
    ('Art Embalagens',         'embalagens',                70, NULL),

    -- pessoas físicas identificadas em revisão manual (2026-07-09) --
    ('Ana Paula Correa',       'fornecedor_materia_prima',  200, NULL),  -- ver também padrão "55072637" (mesma pessoa, aparece sem espaços na fatura de cartão)
    ('55072637',               'fornecedor_materia_prima',  201, NULL),
    ('Caio Henrique',          'freelancer_prestador',      210, NULL),
    ('Celinha Soares',         'freelancer_prestador',      211, NULL),
    ('Leonardo Pereira Marques Silvestre', 'freelancer_prestador', 212, NULL),
    ('Vinicius De Oliveira Nascimento',    'freelancer_prestador', 213, NULL),
    -- "Debora Aparecida Fijos" é Freelancer/prestador em transacoes, mas
    -- Outras despesas em lancamentos_fatura (fatura de cartão) — duas regras
    -- separadas por tabela_alvo em vez de uma regra ambígua compartilhada.
    ('Debora Aparecida Fijos', 'freelancer_prestador',      214, 'transacoes'),
    ('Debora Aparecida Fijos', 'outras_despesas',           215, 'lancamentos_fatura'),

    -- lancamentos_fatura (fatura de cartão) --
    ('INNER AI',               'ferramentas_ia',            110, NULL),
    ('CLAUDE.AI',               'ferramentas_ia',           111, NULL),
    ('HOSTINGER',               'ferramentas_saas',         120, NULL),
    ('CANVA',                   'ferramentas_saas',         121, NULL),
    ('CAPCUT',                  'ferramentas_saas',         122, NULL),
    ('UBER',                    'transporte_deslocamento',  130, NULL),
    ('CASA DAS FITAS E PERFI',  'embalagens',                140, NULL),
    ('PAYGO',                   'revisar_manual',            150, NULL),  -- aguardando definição de categoria (revisão manual de 2026-07-09)
    ('BAR MERCEARIA E AVICOL',  'outras_despesas',           220, NULL),
    ('Compra internacional',    'fornecedor_internacional',  900, NULL);  -- catch-all: verificar IOF à parte (spec seção 5)
