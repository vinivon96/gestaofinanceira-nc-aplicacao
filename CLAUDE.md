# Convenções do projeto — Automação Financeira NC Decorações

## Banco de dados
- Caminho fixo: db/financeiro.db (SQLite)
- Schema em db/schema.sql, seeds em db/seed_plano_de_contas.sql e
  db/seed_regras_classificacao.sql
- Sempre rodar scripts/init_db.py após qualquer alteração no schema

## Validação obrigatória
- Depois de criar ou editar qualquer script, sempre RODAR contra dado real
  ou de exemplo antes de considerar a etapa concluída — nunca apenas
  declarar "implementado" sem testar
- Ao editar um parser, confirmar idempotência (rodar duas vezes, sem duplicar)

## Bibliotecas
- Priorizar apenas a biblioteca padrão do Python (stdlib)
- Se precisar de uma biblioteca externa (ex: pdfplumber), perguntar antes de
  instalar e explicar o motivo

## Dados sensíveis
- Nunca hardcode senha, token ou credencial no código — sempre variável de
  ambiente
- Scripts que lidam com PDF protegido devem tentar abrir sem senha primeiro,
  e só recorrer à variável de ambiente se o arquivo realmente exigir

## Estrutura de dados
- Tabelas principais: transacoes, faturas_cartao, lancamentos_fatura,
  plano_de_contas, regras_classificacao
- regras_classificacao tem coluna tabela_alvo para diferenciar regras por
  fonte (transacoes vs lancamentos_fatura); regra sem tabela_alvo vale para
  ambas
- Itens sem categoria automática ficam com status = 'revisar_manual', nunca
  um chute forçado

## Fontes de dados reais (nomes de pastas no projeto)
- Extrato Inter: dbextratos_reais/
- Fatura de cartão Mercado Pago: faturas_reais/

## Roadmap
- O roadmap completo do projeto está em spec-automacao-financeira-ecommerce_1.md,
  seção 8. Seguir a ordem dos passos, um de cada vez, sem pular etapas.
