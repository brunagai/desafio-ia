# Relatório de Uso de Inteligência Artificial

Durante o desenvolvimento deste desafio, diferentes ferramentas de inteligência artificial foram combinadas para maximizar a qualidade e a eficiência da entrega:

1. **Concepção e Engenharia de Prompts (Google Gemini):** O Gemini foi utilizado estrategicamente em momentos de planejamento para refinar a construção de prompts estruturados, especialmente na comparação de abordagens (Prompt A vago vs. Prompt B especializado em PLD com restrição estrita de JSON) e na estruturação lógica da validação por Pydantic.
2. **Implementação e Codificação (Cursor):** O Cursor atuou como assistente principal no ecossistema de desenvolvimento, auxiliando na sintaxe inicial de manipulação de DataFrames com Pandas, estruturação do notebook e criação das rotinas de fallback.

### Ajustes e Correções Manuais Cruciais:
- **Resiliência e Fallbacks:** A IA inicialmente gerava chamadas diretas que quebravam caso a estrutura do JSON viesse malformada. Foi implementada manualmente a tratativa defensiva de fallback para capturar respostas fora do contrato.
- **Isolamento de Credenciais:** Garantia estrita de que nenhuma chave de API fosse hardcoded, utilizando variáveis de ambiente via `.env` e mantendo o `.gitignore` configurado.
