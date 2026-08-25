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

## Segunda rodada de auditoria — e o que ela achou de errado nos meus próprios documentos

Repeti o exercício da seção 4 depois de o repositório estar fechado, com a mesma instrução de ser impiedoso, mas mirando um alvo diferente: **onde a documentação afirma algo que o código não sustenta.** É o ponto mais fácil de errar de boa-fé, porque o texto envelhece junto com o código e ninguém relê o parágrafo antigo.

Achou seis coisas, e nenhuma delas era falha de execução:

1. **O `README.md` dizia “escolha dinâmica”** para o agente, enquanto `DECISOES.md` e este documento explicam que a seleção de ferramentas é determinística. O README é o primeiro arquivo que qualquer avaliador abre — a contradição sugeria que a honestidade dos documentos internos tinha sido escrita depois, para cobrir a lacuna. Corrigido.
2. **`DECISOES.md` afirmava que “o nível de risco nunca é remendado pelo pipeline”.** Falso como afirmação geral: sem credencial, `chamar_llm` fabrica `nivel_risco: "médio"` e o registro entra como `status: "ok"`; e o `except` da célula 24 do Nível 1 faz o mesmo. Ou seja, o defeito que eu identifiquei, documentei e corrigi no `CLI-017` continuava vivo em dois outros pontos. A frase foi restringida ao caminho de parse, que é onde a garantia realmente vale, e as duas rotas abertas foram para “Furos conhecidos”.
3. **A taxa de 90% estava apresentada como vitória sobre o piso de 80%.** É verdade e é irrelevante: com n=10 isso é um cliente de diferença, o agente respondeu “médio” em 9 das 10 linhas e acerta 1 de 2 nas únicas linhas em que o critério discrimina. Eu tinha construído o instrumento certo para detectar métrica degenerada e não o apliquei ao meu próprio resultado. A ressalva estatística entrou no documento.
4. **Eu reconhecia o viés do `n_sinalizacoes` sem medir a consequência.** A base tem 4 clientes com janela de fracionamento e o lote analisa 2: `CLI-002` e `CLI-003` ficam fora do top 10. Tratar fracionamento como alto no confronto corrige o rótulo de quem entrou, não recupera quem nunca entrou. Reconhecer um viés e não quantificá-lo é meio caminho — o número entrou.
5. **`DECISOES.md` citava um `docs/ARQUITETURA.md` que não existe.** Referência removida.
6. **`ENTREGA.yaml` apontava a limpeza nas “células 4–10”**, mas todas as funções de carga, parsing e validação de contrato estão na célula 2. Intervalo corrigido.

Além dessas, a auditoria confirmou por recálculo independente o que os documentos afirmam: as regras do Nível 1, a normalização de `OP-0013`, o top 10 e as ferramentas por cliente reproduzem exatamente os `outputs/` commitados, os seis commits citados aqui existem e correspondem, e não há credencial em nenhum objeto do histórico git.

**Por que os furos ficaram abertos.** Corrigir o código dos itens 2 e 3 exige rerodar o lote para não deixar `outputs/` descrevendo um comportamento que o código não tem mais — que é exatamente o incidente da seção “Os outputs commitados não vinham do código commitado”. Sem tempo para reexecutar e reconferir, a escolha foi **declarar o furo em vez de mascará-lo**. Um documento que promete uma garantia que o código não dá é pior que um documento que aponta onde a garantia falha.

## Divisão de trabalho e limites

O critério que apliquei o tempo todo: **a IA digita, o humano decide.** Toda decisão com consequência de compliance — imputar ou não uma data, o que fazer com um parecer fora do contrato, qual critério de confronto é honesto, se um resultado ruim entra no relatório — foi tomada por mim e registrada em `docs/DECISOES.md` com o trade-off explícito. O assistente escreveu a maior parte do código sob essas instruções e foi mais útil ainda como revisor adversarial do que como gerador.

Também vale registrar o que a IA **não** decidiu: nenhuma chave de API foi escrita em código. As credenciais ficam em `.env`, fora do versionamento pelo `.gitignore`, e o repositório declara em `ENTREGA.yaml` qual provedor e modelo geraram os resultados.
