# Desafio IA — PLD (Níveis 1 e 2)

Triagem de operações fictícias: regras determinísticas (cálculo) e LLM (parecer). Repositório: https://github.com/brunagai/desafio-ia

## O que foi concluído

- **Nível 1:** limpeza, agregações, duas regras, validação da Regra 1, dois prompts, JSON validado, tokens e latência. Notebook com saídas em `nivel_1/nivel_1.ipynb`.
- **Nível 2:** regras na base maior, top 10, ferramentas, agente, lote e confronto. Resultados em `outputs/`. A seleção de ferramentas é **determinística** (regra em Python sobre as flags), **não** *tool calling* do modelo — o trade-off está em `docs/DECISOES.md`.
- **Nível 3:** não feito; plano em `docs/DECISOES.md`.

Detalhe da autodeclaração: `ENTREGA.yaml`. Uso de IA: `docs/USO_DE_IA.md`.

## Como rodar

```bash
pip install -r requirements.txt
cp .env.example .env   # GOOGLE_API_KEY e/ou GROQ_API_KEY; não commitar
```

- Nível 1: abrir `nivel_1/nivel_1.ipynb` (saídas já commitadas).
- Nível 2: `python nivel_2/agente.py` e `python nivel_2/confronto.py` (lote e confronto já estão em `outputs/`).
- Testes: `python -m pytest nivel_2/test_regras.py` — 6 casos sobre as regras determinísticas, sem chamada de LLM.

A avaliação não depende de rerodar. LLM: Groq `openai/gpt-oss-20b` se o Gemini falhar.
