# Especificação — Classificador de Fluxo de Caixa (E-commerce)

**Contexto:** Movimentação financeira concentrada no Banco Inter PJ (API disponível). Vendas via marketplaces (Mercado Livre, Shopee, e futuramente outros canais).

**Objetivo do projeto:** ler o extrato bancário, **classificar automaticamente cada entrada e saída**, e gerar um relatório periódico (semanal/mensal) do tipo:

```
Período: 01–07/07/2026

ENTRADAS: R$ 3.000
  Mercado Livre .......... R$ 1.500
  Shopee .................. R$ 1.500

SAÍDAS: R$ 400
  Fornecedor .............. R$ 300
  Freelancer .............. R$ 100
```

Não é reconciliação contábil formal nem conferência venda-a-venda — é **visibilidade de fluxo de caixa categorizado**, para identificar rapidamente de onde vem o dinheiro e para onde ele vai.

---

## 1. Fontes de dados

**Neste primeiro momento:**
- Extrato Banco Inter (API PJ, ou CSV como fallback) — fonte única para entradas e saídas de caixa.
- Fatura do cartão Mercado Livre — fonte separada, com suas próprias despesas e metadados (vencimento, fechamento, limite). Ver seção 3b.

**Adiada para uma fase futura, se fizer sentido:**
- Extrato Mercado Pago — traria mais precisão sobre a origem exata das vendas do Mercado Livre (ver observação na seção 3), mas não é necessário para começar. Enquanto não entrar, a receita ML aparece no relatório com a data em que o dinheiro chegou no Inter, não a data da venda.

---

## 2. Modelo de dados

### `transacoes` (tabela única, alimentada pelo extrato Inter)
| Campo | Tipo | Descrição |
|---|---|---|
| id_transacao | text (PK) | hash de (data + descrição + valor) |
| data | date | |
| tipo | text | entrada / saida |
| contraparte | text | nome de quem enviou/recebeu (campo `Descrição` no CSV Inter) |
| valor | numeric | sempre positivo; o campo `tipo` já indica o sentido |
| categoria | text | preenchida pelo motor de classificação (ver plano de contas) |
| origem_receita | text | canal de venda, se `tipo = entrada` (mercado_livre / shopee / outro / não_receita) |
| confianca_classificacao | numeric | 0 a 1, gerada pela IA quando não há regra |
| status | text | classificado / revisar_manual |

### `faturas_cartao` (metadados de cada fatura, ex: cartão Mercado Livre)
| Campo | Tipo | Descrição |
|---|---|---|
| id_fatura | text (PK) | ex: `ml_2026-07` |
| cartao | text | identifica o cartão (útil se você tiver mais de um no futuro) |
| data_fechamento | date | |
| data_vencimento | date | |
| limite_total | numeric | |
| valor_total_fatura | numeric | soma dos lançamentos importados |
| status_pagamento | text | aberta / paga / atrasada |

### `lancamentos_fatura` (itens dentro de cada fatura, alimentado pelo export do cartão)
| Campo | Tipo | Descrição |
|---|---|---|
| id_lancamento | text (PK) | |
| id_fatura | FK → faturas_cartao | |
| data_compra | date | |
| descricao | text | |
| valor | numeric | |
| parcela | text | ex: "2/6", se houver |
| categoria | text | preenchida pelo motor de classificação (mesmo plano de contas de `transacoes`) |
| confianca_classificacao | numeric | |
| status | text | classificado / revisar_manual |

> O **pagamento** da fatura (quando sai do Inter no vencimento) aparece em `transacoes` como uma saída única — deve ser classificado como "Pagamento de fatura" (movimentação interna, não uma despesa nova, já que os gastos já foram contados individualmente em `lancamentos_fatura`). Assim evita contar o mesmo gasto duas vezes no relatório.

### `plano_de_contas`
| Campo | Tipo |
|---|---|
| codigo | text (PK) |
| categoria | text |
| tipo | enum — receita / despesa / movimentação_interna |

### `regras_classificacao`
| Campo | Tipo |
|---|---|
| padrao | text — trecho a buscar em `contraparte` |
| categoria_destino | text — código do plano de contas |
| prioridade | int |

---

## 3. Formato real dos arquivos (Inter)

CSV: `Data Lançamento;Histórico;Descrição;Valor;Saldo` — separador `;`, números BR (vírgula decimal), datas `dd/mm/yyyy`.
```
30/06/2026;Pix recebido;Nc Decoracoes Ltda;1.636,48;2.487,94
26/06/2026;Pix enviado ;Airwallex Singapore Pte Ltd;-149,00;851,46
```
`Histórico` = "Pix recebido" / "Pix enviado" → mapeia direto para `tipo` (entrada/saída). `Descrição` = contraparte.

**Padrão importante identificado:** o Mercado Livre não repassa direto pro Inter. O fluxo real é: venda ML → cai numa carteira Mercado Pago → você transfere manualmente pro Inter. No extrato Inter isso aparece como `Pix recebido: Nc Decoracoes Ltda` (a própria razão social, porque é transferência entre contas suas) — **não** como um nome ligado ao Mercado Livre. Já a Shopee cai direto (`Shpp Brasil Instituição de Pagamento...`), então esse canal não tem esse problema.

**Decisão adotada:** por enquanto, classificar `Pix recebido: [razão social própria]` diretamente como "Vendas Mercado Livre" — sabendo que a data reflete quando o dinheiro chegou no Inter, não a data da venda. Se no futuro você tiver outras transferências entre contas próprias que não sejam receita do ML, essa regra vai precisar de um critério extra (ex: valor mínimo, ou revisão manual dos casos atípicos).

---

## 3b. Fatura de cartão (Mercado Pago — formato real)

**Metadados da fatura** (aparecem na própria fatura, não precisam ser digitados manualmente):
```
Total a pagar: R$ 5.341,35
Vencimento: 22/06/2026
Fechamento da fatura: 15/06/2026
Próximo fechamento: 15/07/2026
Melhor dia de compra: 16/06/2026
Limite total: R$ 10.500,00
Limite utilizado: R$ 10.464,88
Limite disponível: R$ 35,12
Compras parceladas a vencer (compromisso futuro): R$ 5.123,53
```

**Achado importante:** essa fatura é **consolidada em mais de um cartão físico** (ex: Visa final 7111 e Visa final 7787), com padrões de gasto claramente diferentes:
```
Cartão ****7111: INNER AI, CLAUDE.AI SUBSCRIPTION, HOSTINGER, CAPCUT, UBER, CANVA
  → perfil de gasto: ferramentas/SaaS/IA

Cartão ****7787: PAYGO, CASA DAS FITAS E PERFI, DEBORA APARECIDA FIJOS, BAR MERCEARIA
  → perfil de gasto: possivelmente insumos/prestadores/outra frente
```
Vale a pena manter o **cartão como uma dimensão própria** no dado (não só a categoria), porque parece funcionar quase como um centro de custo diferente — cada cartão físico tende a ser usado para um tipo de gasto.

**Também aparece dentro da fatura:** o próprio pagamento da fatura anterior, como uma linha de "Movimentações na fatura" (`Pagamento da fatura de maio/2026`). Isso é só informativo/histórico dentro do PDF — o pagamento real de caixa continua sendo capturado no extrato Inter (seção 3), não aqui.

### Tabelas atualizadas

**`faturas_cartao`**
| Campo | Tipo | Descrição |
|---|---|---|
| id_fatura | text (PK) | ex: `mp_2026-06` |
| emitida_em | date | |
| data_fechamento | date | |
| data_proximo_fechamento | date | |
| data_vencimento | date | |
| limite_total | numeric | |
| limite_utilizado | numeric | |
| limite_disponivel | numeric | |
| valor_total_fatura | numeric | |
| compras_parceladas_futuras | numeric | soma de parcelas a vencer em faturas futuras (compromisso já assumido) |
| status_pagamento | text | aberta / paga / atrasada |

**`lancamentos_fatura`**
| Campo | Tipo | Descrição |
|---|---|---|
| id_lancamento | text (PK) | |
| id_fatura | FK → faturas_cartao | |
| cartao_final | text | últimos 4 dígitos — identifica qual cartão físico |
| data_compra | date | |
| descricao | text | |
| valor | numeric | valor da parcela atual, não o total da compra |
| parcela_atual | int | nulo se à vista |
| parcela_total | int | nulo se à vista |
| categoria | text | preenchida pelo motor de classificação |
| confianca_classificacao | numeric | |
| status | text | classificado / revisar_manual |

---

## 4. Plano de contas sugerido

**Receitas** (`tipo = entrada`)
- Vendas Mercado Livre
- Vendas Shopee
- Vendas — outros canais (extensível conforme novos marketplaces)
- Outras receitas

**Despesas** (`tipo = saida`)
- Fornecedor / matéria-prima (ex: "Suegil", "Central das Chapas")
- Embalagens (ex: "Art Embalagens", "Casa das Fitas e Perfi")
- Frete/logística (ex: "Pex Ta Entregue", "Keeta Delivery")
- Freelancer/prestador de serviço (pagamentos a pessoa física, ex: "Debora Aparecida")
- Ferramentas e SaaS (ex: "Hostinger", "Canva", "CapCut")
- Ferramentas de IA (categoria própria, já que você quer enxergar esse custo separado — ex: "Inner AI", "Claude.ai Subscription")
- Transporte/deslocamento (ex: "Uber")
- Marketing/ads
- Impostos
- Fornecedor internacional (ex: "Airwallex", compras internacionais com IOF)
- Maquininha/adquirente (ex: "PayGo" — se for tarifa de adquirente, não despesa operacional; confirmar natureza)
- Outras despesas

**Movimentação interna** (não entra no relatório de entradas/saídas "reais")
- Transferência entre contas próprias (Inter ↔ Mercado Pago)

> O código exato de cada categoria fica a seu critério na hora de implementar — o que importa é manter a mesma lista entre `plano_de_contas`, `regras_classificacao` e o relatório.

---

## 5. Motor de classificação (regras → fallback IA)

1. Rodar `regras_classificacao` (ordenadas por prioridade) contra `contraparte`.
2. Sem match → mandar pra IA em lote, pedindo `{categoria, confianca}`.
3. `confianca` abaixo de um limiar (ex: 0.7) → `status = revisar_manual`, sem aplicar automaticamente. Você revisa uma vez por período.
4. Toda correção manual vira uma nova `regra_classificacao` — o sistema aprende e cada mês exige menos revisão.

**Regras iniciais validadas no extrato real:**
```
contraparte contém "Shpp Brasil"                 → Vendas Shopee
contraparte = razão social da própria empresa     → Vendas Mercado Livre (via Mercado Pago) [ou "Movimentação interna" se preferir separar]
contraparte contém "Mercado Pago Instituição"     → revisar manual (pode ser ads, fatura, ou aporte)
contraparte contém "Pex Ta Entregue" / "Keeta"    → Frete/logística
contraparte contém "Airwallex"                    → Fornecedor internacional
contraparte contém "Suegil" / "Central das Chapas" → Fornecedor/matéria-prima
contraparte contém "Art Embalagens"                → Embalagens
contraparte = nome de pessoa física                → Freelancer/prestador (revisar natureza na 1ª vez)
```

**Regras iniciais para os lançamentos da fatura de cartão (validadas na fatura real de junho/2026):**
```
descricao contém "INNER AI" ou "CLAUDE.AI"        → Ferramentas de IA
descricao contém "HOSTINGER" ou "CANVA" ou "CAPCUT" → Ferramentas e SaaS
descricao contém "UBER"                            → Transporte/deslocamento
descricao contém "CASA DAS FITAS E PERFI"          → Embalagens
descricao começa com "MP*" + nome de pessoa         → Freelancer/prestador (revisar natureza)
descricao = nome de pessoa física                   → Freelancer/prestador (revisar natureza)
descricao contém "PAYGO"                            → revisar manual (confirmar se é tarifa de maquininha ou compra)
descricao contém "Compra internacional"             → Fornecedor internacional (verificar se acumula IOF à parte)
```

---

## 6. Relatório

**Formato final decidido: dashboard web simples**, rodando localmente. O `relatorio.py` (passo 6) já contém a lógica de agregação — o dashboard consome essa mesma lógica, só troca a saída de texto no terminal por uma página visual (tabelas + gráficos simples), acessível pelo navegador.

Agregação simples por período (semana ou mês, à escolha), combinando as duas fontes:
```sql
-- Entradas por canal (extrato Inter)
SELECT origem_receita, SUM(valor)
FROM transacoes
WHERE tipo = 'entrada' AND data BETWEEN :inicio AND :fim
GROUP BY origem_receita;

-- Saídas por categoria (extrato Inter, excluindo pagamento de fatura p/ não duplicar)
SELECT categoria, SUM(valor)
FROM transacoes
WHERE tipo = 'saida' AND categoria != 'Pagamento de fatura'
  AND data BETWEEN :inicio AND :fim
GROUP BY categoria;

-- Saídas por categoria (dentro da fatura de cartão)
SELECT categoria, SUM(valor)
FROM lancamentos_fatura
WHERE data_compra BETWEEN :inicio AND :fim
GROUP BY categoria;

-- Visão de limite/vencimento
SELECT id_fatura, data_vencimento, limite_total, valor_total_fatura,
       limite_disponivel, compras_parceladas_futuras
FROM faturas_cartao
WHERE status_pagamento = 'aberta';

-- Saídas por cartão (dentro da fatura) — útil como visão de "centro de custo"
SELECT cartao_final, categoria, SUM(valor)
FROM lancamentos_fatura
WHERE data_compra BETWEEN :inicio AND :fim
GROUP BY cartao_final, categoria;
```
As duas primeiras somadas com a terceira dão a visão completa de saídas por categoria (bancárias diretas + gastos no cartão). A quarta ajuda a entender se um dos cartões físicos concentra um tipo de gasto (ex: ferramentas/IA vs. insumos). Formato de saída: dashboard simples, ou resumo automático por e-mail/WhatsApp via n8n.

**Nota sobre extração do PDF:** a fatura do cartão vem protegida por senha. No pipeline automatizado, isso significa que o parser de PDF precisa da senha configurada (variável de ambiente, não hardcoded no código) para abrir o arquivo antes de extrair o texto — bibliotecas como `pypdf`/`pdfplumber` em Python suportam PDFs protegidos por senha diretamente.

---

## 7. Divisão n8n vs Claude Code

- **n8n:** puxar o extrato Inter (API) periodicamente, disparar o pipeline quando uma nova fatura for adicionada, enviar o resumo periódico automaticamente, alertar quando o vencimento da fatura estiver próximo.
- **Claude Code:** parser do CSV/API Inter, parser da fatura, motor de classificação (regras + IA), lógica de linkar pagamento de fatura ao extrato, agregação e geração do relatório.

## 8. Roadmap sugerido

1. Banco de dados + schema (`transacoes`, `faturas_cartao`, `lancamentos_fatura`, `plano_de_contas`, `regras_classificacao`)
2. Parser do extrato Inter (CSV primeiro, depois migrar pra API)
3. Parser da fatura de cartão em PDF (com senha via variável de ambiente) — extrair metadados da fatura + lançamentos por cartão, com parcela atual/total
4. Motor de classificação com as regras iniciais da seção 5, aplicado às duas fontes
5. Lógica de link entre pagamento de fatura (Inter) e a fatura correspondente
6. Relatório de entradas/saídas por período, incluindo visão por cartão (mesmo que só via query + impressão no terminal, pra validar)
7. Revisão manual dos itens de baixa confiança + criação de novas regras
8. **Dashboard web local:** interface simples no navegador (ex: Flask/FastAPI servindo uma página com tabelas e gráficos), reaproveitando a lógica de agregação do `relatorio.py` — com seletor de período (mês/semana/intervalo) e as mesmas 5 visões já validadas no terminal
9. (Opcional, depois) n8n para automatizar a atualização periódica dos dados (rodar os parsers) e, se quiser, alertas de vencimento de fatura

## 10. Fase de melhorias do dashboard (pós-MVP) — CONCLUÍDA

Depois do dashboard básico validado com dados reais, melhorias de interatividade implementadas:

**a) Drill-down por clique:** clicar numa categoria expande a lista de transações/lançamentos individuais que compõem aquele total, no período selecionado.

**b) Reclassificação inline:** trocar a categoria de um item direto na interface, com opção de **criar categoria nova** na hora (cria entrada em `plano_de_contas` automaticamente, tipo inferido pela origem do item). Ao salvar, cria regra em `regras_classificacao` (respeitando `tabela_alvo`) e recalcula o relatório automaticamente, sem reload manual.

**c) Busca por descrição/contraparte:** campo de busca no topo do dashboard, soma o total de itens (entrada + saída, extrato + fatura) que contêm o termo buscado, no período selecionado.

**d) Ranking de fornecedores/contrapartes:** card que agrupa todas as saídas por contraparte exata (não por categoria), em ordem decrescente de valor — clicável para drill-down.

**e) Pendências visíveis no relatório:** itens sem categoria (`status='revisar_manual'`) aparecem como categoria própria "Sem categoria / Revisão pendente" em todos os cards/gráficos, em vez de ficarem ocultos — clicável, com seletor de categoria em cada item para resolver ali mesmo.

Escopo consciente: a referência visual trazida pelo usuário (estilo Power BI) tem dimensões (equipe de vendas, filtro por fornecedor específico) que não existem no modelo de dados atual — não fazem parte desta fase, a menos que surja necessidade real de segmentar por essas dimensões no futuro.

## 11. Fase futura — automação de ingestão (pesquisa em andamento)

Hoje a importação de extrato/fatura é manual (ver seção "fluxo mensal" abaixo).
Avaliando dois caminhos pra automatizar via n8n (já rodando no VPS):

**a) E-mail automático (caminho preferencial, se disponível):** verificar se
o Banco Inter e o Mercado Pago oferecem opção de enviar extrato/fatura por
e-mail automaticamente todo mês (configuração no próprio app). Se sim, o n8n
monitora essa caixa de entrada e baixa os anexos — sem precisar de API nem
certificado digital.

**b) API oficial (fallback para o Inter):** o Banco Inter tem API PJ
(Open Finance) disponível, mas exige cadastro no portal de desenvolvedores e
certificado digital (mTLS) — mais robusto, porém com fricção de setup
inicial. Para o Mercado Pago (fatura do cartão), não há garantia de API
pública para esse fim — e-mail é o caminho mais provável nesse caso.

**Status:** aguardando o usuário verificar se as opções de envio automático
por e-mail existem nos dois apps, antes de decidir a arquitetura final.

### Fluxo mensal manual (enquanto a automação não existe)
```
# Importar um arquivo específico:
python scripts/parse_extrato_inter.py "dbextratos_reais/Extrato-MM-AAAA.csv"
python scripts/parse_fatura_cartao.py "faturas_reais/fatura-mes.pdf"

# OU importar tudo que estiver nas pastas de uma vez (idempotente, aceita
# .csv e .xlsx no caso do extrato):
./scripts/importar_historico.sh

# Depois, sempre:
python scripts/classificar.py
python scripts/vincular_pagamento_fatura.py

# Subir só o banco atualizado pro servidor (não precisa rebuild):
scp db/financeiro.db root@2.25.155.197:/root/automacao-financeira-nc/db/financeiro.db
```

## 12. Infraestrutura de deploy (VPS)

**Servidor:** VPS Hostinger (Ubuntu 24.04), já rodando n8n, Evolution API e Traefik em containers Docker separados.
```
Host: srv1720774.hstgr.cloud
IP: 2.25.155.197
Usuário SSH: root
Pasta do projeto no servidor: /root/automacao-financeira-nc
```

**Deploy via Docker Compose** (não `docker run` manual — abandonado em favor do compose por ser mais robusto e repetível):
- `Dockerfile` — imagem Python enxuta (`python:3.12-slim`), instala `requirements.txt`, copia `scripts/` e `db/`
- `docker-compose.yml` — sobe o container com `restart: unless-stopped`, credenciais vindas de `.env` (nunca hardcoded), volume `./db:/app/db` protegendo o banco de dados contra rebuild
- `.env` (no servidor, **não versionado**) — contém `DASHBOARD_USER`, `DASHBOARD_SENHA`, `DASHBOARD_PORT`
- `deploy.sh` — script único que builda a imagem nova e recria o container, preservando o banco

**Rotina de atualização de código:**
```
# 1. Local, fora do SSH:
scp -r . root@2.25.155.197:/root/automacao-financeira-nc

# 2. Na sessão SSH do servidor:
cd /root/automacao-financeira-nc
./deploy.sh
```

**Rotina de atualização só de dados** (mais rápida, sem rebuild):
```
scp db/financeiro.db root@2.25.155.197:/root/automacao-financeira-nc/db/financeiro.db
```

**Acesso:** `http://2.25.155.197:5000`, autenticação HTTP básica (usuário/senha do `.env`). Compartilhado com sócios com uma única credencial (decisão consciente — ver pendências).

**⚠️ Pendente:** conexão ainda é HTTP puro, sem HTTPS (decisão de adiar, já que exige comprar/configurar domínio + integrar com o Traefik já existente no servidor).

## 13. Bugs encontrados e corrigidos (histórico, para referência)

- **Período da fatura por data de compra vs. fechamento:** corrigido — fatura agora é atribuída inteira ao mês em que fecha (`data_fechamento`), não fatiada por data de compra individual de cada item. Isso revelou que a margem real de junho era ~20,6%, não os ~54,8% que apareciam antes da correção.
- **Extratos em `.xlsx`:** o parser inicialmente só lia `.csv`; foi estendido para aceitar também `.xlsx` (alguns meses do Inter vieram nesse formato apesar do nome do arquivo sugerir CSV).
- **Bug de seleção de categoria "contaminando" outros itens:** ao salvar a categoria de um item no drill-down, todos os outros `<select>` da tela passavam a mostrar a mesma categoria (bug puramente visual — o navegador selecionava a primeira opção da lista por padrão, sem nenhuma opção marcada como `selected` explicitamente). Corrigido com placeholder "Selecionar categoria..." e vínculo exclusivo de cada select ao seu próprio item.
- **Layout cortando conteúdo em zoom 100%:** primeiro a coluna de valor era cortada por descrições longas; depois o botão "Salvar" ficava fora da área visível. Corrigido reestruturando cada item do drill-down em duas linhas empilhadas (info em cima, ação embaixo) em vez de uma linha única.

## 14. Pendências conhecidas (para retomar em conversa futura)

- **Classificações incorretas identificadas, ainda não corrigidas:** "Receita Federal" e "Injeplastec Ind Com" caíram em "freelancer_prestador" pela heurística de "parece nome de pessoa física" — Receita Federal é imposto, Injeplastec parece fornecedor de matéria-prima.
- **PAYGO:** ainda sem categoria definitiva (ficou em revisão manual por decisão consciente) — natureza exata (tarifa de maquininha vs. fornecedor) não confirmada pelo usuário.
- **Itens pendentes acumulados com o histórico jan-jun:** Stripe Brasil Soluções de Pagamento, Prodata Mobility Brasil, Lalamove Tecnologia Brasil, Dlocal Brasil Instituição de Pagamento, entre outros — precisam de revisão manual (agora com as ferramentas de busca/reclassificação no próprio dashboard).
- **Julho ainda não importado** (usuário mencionou ter o arquivo, pendente de colocar na pasta e rodar `importar_historico.sh`).
- **HTTPS/domínio:** adiado, ver seção 12.
- **Usuários separados com permissões (visualização vs. edição):** hoje uma única credencial compartilhada dá acesso total de edição a todos os sócios — considerado aceitável por ora, mas pode precisar de revisão se o uso crescer.
- **Automação de ingestão (e-mail/API):** ver seção 11 — aguardando usuário verificar configurações de envio automático por e-mail no Inter e Mercado Pago.
- **`origem_receita` faltando em alguns itens de entrada:** causa raiz do problema "(sem categoria)" nas entradas foi identificada e corrigida na exibição (agora aparece como "Sem categoria / Revisão pendente" de forma consistente com as saídas) — mas vale confirmar se todos os itens de entrada têm `origem_receita` preenchido corretamente após as importações do histórico completo.

## 15. Fase — Compromissos financeiros (contas recorrentes, parcelamentos, dívidas)

Painel novo (`/compromissos` no dashboard) pra controlar o que **vai vencer**, complementando o fluxo de caixa existente (que só olha o que já aconteceu). Três entidades, com escopos propositalmente diferentes:

- **Contas recorrentes** (`contas_recorrentes` + `ocorrencias_conta_recorrente`): cadastro manual (nome, categoria, valor esperado, dia de vencimento) com lembrete por mês — **sem** tentar conciliar automaticamente com o extrato. O usuário marca "paga"/"pulada" na interface; um mês sem ocorrência registrada é tratado como pendente. Decisão consciente: contas recorrentes não têm o mesmo tratamento de dívidas porque o valor pode variar mês a mês (ex: conta de luz) e a correspondência automática por valor fixo não seria confiável.
- **Parcelamentos**: combina dois lugares — parcelas já visíveis em `lancamentos_fatura` (fatura real já importada, agrupadas pela compra original) e a tabela nova `parcelamentos` (cadastro manual, pra compras feitas mas cuja fatura ainda não fechou). Os manuais têm botão de "encerrar"; os vindos de fatura não (surgem/somem sozinhos conforme a fatura é importada).
- **Dívidas** (`dividas`): ao contrário de contas recorrentes, são rastreadas **junto com o extrato** — modeladas como parcela fixa + parcelas restantes (cobre tanto dívida parcelada quanto pagamento único, com `parcelas_restantes = 1`), o que permite reaproveitar o mesmo mecanismo de `vincular_pagamento_fatura.py`: `scripts/vincular_pagamento_divida.py` procura, para cada dívida aberta, uma saída no extrato com valor ≈ `valor_parcela` numa janela de ±15 dias do próximo vencimento; ao achar, linka a transação (`transacoes.id_divida`), decrementa `parcelas_restantes`, avança o vencimento em +1 mês e marca a dívida como quitada ao zerar. **Limitação assumida:** avanço fixo de +1 mês — dívida com periodicidade diferente exige ajuste manual do campo "próximo vencimento" na interface entre uma parcela e outra.

Rodar `python scripts/vincular_pagamento_divida.py` faz parte do fluxo mensal (`importar_historico.sh`, passo 5/5), mesma lógica de `vincular_pagamento_fatura.py`.
