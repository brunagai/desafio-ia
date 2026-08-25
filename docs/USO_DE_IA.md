# Relatório de Uso de Inteligência Artificial

Ferramentas de IA foram usadas em todo o desenvolvimento deste desafio. Este documento registra onde elas ajudaram, onde erraram e — principalmente — onde o julgamento humano precisou vetar o que a ferramenta propunha. Em PLD, a decisão de o que fazer com um dado ambíguo não é delegável; a digitação é.

## Ferramentas e papéis

1. **Google Gemini — concepção e engenharia de prompts.** Usado no planejamento, para refinar a construção dos prompts estruturados (Prompt A vago vs. Prompt B especializado em PLD com restrição estrita de JSON) e a estruturação lógica da validação por Pydantic.
2. **Cursor — implementação e auditoria.** Assistente principal de codificação: sintaxe de manipulação de DataFrames com pandas, estruturação do notebook e rotinas de fallback. Depois da implementação, foi usado deliberadamente sob persona de auditor técnico, o que rendeu os achados da última seção.

## Casos concretos de correção de rota

### 1. A armadilha da imputação de datas nulas (`OP-0017`)

**Questionamento:** como lidar com a operação sem data no dataset do Nível 1?

**Sugestão inicial da IA:** preencher a data nula por imputação — média do período, data da operação anterior ou estimativa a partir do histórico do cliente — para não perder a linha nas agregações temporais.

**Correção humana:** imputação vetada. Inventar calendário corrompe a janela de fracionamento e a trilha de auditoria: uma data estimada pode criar ou destruir uma janela de 24 h que é justamente o objeto da Regra 1. A operação foi isolada em um DataFrame de exceções, preservando a rastreabilidade. A decisão está registrada em `docs/DECISOES.md`, junto com a limitação que ela gera — Nível 1 e Nível 2 tratam a data nula com políticas diferentes, e a divergência foi medida em vez de escondida.

### 2. O roteamento de ferramentas do agente (Nível 2)

**Questionamento:** quais ferramentas o agente deve chamar para cada cliente sinalizado?

**Primeira versão implementada:** roteamento estritamente condicional, uma ferramenta por tipo de flag — fracionamento chamava `operacoes_do_dia`, valor atípico chamava `perfil_canal`. `historico_cliente` ficava de fora do caso de fracionamento, para economizar chamada.

**O que o resultado mostrou:** economia demais. O `CLI-029` saiu como risco **baixo** olhando um único dia, sem volume acumulado nem comportamento habitual para comparar. Sem linha de base não há como separar rotina de desvio — e a análise foi o preço da chamada poupada.

**Correção:** `historico_cliente` passou a ser linha de base para todo cliente sinalizado, mantendo as outras duas condicionais (commit `0e0cd11`). O efeito é verificável no `outputs/lote.json`: o parecer atual do `CLI-029` cita as 16 operações e os R$ 191.385,77 de volume acumulado para sustentar o nível de risco, evidência que antes não existia no texto.

**Ressalva honesta:** essa seleção é determinística — uma regra em Python decide as ferramentas, não é *tool calling* do modelo. Foi escolha consciente, porque em PLD a decisão auditável e reprodutível vale mais que a autonomia; o custo é que o agente não formula hipótese fora do catálogo de alertas. O trade-off está em `docs/DECISOES.md` e não deve ser lido como roteamento dinâmico pelo LLM.

### 3. Falha de provedor e JSON fora do contrato no meio do lote

**Questionamento:** o que acontece se a LLM devolver JSON malformado ou o provedor cair na décima chamada?

**Sugestão inicial da IA:** a execução em lote nasceu como uma *list comprehension* que confiava no contrato de saída do modelo e não isolava as chamadas. Uma exceção em qualquer cliente derrubava o lote inteiro e descartava os pareceres já obtidos.

**Correção:** a lacuna foi apontada pela auditoria (seção seguinte) e a correção foi decidida e dirigida por mim; o código foi escrito pelo assistente sob essa instrução. Cada cliente passou a rodar em `try/except` próprio, com `lote.json` gravado a cada iteração e campos `status` e `erro` por linha (commit `259ffba`). Reprocessar dez clientes por causa de um 429 no último é caro e desnecessário em rotina de mesa.

### 4. Uso do Cursor como auditor do próprio código

**Abordagem:** com o Nível 1 e o Nível 2 já implementados, o editor foi colocado sob persona de avaliador técnico sênior, com instrução explícita de ser impiedoso e de procurar o que a banca questionaria numa entrevista — falhas de contrato, ausência de isolamento de exceções, tratamento de nulos e rastreabilidade dos `outputs/`.

**Ganho:** a varredura encontrou três problemas que eu não tinha visto e que não apareciam em teste — nenhum deles quebrava a execução, e é exatamente por isso que passariam despercebidos. Estão detalhados abaixo.

## O que a auditoria automatizada encontrou

### A métrica de concordância estava degenerada

O confronto seguia o exemplo do enunciado: alto só quando as duas famílias de regra marcam o mesmo cliente, médio quando uma só. Nesta base, nenhum cliente do top 10 dispara as duas — então o risco esperado era "médio" nas dez linhas, e **um agente que respondesse sempre "médio" cravaria 100%**. A taxa estava medindo a frequência de uma palavra, não julgamento.

A correção foi adotar um critério por tipologia (fracionamento é smurfing, logo alto por natureza), manter o critério do enunciado ao lado como análise de sensibilidade e publicar, para cada um, o piso do melhor agente constante (commit `a83a42b`). O resultado é desconfortável de propósito e está no relatório: pelo critério de tipologia o agente supera o piso (90% contra 80%), pelo critério do enunciado não supera (90% contra 100%). Publicar o piso junto com a taxa é o que impede a métrica de enganar quem lê.

### Uma falha de formatação rebaixava risco em silêncio

No `CLI-017`, a LLM devolveu `nivel_risco = alto` com quatro red flags e esqueceu o campo `justificativa`. A validação Pydantic reprovava a resposta inteira e o pipeline gravava "médio / indeterminada" — que, por coincidência, batia com o risco esperado e entrava na conta como **acerto**. Um alerta de risco alto virava acerto estatístico por erro de serialização.

Passaram a existir três estados de parse: `ok`, `reparado` (nível de risco válido aproveitado, campo de texto ausente marcado explicitamente, mais uma chamada pedindo o formato completo) e `sem_parecer` (nada aproveitável, cliente vai para revisão humana e sai da taxa). O nível de risco nunca é remendado pelo pipeline (commits `8b87c56` e `a1a1a82`). Na rodada commitada o `CLI-017` voltou completo já na primeira tentativa, então o salvamento não aparece nos outputs — a falha de serialização é intermitente, e é justamente por isso que o tratamento fica no código.

### Os outputs commitados não vinham do código commitado

O `outputs/lote.json` do repositório tinha sido gerado antes da correção de roteamento da seção 2, então as ferramentas registradas nele não correspondiam ao que o código faria se rodasse. Nenhum teste pega isso, e um avaliador que rodasse o projeto veria resultado diferente do commitado. O lote foi regerado com credencial válida — 10 de 10 clientes, sem erro e sem reparo de parse — e os artefatos voltaram a ser reprodutíveis (commit `9d290d4`).

## Divisão de trabalho e limites

O critério que apliquei o tempo todo: **a IA digita, o humano decide.** Toda decisão com consequência de compliance — imputar ou não uma data, o que fazer com um parecer fora do contrato, qual critério de confronto é honesto, se um resultado ruim entra no relatório — foi tomada por mim e registrada em `docs/DECISOES.md` com o trade-off explícito. O assistente escreveu a maior parte do código sob essas instruções e foi mais útil ainda como revisor adversarial do que como gerador.

Também vale registrar o que a IA **não** decidiu: nenhuma chave de API foi escrita em código. As credenciais ficam em `.env`, fora do versionamento pelo `.gitignore`, e o repositório declara em `ENTREGA.yaml` qual provedor e modelo geraram os resultados.
