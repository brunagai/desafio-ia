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

# Critério de correspondência (o enunciado pede um, justificado, não “o” único certo):
# - As duas famílias de regra no mesmo cliente (janela de fracionamento E valor atípico)
#   deveriam sair como risco alto: é o recorte mais grave que as regras simples conseguem
#   marcar ao mesmo tempo.
# - Só uma família → esperamos médio: o cliente está no top 10, mas a regra isolada
#   (sobretudo 5× a mediana) gera falso positivo fácil em operação grande pontual.
# Concordância = o parecer do agente iguala esse risco esperado.
# Divergir não é automaticamente erro do modelo: regras propositalmente pobres.


def risco_esperado_pela_regra(
    n_janelas_fracionamento: int,
    n_ops_atipicas: int,
) -> NivelRisco:
    tem_fracionamento = n_janelas_fracionamento > 0
    tem_atipico = n_ops_atipicas > 0
    if tem_fracionamento and tem_atipico:
        return "alto"
    if tem_fracionamento or tem_atipico:
        return "médio"
    return "baixo"


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
    if linha["n_janelas_fracionamento"] > 0 and obtido == "baixo":
        return (
            f"{cid}: a regra marcou fracionamento e o agente baixou o risco. "
            "Aqui a regra tende a estar mais certa — smurfing é exatamente o que ela captura. "
            "O modelo pode ter lido só o recorte do dia como 'rotina' e subestimado o padrão."
        )
    if linha["n_ops_atipicas"] > 0 and esperado == "médio" and obtido == "alto":
        return (
            f"{cid}: só há flag de valor atípico (5× mediana), sem janela de fracionamento. "
            "O agente endureceu para alto. Pode ser razoável se o parecer cita volume e canais, "
            "mas a regra sozinha não justificava 'alto' no nosso critério — típico de FP da mediana."
        )
    if linha["n_ops_atipicas"] > 0 and esperado == "médio" and obtido == "baixo":
        return (
            f"{cid}: a regra 2 acusou outlier e o agente suavizou. "
            "Se o parecer descreve operação pontual sem estruturação, o agente está ponderando "
            "o falso positivo da mediana; se ignora o outlier, o modelo é que ficou frouxo."
        )
    if linha["fallback_parse"]:
        return (
            f"{cid}: JSON do agente falhou o contrato (fallback). A discordância não é julgamento "
            "de PLD — é falha de formato. A regra permanece a única âncora confiável neste caso."
        )
    return (
        f"{cid}: esperado {esperado}, agente {obtido}. "
        "Revisar o parecer: a regra é burra de propósito; o modelo só ganha se a justificativa "
        "usar evidência das ferramentas, não se só inverter o rótulo."
    )


def confrontar(lote: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    registros = lote if lote is not None else _carregar_lote()
    linhas: list[dict[str, Any]] = []
    for item in registros:
        parecer = item.get("parecer") or {}
        esperado = risco_esperado_pela_regra(
            int(item.get("n_janelas_fracionamento") or 0),
            int(item.get("n_ops_atipicas") or 0),
        )
        obtido = str(parecer.get("nivel_risco") or "médio")
        linha = {
            "cliente_id": item["cliente_id"],
            "n_sinalizacoes": item.get("n_sinalizacoes"),
            "n_janelas_fracionamento": item.get("n_janelas_fracionamento"),
            "n_ops_atipicas": item.get("n_ops_atipicas"),
            "volume_brl": item.get("volume_brl"),
            "risco_esperado": esperado,
            "risco_agente": obtido,
            "concordante": esperado == obtido,
            "fallback_parse": bool(item.get("fallback_parse")),
            "tipologia_suspeita": parecer.get("tipologia_suspeita"),
            "justificativa_agente": parecer.get("justificativa"),
        }
        linha["leitura_divergencia"] = _avaliar_divergencia(linha)
        linhas.append(linha)

    n = len(linhas)
    n_ok = sum(1 for linha in linhas if linha["concordante"])
    taxa = round(n_ok / n, 4) if n else 0.0
    divergencias = [linha for linha in linhas if not linha["concordante"]]

    relatorio: dict[str, Any] = {
        "criterio": (
            "alto se (fracionamento e atípico); médio se só uma das famílias; "
            "concordância = risco_agente == risco_esperado"
        ),
        "n_clientes": n,
        "n_concordantes": n_ok,
        "n_divergentes": len(divergencias),
        "taxa_concordancia": taxa,
        "linhas": linhas,
        "divergencias": divergencias,
        "sintese": (
            f"Concordância {n_ok}/{n} ({taxa:.0%}). "
            "Divergência é o ponto do exercício: a mediana e o corte de 50 mil são toscos; "
            "um agente que discorda com evidência das ferramentas pode estar certo."
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
        "risco_agente",
        "concordante",
        "fallback_parse",
        "leitura_divergencia",
    ]
    pd.DataFrame(relatorio["linhas"])[colunas].to_csv(
        CAMINHO_TABELA, index=False, encoding="utf-8"
    )


def main() -> dict[str, Any]:
    relatorio = confrontar()
    salvar(relatorio)
    print(relatorio["sintese"])
    print(f"Taxa de concordância: {relatorio['taxa_concordancia']:.0%}")
    print()
    df = pd.DataFrame(relatorio["linhas"])
    print(
        df[
            [
                "cliente_id",
                "n_janelas_fracionamento",
                "n_ops_atipicas",
                "risco_esperado",
                "risco_agente",
                "concordante",
            ]
        ].to_string(index=False)
    )
    print()
    print("Divergências:")
    if not relatorio["divergencias"]:
        print("  nenhuma")
    for item in relatorio["divergencias"]:
        print(f"- {item['leitura_divergencia']}")
    print()
    print(f"Salvo em {CAMINHO_RELATORIO} e {CAMINHO_TABELA}")
    return relatorio


if __name__ == "__main__":
    main()
