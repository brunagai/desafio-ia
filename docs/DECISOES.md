# Decisões de Arquitetura e Trade-offs

## Trade-offs

**Data nula (`OP-0017` e equivalentes no Nível 2).** Isolar em exceção, sem imputar. Em PLD, inventar calendário corrompe janela de fracionamento e trilha de auditoria.

**Cálculo vs. redação.** Soma, mediana, contagem e cortes (R$ 50 mil / R$ 20 mil / 5×) ficam no pandas / Python. A LLM só interpreta evidência das ferramentas. Pedir para o modelo “decidir se passou do limite” foi recusado de propósito.

**Nível 2: replicar regras no `agente.py` em vez de um módulo compartilhado com o notebook.** Mais rápido para a entrega de 24 h; o custo é duplicação. Com mais tempo extrairia `regras.py` e o notebook só importaria — validaria rerodando o Nível 1 e conferindo os mesmos `CLI-A-1` / `OP-0013`.

**Agente não chama as três ferramentas sempre.** `historico_cliente` é linha de base para todo cliente sinalizado — sem o comportamento habitual não dá para separar rotina de desvio. As outras duas são condicionais: fracionamento → `operacoes_do_dia` da janela; atípico → `perfil_canal`. Nenhum cliente recebe as três por padrão. A primeira versão poupava também o histórico no fracionamento, e o resultado apareceu no lote: `CLI-029` saiu como risco baixo vendo um único dia, sem volume nem perfil para comparar. Economizar a chamada custou a análise. Na rodada atual o mesmo cliente sai como médio e a justificativa cita as 16 operações e os R$ 191.385,77 de volume acumulado — a evidência que faltava está no texto do parecer.

**Gemini e Groq.** Duas chaves no `.env`, nunca no git. Gemini 403 nesta conta; Groq autenticou, mas `llama-3.1-8b-instant` não existe nela. A chamada que vale é `openai/gpt-oss-20b`.

**Critério do confronto: por tipologia, não por acúmulo de flags.** A primeira versão seguia o exemplo do enunciado — alto só com as duas famílias de regra, médio com uma. Nesta base isso não mede nada: nenhum cliente do top 10 dispara as duas, então o risco esperado é “médio” nas dez linhas e um agente que respondesse sempre “médio” cravaria 100%. A taxa mediria a frequência de uma palavra.

O critério principal passou a olhar o que cada regra captura. Janela de fracionamento é smurfing — estruturação deliberada para ficar abaixo do limite — e sai como **alto** pela natureza da tipologia, não por soma de flags. Valor atípico isolado fica em **médio**, porque 5× a mediana gera falso positivo fácil em operação grande pontual. O critério do enunciado continua calculado lado a lado, como análise de sensibilidade.

**Reportar o piso junto com a taxa.** `confronto.py` publica, para cada critério, a taxa do agente e a taxa do melhor agente constante. Pelo critério de tipologia o agente faz 90% contra um piso de 80%; pelo critério do enunciado faz os mesmos 90% contra um piso de 100%, porque ali responder sempre “médio” é imbatível. Superar o piso num critério e não no outro, com a mesma resposta do modelo, é a demonstração de que a taxa de concordância contra regra pobre não sustenta conclusão sozinha. Os dois números estão no relatório de propósito; o que sustenta a avaliação é a leitura caso a caso das divergências.

**Parecer fora do contrato não vira risco médio.** O caso real foi `CLI-017`: a LLM devolveu `nivel_risco = alto` com quatro red flags e esqueceu a `justificativa`. A versão antiga descartava a resposta inteira e gravava “médio/indeterminada” — que por acaso batia com o esperado e entrava como *acerto*. Perder o alerta por erro de serialização é incidente, não detalhe. Agora há três estados: `ok`, `reparado` (nível de risco válido, campo de texto ausente marcado explicitamente, mais uma chamada pedindo o formato completo) e `sem_parecer` (nada aproveitável). O nível de risco nunca é remendado pelo pipeline; sem ele, o cliente vai para revisão humana e sai da taxa de concordância. Na rodada commitada o `CLI-017` voltou completo já na primeira tentativa (`status: ok`, `tentativas: 1`) e o salvamento não precisou agir — a falha de serialização é intermitente, e é por isso que o tratamento fica no código mesmo sem aparecer nos outputs.

**Falha isolada não derruba o lote.** Cada cliente roda em `try/except` próprio e o `lote.json` é gravado a cada iteração. Uma queda do provedor no décimo cliente não descarta os nove anteriores — em rotina de mesa, reprocessar tudo por causa de um 429 é caro e desnecessário.

## Ambiguidades do enunciado e como foram resolvidas

O enunciado pede para decidir e registrar. Estas foram as quatro decisões:

- **“nenhuma operação isolada atinge R$ 20.000”** → `max < 20.000`, estrito. “Atingir” inclui o valor exato, então R$ 20.000 cravados descaracterizam a janela.
- **“soma ultrapassa R$ 50.000”** → `soma > 50.000`, estrito, pelo mesmo raciocínio.
- **Mediana da Regra 2 inclui a própria operação candidata.** Não é neutro: em cliente com poucas operações, um outlier grande puxa a mediana e dificulta o próprio disparo. Manter é a leitura literal do enunciado (“mediana dos valores daquele cliente”); a alternativa seria mediana dos demais, mais sensível e menos reprodutível.
- **`n_sinalizacoes` soma janelas de fracionamento (dias) com operações atípicas (unidades).** São grandezas diferentes no mesmo contador. É o ranking que o enunciado pede — “número de sinalizações” — mas ele favorece quem tem muitas operações atípicas sobre quem tem uma janela de fracionamento, que é o alerta mais grave. Por isso o confronto trata fracionamento como alto, independente da contagem.

## Limitações

- Mediana com poucas ops é instável; USD convertido vira outlier fácil (Regra 2).
- Sem cache de LLM: dez chamadas no lote gastam cota e ~36 s (12.502 tokens); em RPM baixo quebraria. Com o retry de formato, um cliente problemático custa duas chamadas.
- O confronto depende de `outputs/lote.json` já gerado; não reexecuta o modelo.
- **A operação sem data é tratada de forma diferente nos dois níveis.** No Nível 1 ela sai da base analítica inteira; no Nível 2 permanece no volume e na mediana, saindo só do agrupamento por dia. A política do Nível 2 é a defensável — excluir apenas das regras que dependem de calendário —, mas a divergência é real e mede: aplicando a regra do Nível 1 à base grande, o top 10 muda (`CLI-001` sai, `CLI-002` entra) e `CLI-029` ganha flag de valor atípico, virando o único cliente com as duas famílias. Unificar exige rerodar o lote e foi deixado de fora do prazo.
- Regras e ferramentas não cobrem contraparte compartilhada, rede ou sazonalidade. Em dados reais de banco isso seria o mínimo da mesa.
- O agente escolhe ferramentas por regra determinística, não por *tool calling* do modelo. A escolha é auditável e reprodutível, que em PLD vale mais; o custo é que ele não formula hipótese fora do catálogo de alertas.

<a id="nivel-2"></a>

## Nível 2 (feito)

Lista dos 10, lote e confronto estão no repo. O que faria diferente desde o começo: um único módulo de limpeza+regras usado pelo notebook e pelo agente, e cache de parecer por `cliente_id` + hash das evidências.

<a id="nivel-3"></a>
<a id="confronto"></a>

## Nível 3 — não feito (Trilha A)

Com mais tempo: estado compartilhado (cliente, flags, evidências, parecer parcial); Triador (segue / para); Investigador (ferramentas do Nível 2); Redator (JSON final); parada se o triador recusar. Diagrama Mermaid em `docs/ARQUITETURA.md`. Só depois do Nível 2 estável. Validação: um caso que para no triador e um que chega ao redator com as mesmas evidências do lote.
