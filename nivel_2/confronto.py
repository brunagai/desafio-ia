"""Confronto entre flags determinísticas e o risco atribuído pelo agente."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import pandas as pd

DIR_RAIZ = Path(__file__).resolve().parent.parent
DIR_OUTPUTS = DIR_RAIZ / "outputs"
CAMINHO_LOTE = DIR_OUTPUTS / "lote.json"
CAMINHO_RELATORIO = DIR_OUTPUTS / "confronto.json"
CAMINHO_TABELA = DIR_OUTPUTS / "confronto.csv"

NivelRisco = Literal["baixo", "médio", "alto"]

# Dois critérios de correspondência, reportados lado a lado.
#
# `enunciado` é o exemplo do próprio enunciado: sinalizado pelas duas regras → alto,
# por uma só → médio. Nesta base ele não serve como métrica: nenhum cliente do top 10
# dispara as duas famílias, então o risco esperado vira "médio" nas dez linhas e
# responder sempre "médio" cravaria 100%. A taxa mediria a frequência de uma palavra.
#
# `tipologia` é o critério principal e olha para o que cada regra captura. Janela de
# fracionamento é smurfing — estruturação deliberada para ficar abaixo do limite —,
# e isso é alto por natureza da tipologia, não por acúmulo de flags. Valor atípico
# isolado é médio: 5× a mediana gera falso positivo fácil em operação grande pontual.
#
# Concordância = o parecer do agente iguala o risco esperado. Divergir não é
# automaticamente erro do modelo: as regras são propositalmente pobres.

CriterioCorrespondencia = Literal["tipologia", "enunciado"]
CRITERIO_PRINCIPAL: CriterioCorrespondencia = "tipologia"

# Escala ordinal do risco. Só existe para medir a direção da divergência: em PLD
# não é indiferente errar para cima ou para baixo. Afrouxar é o erro caro — deixa
# passar. Endurecer custa fila de análise.
ORDEM_RISCO: dict[str, int] = {"baixo": 0, "médio": 1, "alto": 2}


def direcao_vs_regra(esperado: str, obtido: str) -> tuple[str, int | None]:
    """Posição do agente frente à regra: endureceu, afrouxou ou igual.

    Devolve também a distância em degraus da escala (positiva quando o agente
    sobe o risco), ou `None` se algum dos rótulos estiver fora da escala.
    """
    if esperado not in ORDEM_RISCO or obtido not in ORDEM_RISCO:
        return "indeterminada", None
    distancia = ORDEM_RISCO[obtido] - ORDEM_RISCO[esperado]
    if distancia > 0:
        return "endureceu", distancia
    if distancia < 0:
        return "afrouxou", distancia
    return "igual", 0


def risco_esperado_pela_regra(
    n_janelas_fracionamento: int,
    n_ops_atipicas: int,
    criterio: CriterioCorrespondencia = CRITERIO_PRINCIPAL,
) -> NivelRisco:
    tem_fracionamento = n_janelas_fracionamento > 0
    tem_atipico = n_ops_atipicas > 0
    if not tem_fracionamento and not tem_atipico:
        return "baixo"
    if criterio == "enunciado":
        return "alto" if (tem_fracionamento and tem_atipico) else "médio"
    return "alto" if tem_fracionamento else "médio"


def baseline_constante(esperados: list[str]) -> dict[str, Any]:
    """Melhor taxa possível para um agente que responde sempre o mesmo rótulo.

    Serve de piso de comparação: se a taxa do agente não supera este número, a
    métrica está medindo distribuição de rótulo, não capacidade de julgamento.
    """
    if not esperados:
        return {"rotulo": None, "taxa": 0.0}
    contagem = {rotulo: esperados.count(rotulo) for rotulo in set(esperados)}
    rotulo = max(contagem, key=lambda chave: contagem[chave])
    return {"rotulo": rotulo, "taxa": round(contagem[rotulo] / len(esperados), 4)}


def _plural(quantidade: int, singular: str, plural: str) -> str:
    return f"{quantidade} {singular if quantidade == 1 else plural}"


def _carregar_lote(caminho: Path = CAMINHO_LOTE) -> list[dict[str, Any]]:
    if not caminho.is_file():
        raise FileNotFoundError(f"Rode o agente antes: {caminho} não existe.")
    bruto = json.loads(caminho.read_text(encoding="utf-8"))
    if not isinstance(bruto, list) or not bruto:
        raise ValueError("lote.json vazio ou inválido.")
    return bruto


def _avaliar_divergencia(linha: dict[str, Any]) -> str:
    esperado: str = linha["risco_esperado"]
    obtido: str = linha["risco_agente"]
    if esperado == obtido:
        return "concordante"
    cid = linha["cliente_id"]
    direcao = linha["direcao_vs_regra"]
    if linha["n_janelas_fracionamento"] > 0 and esperado == "alto":
        grau = "zerou o alerta" if obtido == "baixo" else "rebaixou um degrau"
        return (
            f"{cid}: {direcao} — a regra marcou janela de fracionamento (esperado alto) "
            f"e o agente {grau}, para {obtido}. Smurfing é exatamente o que essa regra captura, "
            "então aqui o ônus da prova é do modelo: a queda só se sustenta se a justificativa "
            "mostrar, com histórico e operações do dia, que os valores logo abaixo do limite têm "
            "lastro de rotina. É o erro caro em PLD — afrouxar deixa passar."
        )
    if linha["n_ops_atipicas"] > 0 and esperado == "médio" and obtido == "alto":
        return (
            f"{cid}: {direcao} — só há flag de valor atípico (5× mediana), sem janela de "
            "fracionamento, e o agente subiu para alto. Pode ser razoável se o parecer cita "
            "volume e canais, mas a regra sozinha não justificava 'alto' no nosso critério — "
            "típico de FP da mediana. Custa fila de análise, não risco de deixar passar."
        )
    if linha["n_ops_atipicas"] > 0 and esperado == "médio" and obtido == "baixo":
        return (
            f"{cid}: {direcao} — a regra 2 acusou outlier e o agente desceu para baixo. "
            "Se o parecer descreve operação pontual sem estruturação, o agente está ponderando "
            "o falso positivo da mediana; se ignora o outlier, o modelo é que ficou frouxo."
        )
    if linha["fallback_parse"]:
        reparado = linha.get("status") == "reparado"
        origem = (
            "o parecer precisou de reparo de formato (campos de texto ausentes)"
            if reparado
            else "o JSON do agente falhou o contrato"
        )
        return (
            f"{cid}: {origem}. A discordância não é julgamento de PLD — é falha de formato, "
            "e a regra permanece a única âncora confiável neste caso."
        )
    return (
        f"{cid}: {direcao} — esperado {esperado}, agente {obtido}. "
        "Revisar o parecer: a regra é burra de propósito; o modelo só ganha se a justificativa "
        "usar evidência das ferramentas, não se só inverter o rótulo."
    )


def confrontar(lote: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    registros = lote if lote is not None else _carregar_lote()
    linhas: list[dict[str, Any]] = []
    nao_avaliados: list[dict[str, Any]] = []
    for item in registros:
        parecer = item.get("parecer")
        esperado = risco_esperado_pela_regra(
            int(item.get("n_janelas_fracionamento") or 0),
            int(item.get("n_ops_atipicas") or 0),
        )
        if not parecer or not parecer.get("nivel_risco"):
            # Sem parecer não há o que confrontar: entra como não avaliado, e não
            # como divergência ou acerto — senão a taxa mede falha de execução.
            nao_avaliados.append(
                {
                    "cliente_id": item["cliente_id"],
                    "risco_esperado": esperado,
                    "motivo": item.get("erro") or "cliente sem parecer estruturado",
                }
            )
            continue
        obtido = str(parecer.get("nivel_risco"))
        direcao, distancia = direcao_vs_regra(esperado, obtido)
        linha = {
            "cliente_id": item["cliente_id"],
            "n_sinalizacoes": item.get("n_sinalizacoes"),
            "n_janelas_fracionamento": item.get("n_janelas_fracionamento"),
            "n_ops_atipicas": item.get("n_ops_atipicas"),
            "volume_brl": item.get("volume_brl"),
            "risco_esperado": esperado,
            "risco_esperado_criterio_enunciado": risco_esperado_pela_regra(
                int(item.get("n_janelas_fracionamento") or 0),
                int(item.get("n_ops_atipicas") or 0),
                criterio="enunciado",
            ),
            "risco_agente": obtido,
            "concordante": esperado == obtido,
            "direcao_vs_regra": direcao,
            "distancia_niveis": distancia,
            "fallback_parse": bool(item.get("fallback_parse")),
            "status": item.get("status", "ok"),
            "tipologia_suspeita": parecer.get("tipologia_suspeita"),
            "justificativa_agente": parecer.get("justificativa"),
        }
        linha["leitura_divergencia"] = _avaliar_divergencia(linha)
        linhas.append(linha)

    n = len(linhas)
    n_ok = sum(1 for linha in linhas if linha["concordante"])
    taxa = round(n_ok / n, 4) if n else 0.0
    divergencias = [linha for linha in linhas if not linha["concordante"]]

    n_ok_enunciado = sum(
        1
        for linha in linhas
        if linha["risco_esperado_criterio_enunciado"] == linha["risco_agente"]
    )
    piso = baseline_constante([linha["risco_esperado"] for linha in linhas])
    piso_enunciado = baseline_constante(
        [linha["risco_esperado_criterio_enunciado"] for linha in linhas]
    )

    # O piso também vai linha a linha: assim a comparação sobrevive no CSV, e a
    # taxa do baseline é recalculável a partir da própria tabela.
    for linha in linhas:
        linha["baseline_rotulo"] = piso["rotulo"]
        linha["baseline_acerta"] = linha["risco_esperado"] == piso["rotulo"]

    direcoes = {
        chave: sum(1 for linha in linhas if linha["direcao_vs_regra"] == chave)
        for chave in ("igual", "endureceu", "afrouxou", "indeterminada")
    }

    relatorio: dict[str, Any] = {
        "criterio": (
            "principal (tipologia): alto se há janela de fracionamento (smurfing), "
            "médio se só há valor atípico, baixo se nenhuma regra marcou; "
            "concordância = risco_agente == risco_esperado"
        ),
        "criterio_alternativo": (
            "enunciado: alto só se as duas famílias marcam o mesmo cliente, médio se uma só"
        ),
        "n_clientes": n + len(nao_avaliados),
        "n_avaliados": n,
        "n_nao_avaliados": len(nao_avaliados),
        "n_concordantes": n_ok,
        "n_divergentes": len(divergencias),
        "taxa_concordancia": taxa,
        "direcao_das_divergencias": {
            "n_endureceu": direcoes["endureceu"],
            "n_afrouxou": direcoes["afrouxou"],
            "n_igual": direcoes["igual"],
            "n_indeterminada": direcoes["indeterminada"],
            "leitura": (
                "Endurecer custa fila de análise; afrouxar deixa passar. Em PLD os dois "
                "erros não têm o mesmo preço, então a contagem por direção diz mais do que "
                "a taxa de concordância."
            ),
        },
        "dicionario_de_campos": {
            "risco_esperado": "risco derivado das regras determinísticas pelo critério principal (tipologia)",
            "risco_esperado_criterio_enunciado": "mesmo cálculo pelo critério do enunciado, mantido como análise de sensibilidade",
            "direcao_vs_regra": (
                "posição do parecer do agente frente à regra: 'endureceu' quando ele atribui "
                "risco acima do esperado, 'afrouxou' quando atribui abaixo, 'igual' quando bate"
            ),
            "distancia_niveis": (
                "distância em degraus na escala baixo(0) < médio(1) < alto(2); positiva no "
                "endurecimento, negativa no afrouxamento, zero na concordância"
            ),
            "leitura_divergencia": (
                "leitura em texto do caso, dizendo de quem é o ônus da prova; "
                "'concordante' quando não há divergência"
            ),
            "baseline_rotulo": "rótulo que um agente constante usaria (a classe majoritária entre os esperados)",
            "baseline_acerta": "se esse agente constante acertaria esta linha; a média da coluna reproduz a taxa do baseline",
        },
        "sensibilidade_ao_criterio": {
            "tipologia": {
                "taxa": taxa,
                "baseline_constante": piso,
                "supera_baseline": taxa > piso["taxa"],
            },
            "enunciado": {
                "taxa": round(n_ok_enunciado / n, 4) if n else 0.0,
                "baseline_constante": piso_enunciado,
                "supera_baseline": (n_ok_enunciado / n if n else 0.0) > piso_enunciado["taxa"],
            },
        },
        "linhas": linhas,
        "divergencias": divergencias,
        "nao_avaliados": nao_avaliados,
        "sintese": (
            f"Concordância {n_ok}/{n} ({taxa:.0%}) pelo critério de tipologia"
            f"{f'; {len(nao_avaliados)} cliente(s) sem parecer ficaram fora da conta' if nao_avaliados else ''}. "
            f"Um agente que respondesse sempre '{piso['rotulo']}' faria {piso['taxa']:.0%}, "
            "então a taxa sozinha não prova julgamento — a leitura das divergências é que vale. "
            f"Divergências por direção: {_plural(direcoes['afrouxou'], 'afrouxamento', 'afrouxamentos')} "
            f"e {_plural(direcoes['endureceu'], 'endurecimento', 'endurecimentos')} frente à regra. "
            "As regras são propositalmente pobres: um agente que discorda com evidência das "
            "ferramentas pode estar certo."
        ),
    }
    return relatorio


def salvar(relatorio: dict[str, Any]) -> None:
    DIR_OUTPUTS.mkdir(parents=True, exist_ok=True)
    CAMINHO_RELATORIO.write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    colunas = [
        "cliente_id",
        "n_sinalizacoes",
        "n_janelas_fracionamento",
        "n_ops_atipicas",
        "risco_esperado",
        "risco_esperado_criterio_enunciado",
        "risco_agente",
        "concordante",
        "direcao_vs_regra",
        "distancia_niveis",
        "baseline_rotulo",
        "baseline_acerta",
        "fallback_parse",
        "status",
        "leitura_divergencia",
    ]
    pd.DataFrame(relatorio["linhas"])[colunas].to_csv(
        CAMINHO_TABELA, index=False, encoding="utf-8"
    )


def main() -> dict[str, Any]:
    relatorio = confrontar()
    salvar(relatorio)
    print(relatorio["sintese"])
    print()
    print("Sensibilidade ao critério de correspondência:")
    for nome, dados in relatorio["sensibilidade_ao_criterio"].items():
        piso = dados["baseline_constante"]
        veredito = "supera o piso" if dados["supera_baseline"] else "NÃO supera o piso"
        print(
            f"  {nome}: taxa {dados['taxa']:.0%} | responder sempre "
            f"'{piso['rotulo']}' daria {piso['taxa']:.0%} → {veredito}"
        )
    print()
    direcao = relatorio["direcao_das_divergencias"]
    print(
        "Direção frente à regra: "
        f"{_plural(direcao['n_afrouxou'], 'afrouxamento', 'afrouxamentos')}, "
        f"{_plural(direcao['n_endureceu'], 'endurecimento', 'endurecimentos')}, "
        f"{_plural(direcao['n_igual'], 'linha igual', 'linhas iguais')}. "
        "Afrouxar deixa passar; endurecer custa fila de análise."
    )
    print()
    df = pd.DataFrame(relatorio["linhas"])
    print(
        df[
            [
                "cliente_id",
                "n_janelas_fracionamento",
                "n_ops_atipicas",
                "risco_esperado",
                "risco_esperado_criterio_enunciado",
                "risco_agente",
                "concordante",
                "direcao_vs_regra",
                "baseline_acerta",
            ]
        ].to_string(index=False)
    )
    print()
    print("Divergências:")
    if not relatorio["divergencias"]:
        print("  nenhuma")
    for item in relatorio["divergencias"]:
        print(f"- {item['leitura_divergencia']}")
    if relatorio["nao_avaliados"]:
        print()
        print("Sem parecer (fora da taxa, para revisão humana):")
        for item in relatorio["nao_avaliados"]:
            print(f"- {item['cliente_id']}: {item['motivo']}")
    print()
    print(f"Salvo em {CAMINHO_RELATORIO} e {CAMINHO_TABELA}")
    return relatorio


if __name__ == "__main__":
    main()
