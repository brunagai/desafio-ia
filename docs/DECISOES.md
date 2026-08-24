# Decisões de Arquitetura e Trade-offs

## Trade-offs

**Data nula (`OP-0017` e equivalentes no Nível 2).** Isolar em exceção, sem imputar. Em PLD, inventar calendário corrompe janela de fracionamento e trilha de auditoria.

**Cálculo vs. redação.** Soma, mediana, contagem e cortes (R$ 50 mil / R$ 20 mil / 5×) ficam no pandas / Python. A LLM só interpreta evidência das ferramentas. Pedir para o modelo “decidir se passou do limite” foi recusado de propósito.

**Nível 2: replicar regras no `agente.py` em vez de um módulo compartilhado com o notebook.** Mais rápido para a entrega de 24 h; o custo é duplicação. Com mais tempo extrairia `regras.py` e o notebook só importaria — validaria rerodando o Nível 1 e conferindo os mesmos `CLI-A-1` / `OP-0013`.

**Agente não chama as três ferramentas sempre.** Fracionamento → `operacoes_do_dia` da janela; atípico → `historico_cliente` + `perfil_canal`. Chamar tudo em todo cliente seria script, não agente.

**Gemini e Groq.** Duas chaves no `.env`, nunca no git. Gemini 403 nesta conta; Groq autenticou, mas `llama-3.1-8b-instant` não existe nela. A chamada que vale é `openai/gpt-oss-20b`.

**Critério do confronto.** Alto só se as *duas* famílias de regra marcam o mesmo cliente; uma família só → médio. Concordância = rótulo do agente igual a esse esperado. Ninguém do top 10 tem as duas flags: o “alto automático” não aparece, e isso é dado, não falha de código. Divergir pode ser o agente certo (mediana estoura em operação pontual) ou o agente frouxo (fracionamento classificado como baixo).

## Limitações

- Mediana com poucas ops é instável; USD convertido vira outlier fácil (Regra 2).
- Sem cache de LLM: dez chamadas no lote gastam cota e ~27 s; em RPM baixo quebraria.
- O confronto depende de `outputs/lote.json` já gerado; não reexecuta o modelo.
- Regras e ferramentas não cobrem contraparte compartilhada, rede ou sazonalidade. Em dados reais de banco isso seria o mínimo da mesa.

<a id="nivel-2"></a>

## Nível 2 (feito)

Lista dos 10, lote e confronto estão no repo. O que faria diferente desde o começo: um único módulo de limpeza+regras usado pelo notebook e pelo agente, e cache de parecer por `cliente_id` + hash das evidências.

<a id="nivel-3"></a>
<a id="confronto"></a>

## Nível 3 — não feito (Trilha A)

Com mais tempo: estado compartilhado (cliente, flags, evidências, parecer parcial); Triador (segue / para); Investigador (ferramentas do Nível 2); Redator (JSON final); parada se o triador recusar. Diagrama Mermaid em `docs/ARQUITETURA.md`. Só depois do Nível 2 estável. Validação: um caso que para no triador e um que chega ao redator com as mesmas evidências do lote.
