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

**E o quanto esses 90% valem: quase nada, estatisticamente.** Com n=10, 90% contra 80% é a diferença de **um único cliente**, sem significância nenhuma. Pior: o agente respondeu “médio” em 9 das 10 linhas, ou seja, ele é quase o próprio agente constante. Nas duas únicas linhas em que o critério separa alguma coisa — os clientes com janela de fracionamento, onde o esperado é alto — ele acerta **uma de duas** (`CLI-017` certo, `CLI-029` errado). É cara ou coroa. Publicar a taxa sem esta frase seria usar o piso como enfeite em vez de como controle, então a leitura correta desta entrega é: o agente **não** está demonstrado como melhor que um classificador constante; o que a entrega demonstra é o instrumento capaz de revelar isso.

**Divergência tem direção, e as duas direções não custam o mesmo.** Cada linha do confronto carrega `direcao_vs_regra` (`endureceu`, `afrouxou` ou `igual`) e `distancia_niveis`, a distância em degraus na escala baixo < médio < alto. Concordância trata todo erro como um só; em PLD, afrouxar deixa passar e endurecer só custa fila de análise. A rodada atual tem 1 afrouxamento e 0 endurecimentos: o `CLI-029` desceu de alto para médio num cliente com janela de fracionamento, que é o caso em que o ônus da prova é do modelo. O piso também aparece linha a linha (`baseline_rotulo` e `baseline_acerta`), então a taxa do agente constante é recalculável a partir do próprio CSV — e dá para ver onde o agente ganha dele: o `CLI-017` é a linha em que o chute de “médio” erraria e o agente acertou o alto.

**Parecer fora do contrato não vira risco médio.** O caso real foi `CLI-017`: a LLM devolveu `nivel_risco = alto` com quatro red flags e esqueceu a `justificativa`. A versão antiga descartava a resposta inteira e gravava “médio/indeterminada” — que por acaso batia com o esperado e entrava como *acerto*. Perder o alerta por erro de serialização é incidente, não detalhe. Agora há três estados: `ok`, `reparado` (nível de risco válido, campo de texto ausente marcado explicitamente, mais uma chamada pedindo o formato completo) e `sem_parecer` (nada aproveitável). No caminho de parse o nível de risco nunca é remendado; sem ele, o cliente vai para revisão humana e sai da taxa de concordância.

**Ressalva medida depois de escrever isso.** A garantia acima vale para o parse, não para o pipeline inteiro. Uma auditoria adversarial posterior achou duas rotas em que o nível de risco *é* decidido fora do modelo, e as duas continuam abertas nesta entrega — estão listadas em Limitações como "furos conhecidos". Preferi declará-las a corrigir sem reexecutar o lote: mexer no código e deixar `outputs/` desalinhado repetiria o incidente que essa mesma seção descreve. Na rodada commitada o `CLI-017` voltou completo já na primeira tentativa (`status: ok`, `tentativas: 1`) e o salvamento não precisou agir — a falha de serialização é intermitente, e é por isso que o tratamento fica no código mesmo sem aparecer nos outputs.

**Falha isolada não derruba o lote.** Cada cliente roda em `try/except` próprio e o `lote.json` é gravado a cada iteração. Uma queda do provedor no décimo cliente não descarta os nove anteriores — em rotina de mesa, reprocessar tudo por causa de um 429 é caro e desnecessário.

## Ambiguidades do enunciado e como foram resolvidas

O enunciado pede para decidir e registrar. Estas foram as quatro decisões:

- **“nenhuma operação isolada atinge R$ 20.000”** → `max < 20.000`, estrito. “Atingir” inclui o valor exato, então R$ 20.000 cravados descaracterizam a janela.
- **“soma ultrapassa R$ 50.000”** → `soma > 50.000`, estrito, pelo mesmo raciocínio.
- **Mediana da Regra 2 inclui a própria operação candidata.** Não é neutro: em cliente com poucas operações, um outlier grande puxa a mediana e dificulta o próprio disparo. Manter é a leitura literal do enunciado (“mediana dos valores daquele cliente”); a alternativa seria mediana dos demais, mais sensível e menos reprodutível.
- **`n_sinalizacoes` soma janelas de fracionamento (dias) com operações atípicas (unidades).** São grandezas diferentes no mesmo contador. É o ranking que o enunciado pede — “número de sinalizações” — mas ele favorece quem tem muitas operações atípicas sobre quem tem uma janela de fracionamento, que é o alerta mais grave. Por isso o confronto trata fracionamento como alto, independente da contagem.

  **O custo disso, medido:** a base tem **4 clientes com janela de fracionamento** (`CLI-029`, `CLI-017`, `CLI-002`, `CLI-003`) e o lote analisa **2**. `CLI-002` (posição 11) e `CLI-003` (posição 13) caem fora do top 10, enquanto 8 das 10 vagas vão para clientes cuja única flag é valor atípico — a família que eu mesma classifico como falso positivo fácil. Tratar fracionamento como alto no confronto corrige o rótulo de quem entrou; não recupera quem nunca entrou. Em PLD o efeito líquido é que o ranking pedido pelo enunciado enterra a tipologia mais grave, e metade da população de smurfing desta base não foi olhada por agente nenhum. Ordenar por gravidade de tipologia antes de contagem resolveria, mas desviaria do ranking pedido — então fica registrado como consequência assumida, não como descuido.

## Limitações

- Mediana com poucas ops é instável; USD convertido vira outlier fácil (Regra 2).
- Sem cache de LLM: dez chamadas no lote gastam cota e ~36 s (12.502 tokens); em RPM baixo quebraria. Com o retry de formato, um cliente problemático custa duas chamadas.
- O confronto depende de `outputs/lote.json` já gerado; não reexecuta o modelo.
- **A operação sem data é tratada de forma diferente nos dois níveis.** No Nível 1 ela sai da base analítica inteira; no Nível 2 permanece no volume e na mediana, saindo só do agrupamento por dia. A política do Nível 2 é a defensável — excluir apenas das regras que dependem de calendário —, mas a divergência é real e mede: aplicando a regra do Nível 1 à base grande, o top 10 muda (`CLI-001` sai, `CLI-002` entra) e `CLI-029` ganha flag de valor atípico, virando o único cliente com as duas famílias. Unificar exige rerodar o lote e foi deixado de fora do prazo.
- Regras e ferramentas não cobrem contraparte compartilhada, rede ou sazonalidade. Em dados reais de banco isso seria o mínimo da mesa.
- O agente escolhe ferramentas por regra determinística, não por *tool calling* do modelo. A escolha é auditável e reprodutível, que em PLD vale mais; o custo é que ele não formula hipótese fora do catálogo de alertas.

## Furos conhecidos e não corrigidos nesta entrega

Achados de uma segunda auditoria adversarial, feita depois de o repo estar fechado. Nenhum quebra a execução — é por isso que passaram. Estão aqui porque declarar furo aberto vale mais que entregar documento que promete o que o código não faz.

- **Sem credencial, o pipeline fabrica nível de risco.** `chamar_llm` devolve, quando não há `GOOGLE_API_KEY` nem `GROQ_API_KEY`, um JSON com `nivel_risco: "médio"`. Esse registro entra com `status: "ok"` e `fallback_parse: False`, e o `confronto.py` o conta como avaliado legítimo. Quem clonar o repo sem chave e rodar o agente recebe dez pareceres “médio” com aparência de execução real e uma taxa de 80%. O único rastro é `provedor: None` no `lote.csv` — nada no código impede a contagem. O mesmo padrão está no Nível 1: o `except` da célula 24 constrói `AvaliacaoPLD(nivel_risco="médio", …)`. É o incidente do `CLI-017` vivo em dois outros lugares. Correção certa: devolver `parecer: None` e `status: "erro"`, mandando para revisão humana. **Não aplicada** — exige rerodar o lote para manter `outputs/` coerente com o código.
- **O retry de formato pode rebaixar risco.** A segunda chamada pede “não mude a análise, apenas complete o formato”, mas nada impõe isso. Se a primeira resposta vier `alto` sem `justificativa` e a segunda vier bem formatada com `baixo`, o gravado é `baixo` com `status: "ok"` — e o `alto` original desaparece do registro. Correção: guardar o `nivel_risco` da primeira tentativa e escalar divergência entre tentativas para revisão humana, em vez de sobrescrever.
- **Falha de formato fica invisível justamente no caso grave.** Em `_avaliar_divergencia`, a checagem de `fallback_parse` vem depois dos ramos de divergência de risco. Uma linha com reparo de formato **e** afrouxamento em fracionamento recebe a leitura “o ônus da prova é do modelo” sem mencionar que houve reparo — lê falha técnica como julgamento de PLD, a confusão que motivou toda a refatoração. Correção: checar `fallback_parse` primeiro.
- **O desempate do piso não é determinístico.** `baseline_constante` usa `max()` sobre um `set`; em empate (5/5) o rótulo escolhido varia entre processos, porque a ordem de iteração do set varia. Nesta base não há empate (8 “médio” / 2 “alto”), então o número publicado é estável — mas o instrumento que sustenta minha defesa contra a métrica degenerada é, ele mesmo, irreprodutível no caso de empate. Correção: ordenar por `(-contagem, rotulo)`.
- **Queda total do provedor derruba o confronto.** `confrontar()` trata bem o caso de nenhum cliente avaliável (`n_avaliados: 0`), e então `salvar()` levanta `KeyError` ao indexar colunas de um DataFrame vazio. O lote sobrevive à indisponibilidade, o confronto não.
- **O Nível 2 não tem o equivalente ao `df_excecoes`.** `tools.py` descarta em silêncio registro com moeda fora de BRL/USD ou valor negativo, sem contabilizar em nenhum output. Nesta base o caminho é inerte — conferido: só há BRL e USD, nenhum valor negativo, e os 5 descartes reais são duplicatas exatas de `id` —, mas é assimetria injustificada frente ao Nível 1, que registra motivo de exclusão linha por linha.
- ~~Não há teste automatizado.~~ **Fechado:** `nivel_2/test_regras.py` cobre os três casos que faltavam — a janela do `CLI-A-1` que acende, as fronteiras que não acendem (soma abaixo do corte, R$ 50.000 cravados, R$ 20.000 cravados e menos de 3 operações no dia) e a política de data nula do Nível 2, que tira a operação do agrupamento diário mas a mantém no volume. Rodar com `python -m pytest nivel_2/test_regras.py`. Os dois limiares estritos declarados em "Ambiguidades" agora estão fixados por teste, não só por texto: se alguém trocar `>` por `>=`, o teste quebra. O que **não** está coberto é a Regra 2, o roteamento de ferramentas e o parse de resposta da LLM.
- **Nenhum dos números desta entrega foi reexecutado ao escrever esta seção.** O top 10, as ferramentas por cliente e as taxas do confronto foram reconferidos contra `outputs/` e batem com o código commitado; o que não foi refeito é a chamada de LLM.

<a id="nivel-2"></a>

## Nível 2 (feito)

Lista dos 10, lote e confronto estão no repo. O que faria diferente desde o começo: um único módulo de limpeza+regras usado pelo notebook e pelo agente, e cache de parecer por `cliente_id` + hash das evidências.

<a id="nivel-3"></a>
<a id="confronto"></a>

## Nível 3 — não feito (Trilha A)

Com mais tempo: estado compartilhado (cliente, flags, evidências, parecer parcial); Triador (segue / para); Investigador (ferramentas do Nível 2); Redator (JSON final); parada se o triador recusar. Só depois do Nível 2 estável. Validação: um caso que para no triador e um que chega ao redator com as mesmas evidências do lote. Não há diagrama neste repo — a arquitetura acima é texto, e prometer um `ARQUITETURA.md` que não existe seria a mesma desonestidade que este documento tenta evitar.
