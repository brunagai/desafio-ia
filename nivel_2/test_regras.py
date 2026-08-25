"""Testes das regras determinísticas.

Fixa os três casos que a entrega afirma nos documentos: a janela que deve
acender, a parecida que não deve, e a política de data nula do Nível 2. Os
valores dos dois primeiros são os do `CLI-A-1` e `CLI-A-3` do Nível 1, para
que a mesma evidência valha nos dois níveis.
"""

from __future__ import annotations

import pytest

from agente import (
    LIMIAR_OPERACAO_ISOLADA,
    LIMIAR_SOMA_FRACIONAMENTO,
    aplicar_regras,
)


def op(op_id: str, cliente_id: str, data: str | None, valor_brl: float) -> dict:
    """Operação mínima com os campos que `aplicar_regras` consome."""
    return {
        "id": op_id,
        "cliente_id": cliente_id,
        "data": data,
        "valor": valor_brl,
        "moeda": "BRL",
        "valor_brl": valor_brl,
        "canal": "pix",
        "tipo": "transferencia",
        "contraparte": "Contraparte X",
        "observacao": "",
    }


def test_regra_1_acende_na_janela_do_cli_a_1():
    """3 ops no dia, soma R$ 54.200 (> 50k) e máx. R$ 18.800 (< 20k) → acende."""
    operacoes = (
        op("OP-0001", "CLI-A-1", "2026-03-09", 18_100.0),
        op("OP-0002", "CLI-A-1", "2026-03-09", 17_300.0),
        op("OP-0003", "CLI-A-1", "2026-03-09", 18_800.0),
    )

    (sinal,) = aplicar_regras(operacoes)

    assert sinal.cliente_id == "CLI-A-1"
    assert sinal.n_janelas_fracionamento == 1
    assert sinal.datas_fracionamento == ["2026-03-09"]
    # Com 3 operações a Regra 2 não se aplica: o alerta vem só do fracionamento.
    assert sinal.n_ops_atipicas == 0
    assert sinal.volume_brl == 54_200.0


@pytest.mark.parametrize(
    ("caso", "valores"),
    [
        # CLI-A-3 em 2026-03-05: nenhuma isolada atinge 20k, mas a soma fica abaixo do corte.
        ("soma_abaixo_do_corte", (17_200.0, 16_100.0, 15_200.0)),
        # "Ultrapassa R$ 50.000" foi lido como estrito: 50k cravado não caracteriza.
        ("soma_exatamente_no_limiar", (LIMIAR_SOMA_FRACIONAMENTO / 2, 15_000.0, 10_000.0)),
        # "Nenhuma isolada atinge R$ 20.000" foi lido como estrito: 20k cravado descaracteriza.
        ("isolada_exatamente_no_limiar", (LIMIAR_OPERACAO_ISOLADA, 19_000.0, 18_000.0)),
        # Menos de 3 operações no dia, ainda que a soma passe de 50k.
        ("apenas_duas_operacoes", (19_000.0, 18_000.0)),
    ],
)
def test_regra_1_nao_acende_nos_casos_de_fronteira(caso: str, valores: tuple[float, ...]):
    operacoes = tuple(
        op(f"OP-{i:04d}", "CLI-A-3", "2026-03-05", valor) for i, valor in enumerate(valores, start=1)
    )

    sinalizados = aplicar_regras(operacoes)

    # Sem nenhuma flag, o cliente não entra na lista de sinalizados.
    assert sinalizados == [], f"{caso} não deveria acionar a Regra 1"


def test_operacao_sem_data_sai_do_agrupamento_diario_mas_entra_no_volume():
    """Política de data nula do Nível 2: fora do calendário, dentro do volume.

    A operação sem data não pode criar nem destruir uma janela de 24 h, mas
    excluí-la do volume esconderia dinheiro que existe.
    """
    operacoes = (
        op("OP-0001", "CLI-A-1", "2026-03-09", 18_100.0),
        op("OP-0002", "CLI-A-1", "2026-03-09", 17_300.0),
        op("OP-0003", "CLI-A-1", "2026-03-09", 18_800.0),
        op("OP-0017", "CLI-A-1", None, 10_000.0),
    )

    (sinal,) = aplicar_regras(operacoes)

    # A janela continua sendo a dos três dias datados — a op sem data não a altera.
    assert sinal.n_janelas_fracionamento == 1
    assert sinal.datas_fracionamento == ["2026-03-09"]
    # E o volume soma as quatro operações, inclusive a sem data.
    assert sinal.volume_brl == 64_200.0
