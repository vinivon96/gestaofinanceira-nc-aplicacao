-- Plano de contas sugerido (spec, seção 4).
-- Códigos são livres — o que importa é manter consistência entre
-- plano_de_contas, regras_classificacao e o relatório.

INSERT OR IGNORE INTO plano_de_contas (codigo, categoria, tipo) VALUES
    ('vendas_mercado_livre',      'Vendas Mercado Livre',            'receita'),
    ('vendas_shopee',             'Vendas Shopee',                   'receita'),
    ('vendas_outros_canais',      'Vendas — outros canais',          'receita'),
    ('outras_receitas',           'Outras receitas',                 'receita'),

    ('fornecedor_materia_prima',  'Fornecedor / matéria-prima',      'despesa'),
    ('embalagens',                'Embalagens',                      'despesa'),
    ('frete_logistica',           'Frete/logística',                 'despesa'),
    ('freelancer_prestador',      'Freelancer/prestador de serviço', 'despesa'),
    ('ferramentas_saas',          'Ferramentas e SaaS',              'despesa'),
    ('ferramentas_ia',            'Ferramentas de IA',               'despesa'),
    ('transporte_deslocamento',   'Transporte/deslocamento',         'despesa'),
    ('marketing_ads',             'Marketing/ads',                   'despesa'),
    ('impostos',                  'Impostos',                        'despesa'),
    ('fornecedor_internacional',  'Fornecedor internacional',        'despesa'),
    ('maquininha_adquirente',     'Maquininha/adquirente',           'despesa'),
    ('outras_despesas',           'Outras despesas',                 'despesa'),
    ('pagamento_fatura',          'Pagamento de fatura',             'movimentacao_interna'),

    ('transferencia_entre_contas', 'Transferência entre contas próprias', 'movimentacao_interna'),

    -- Sentinela usada pelo motor de classificação (não é categoria contábil
    -- real, nunca é gravada em transacoes.categoria/lancamentos_fatura.categoria):
    -- indica que uma regra reconheceu o padrão mas decidiu propositalmente
    -- não classificar automaticamente (ex: "Mercado Pago Instituição", "PAYGO"
    -- — spec seção 5), forçando status = revisar_manual.
    ('revisar_manual',            'A revisar (sem categoria automática)', 'movimentacao_interna');
