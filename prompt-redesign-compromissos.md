# Prompt — Redesign da tela `/compromissos` (Contas & Dívidas)

Contexto: leia `spec-automacao-financeira-ecommerce.md` seção 15 antes de começar (`@spec-automacao-financeira-ecommerce.md`).

## Objetivo

Redesenhar a tela `/compromissos` (`scripts/templates/compromissos.html` +
`scripts/dashboard.py::montar_contexto_compromissos`/`pagina_compromissos` +
`scripts/compromissos.py`). Hoje ela mostra três blocos lado a lado (contas
recorrentes, parcelamentos, dívidas), cada um com sua mini-tabela e seu
formulário de cadastro embaixo. O novo layout deve ter:

1. Uma barra de filtros única no topo da página.
2. Uma única tabela de resultados abaixo, combinando os três tipos.
3. Os formulários de cadastro saem da página e viram um painel lateral
   (drawer) que desliza da direita, aberto por botões "+ Novo" no topo.

Não mexer no bloco "Custo fixo & Prospecção" (fica como está, abaixo da
tabela unificada).

## 1. Barra de filtros (topo)

Campos (todos opcionais, combináveis):

- **Tipo de conta**: select — Recorrente / Parcelamento / Dívida / (todos)
- **Nome / destinatário**: texto livre — busca em `nome` (recorrente),
  `descricao` (parcelamento) e `credor`+`descricao` (dívida)
- **Categoria**: select, populado com `categorias_lista` (já existe no
  contexto atual)
- **Cartão (final)**: texto — só é relevante pra parcelamentos, mas não
  precisa desabilitar quando outro tipo estiver selecionado; se não houver
  match simplesmente não filtra nada daquele tipo
- **Total de parcelas**: número — filtra por `parcela_total` (só
  parcelamentos)
- **Status**: select — Pago / Pendente / Pulada / Projetado / Aberta (a
  lista de opções pode variar conforme o tipo selecionado, ou pode ser uma
  lista única com todos os status possíveis — decida o que for mais simples
  de implementar dado o formato dos dados)
- **Mês** (`input type="month"`): mantém o comportamento atual — controla a
  competência das contas recorrentes e dos parcelamentos (parcelamentos já
  são projetados por mês em `parcelamentos_em_andamento`); dívidas
  continuam mostrando a situação atual, independente do mês

Botões "Aplicar" e "Limpar filtros".

**Decisão de implementação**: pode ser filtro client-side (JS puro sobre as
linhas já renderizadas, sem reload) ou server-side (query params, Python
filtra antes de montar o contexto). Prefira client-side pra campos de
texto/categoria/cartão/parcelas/status (resposta instantânea, sem
round-trip), e mantenha o filtro de mês como está hoje (GET com reload,
porque já reprocessa os dados no backend). Justifique a escolha no resumo
final se decidir diferente.

## 2. Tabela unificada

Uma função nova em `scripts/compromissos.py`, algo como
`linhas_unificadas_compromissos(conn, mes)`, que combina o retorno de
`contas_recorrentes_do_mes`, `parcelamentos_em_andamento` e
`dividas_em_aberto` num formato comum de linha:

```
{
  "tipo": "recorrente" | "parcelamento" | "divida",
  "nome": str,              # nome / descricao / credor conforme o tipo
  "categoria_nome": str,
  "cartao_rotulo": str | None,
  "parcela": str | None,    # "6/12" pra parcelamento, None pros outros
  "vencimento": str,        # "Todo dia 10" / "05/09" / data da dívida
  "valor_fmt": str,
  "status": str,            # pendente/paga/pulada/projetado/aberta
  "status_rotulo": str,
  "id": ...,                # id original, pra manter ações (marcar, excluir etc.)
}
```

Mantenha as funções existentes (`contas_recorrentes_do_mes` etc.) como
estão — essa função só orquestra e normaliza, sem duplicar lógica de
projeção de parcela. Ações por linha (marcar paga, encerrar parcelamento,
atualizar vencimento de dívida) continuam usando as rotas já existentes em
`dashboard.py` — não precisa reescrevê-las, só adaptar os `<form>` da nova
tabela pra apontar pra elas (usando `tipo` e `id` da linha pra decidir qual
ação mostrar).

Colunas da tabela: Tipo, Nome/Descrição, Categoria, Cartão, Parcela,
Vencimento, Valor, Status, Ação.

Mantenha os cards de totais (Esperado/Pago/Pendente) exatamente como
funcionam hoje: referem-se só às contas recorrentes do mês selecionado.
Não somar parcelamentos nem dívidas nesses cards.

## 3. Drawer de cadastro

Três botões no topo da página: "+ Nova conta recorrente", "+ Novo
parcelamento", "+ Nova dívida". Cada um abre um painel lateral (drawer)
deslizando da direita, com o formulário correspondente — os mesmos campos
que já existem em `compromissos.html` hoje (não mude os campos, só o
lugar onde aparecem). O `<form>` de cada drawer aponta pra rota POST que já
existe (`criar_conta_recorrente_rota`, `criar_parcelamento_rota`,
`criar_divida_rota`) — sem mudança de backend nessa parte.

Comportamento esperado do drawer:
- Abre com um clique no botão correspondente, sem reload de página.
- Fecha com X, clique fora, ou Esc.
- Implementação em JS puro (sem framework) é suficiente — o resto do app
  não usa nenhum framework JS.

## 4. Estilo

Siga o tema já existente em `scripts/static/estilo.css` (dark, gradiente
sutil, variáveis `--bg-panel`, `--bg-panel-2`, `--borda`, `--texto`,
`--texto-fraco`, `--verde`, `--laranja`, badges `.badge-*`, botão primário
com gradiente azul/roxo). Não crie um novo sistema de cores — reaproveite
as classes existentes (`.badge-pendente`, `.badge-paga`, `.badge-projetado`
etc.) e adicione só o CSS necessário pro drawer (overlay + painel) e pra
barra de filtros.

## 5. O que não mudar

- Rotas POST existentes (criar/marcar/desmarcar/excluir/encerrar/atualizar)
  continuam com a mesma assinatura.
- Bloco de prospecção de custo fixo, no fim da página.
- Lógica de projeção de parcela em `compromissos.py` (`_parcela_projetada`,
  `parcelamentos_em_andamento` etc.) — a nova função de unificação só
  consome o que já existe.

## Entrega

Ao terminar, rode a aplicação localmente e confirme visualmente:
1. A barra de filtros aparece no topo, funcional.
2. A tabela única mostra os três tipos misturados, ordenável/filtrável.
3. Os três drawers abrem e fecham corretamente, e o cadastro continua
   funcionando (POST nas rotas certas).
4. Nada quebrou nas ações por linha (marcar paga, pular, excluir,
   encerrar parcelamento, atualizar vencimento de dívida).

Peça confirmação antes de rodar qualquer migração de schema — este
redesenho é só de camada de apresentação (`compromissos.py` ganha uma
função nova de leitura, `dashboard.py` e `compromissos.html` mudam; o
schema em `db/schema.sql` não deveria precisar mudar).
