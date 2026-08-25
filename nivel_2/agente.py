"""Agente de triagem PLD: regras na base do nível 2, escolha de ferramentas e lote."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any, Callable, Literal

import pandas as pd
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

_DIR_MODULO = Path(__file__).resolve().parent
if str(_DIR_MODULO) not in sys.path:
    sys.path.insert(0, str(_DIR_MODULO))

from tools import Operacao, carregar_base, historico_cliente, operacoes_do_dia, perfil_canal

LIMIAR_SOMA_FRACIONAMENTO = 50_000.0
LIMIAR_OPERACAO_ISOLADA = 20_000.0
FATOR_MEDIANA_ATIPICO = 5
MIN_OPS_ATIPICO = 4
TOP_N = 10

NivelRisco = Literal["baixo", "médio", "alto"]
# Fora do contrato da LLM de propósito: só o pipeline emite este rótulo, quando não há parecer.
RISCO_INDETERMINADO = "indeterminado"
DIR_RAIZ = Path(__file__).resolve().parent.parent
DIR_OUTPUTS = DIR_RAIZ / "outputs"


class AvaliacaoPLD(BaseModel):
    nivel_risco: NivelRisco
    tipologia_suspeita: str = Field(min_length=1)
    red_flags: list[str]
    justificativa: str = Field(min_length=1)

    @field_validator("nivel_risco", mode="before")
    @classmethod
    def _normalizar_risco(cls, valor: Any) -> str:
        texto = str(valor).strip().lower()
        mapa = {
            "baixo": "baixo",
            "baixa": "baixo",
            "low": "baixo",
            "medio": "médio",
            "médio": "médio",
            "medium": "médio",
            "alto": "alto",
            "alta": "alto",
            "high": "alto",
        }
        if texto not in mapa:
            raise ValueError(f"nivel_risco inválido: {valor}")
        return mapa[texto]


@dataclass
class SinalizacaoCliente:
    cliente_id: str
    n_sinalizacoes: int
    n_janelas_fracionamento: int
    n_ops_atipicas: int
    volume_brl: float
    datas_fracionamento: list[str] = field(default_factory=list)
    ids_atipicos: list[str] = field(default_factory=list)


def aplicar_regras(operacoes: tuple[Operacao, ...]) -> list[SinalizacaoCliente]:
    por_cliente: dict[str, list[Operacao]] = defaultdict(list)
    for op in operacoes:
        por_cliente[op["cliente_id"]].append(op)

    resultado: list[SinalizacaoCliente] = []
    for cliente_id, ops in por_cliente.items():
        volume = round(sum(op["valor_brl"] for op in ops), 2)
        por_dia: dict[str, list[Operacao]] = defaultdict(list)
        for op in ops:
            if op["data"]:
                por_dia[op["data"]].append(op)

        datas_frac: list[str] = []
        for dia, grupo in por_dia.items():
            valores = [item["valor_brl"] for item in grupo]
            if (
                len(grupo) >= 3
                and sum(valores) > LIMIAR_SOMA_FRACIONAMENTO
                and max(valores) < LIMIAR_OPERACAO_ISOLADA
            ):
                datas_frac.append(dia)

        ids_atipicos: list[str] = []
        if len(ops) >= MIN_OPS_ATIPICO:
            med = median(op["valor_brl"] for op in ops)
            corte = FATOR_MEDIANA_ATIPICO * med
            for op in ops:
                if op["valor_brl"] > corte:
                    ids_atipicos.append(op["id"])

        n_janelas = len(datas_frac)
        n_atipico = len(ids_atipicos)
        n_sinais = n_janelas + n_atipico
        if n_sinais == 0:
            continue
        resultado.append(
            SinalizacaoCliente(
                cliente_id=cliente_id,
                n_sinalizacoes=n_sinais,
                n_janelas_fracionamento=n_janelas,
                n_ops_atipicas=n_atipico,
                volume_brl=volume,
                datas_fracionamento=sorted(datas_frac),
                ids_atipicos=ids_atipicos,
            )
        )
    resultado.sort(key=lambda item: (-item.n_sinalizacoes, -item.volume_brl, item.cliente_id))
    return resultado


def top_clientes_sinalizados(n: int = TOP_N) -> list[SinalizacaoCliente]:
    _, operacoes = carregar_base()
    return aplicar_regras(operacoes)[:n]


def decidir_ferramentas(sinal: SinalizacaoCliente) -> list[tuple[str, dict[str, str]]]:
    """Escolhe ferramentas pelo tipo de alerta — não dispara as três em todo mundo."""
    chamadas: list[tuple[str, dict[str, str]]] = []
    if sinal.n_janelas_fracionamento > 0:
        for dia in sinal.datas_fracionamento:
            chamadas.append(("operacoes_do_dia", {"cliente_id": sinal.cliente_id, "data": dia}))
    if sinal.n_ops_atipicas > 0:
        chamadas.append(("perfil_canal", {"cliente_id": sinal.cliente_id}))
    if sinal.n_janelas_fracionamento == 0 or sinal.n_ops_atipicas > 0:
        chamadas.insert(0, ("historico_cliente", {"cliente_id": sinal.cliente_id}))
    if not chamadas:
        chamadas.append(("historico_cliente", {"cliente_id": sinal.cliente_id}))
    return chamadas


_FERRAMENTAS: dict[str, Callable[..., Any]] = {
    "historico_cliente": historico_cliente,
    "operacoes_do_dia": operacoes_do_dia,
    "perfil_canal": perfil_canal,
}


def executar_ferramentas(
    chamadas: list[tuple[str, dict[str, str]]],
) -> dict[str, Any]:
    evidencias: dict[str, Any] = {"ferramentas_usadas": [], "resultados": []}
    vistos: set[tuple[str, str]] = set()
    for nome, kwargs in chamadas:
        chave = (nome, json.dumps(kwargs, sort_keys=True))
        if chave in vistos:
            continue
        vistos.add(chave)
        funcao = _FERRAMENTAS[nome]
        evidencias["ferramentas_usadas"].append({"nome": nome, **kwargs})
        evidencias["resultados"].append({"nome": nome, "kwargs": kwargs, "retorno": funcao(**kwargs)})
    return evidencias


def _extrair_json(texto: str) -> str:
    limpo = texto.strip()
    if limpo.startswith("```"):
        limpo = re.sub(r"^```(?:json)?\s*", "", limpo)
        limpo = re.sub(r"\s*```$", "", limpo)
    inicio, fim = limpo.find("{"), limpo.rfind("}")
    if inicio >= 0 and fim > inicio:
        return limpo[inicio : fim + 1]
    return limpo


def parse_avaliacao(texto: str) -> tuple[AvaliacaoPLD, bool]:
    try:
        return AvaliacaoPLD.model_validate_json(_extrair_json(texto)), False
    except Exception as erro:
        return (
            AvaliacaoPLD(
                nivel_risco="médio",
                tipologia_suspeita="indeterminada",
                red_flags=["resposta da LLM malformada ou indisponível"],
                justificativa=f"Fallback ({type(erro).__name__}): {texto[:400]}",
            ),
            True,
        )


def _carregar_env() -> None:
    for candidato in (DIR_RAIZ / ".env", Path.cwd() / ".env"):
        load_dotenv(candidato)


def chamar_llm(prompt: str) -> tuple[str, dict[str, Any]]:
    _carregar_env()
    google_key = (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()
    groq_key = (os.getenv("GROQ_API_KEY") or "").strip()
    inicio = time.perf_counter()

    if google_key:
        try:
            from google import genai

            modelo = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
            resposta = genai.Client(api_key=google_key).models.generate_content(
                model=modelo,
                contents=prompt,
                config={"response_mime_type": "application/json", "temperature": 0.2},
            )
            uso = getattr(resposta, "usage_metadata", None)
            return (resposta.text or ""), {
                "provedor": "gemini",
                "modelo": modelo,
                "latencia_s": round(time.perf_counter() - inicio, 3),
                "tokens_prompt": getattr(uso, "prompt_token_count", None),
                "tokens_resposta": getattr(uso, "candidates_token_count", None),
                "tokens_total": getattr(uso, "total_token_count", None),
            }
        except Exception as erro:
            print(f"Gemini indisponível ({type(erro).__name__}); tentando Groq.")

    if groq_key:
        try:
            from groq import Groq
        except ImportError as erro:
            raise RuntimeError(
                "Pacote groq ausente; instale com pip install -r requirements.txt."
            ) from erro

        modelos = [
            os.getenv("GROQ_MODEL") or "openai/gpt-oss-20b",
            "openai/gpt-oss-20b",
        ]
        try:
            cliente = Groq(api_key=groq_key)
        except Exception as erro:
            raise RuntimeError(
                f"Não foi possível criar o cliente Groq ({type(erro).__name__})."
            ) from erro
        ultimo: Exception | None = None
        for modelo in dict.fromkeys(modelos):
            try:
                resposta = cliente.chat.completions.create(
                    model=modelo,
                    temperature=0.2,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": "Responda apenas JSON válido. Não calcule limites."},
                        {"role": "user", "content": prompt},
                    ],
                )
                uso = resposta.usage
                return resposta.choices[0].message.content or "", {
                    "provedor": "groq",
                    "modelo": modelo,
                    "latencia_s": round(time.perf_counter() - inicio, 3),
                    "tokens_prompt": getattr(uso, "prompt_tokens", None),
                    "tokens_resposta": getattr(uso, "completion_tokens", None),
                    "tokens_total": getattr(uso, "total_tokens", None),
                }
            except Exception as erro:
                ultimo = erro
                print(f"Groq {modelo} falhou ({type(erro).__name__}).")
        raise RuntimeError(f"Groq indisponível: {type(ultimo).__name__}")

    return (
        '{"nivel_risco":"médio","tipologia_suspeita":"indeterminada",'
        '"red_flags":["chave de API ausente"],'
        '"justificativa":"Sem GOOGLE_API_KEY ou GROQ_API_KEY; chamada não disparada."}',
        {
            "provedor": None,
            "modelo": None,
            "latencia_s": round(time.perf_counter() - inicio, 3),
            "tokens_prompt": 0,
            "tokens_resposta": 0,
            "tokens_total": 0,
        },
    )


def montar_prompt(sinal: SinalizacaoCliente, evidencias: dict[str, Any]) -> str:
    return (
        "Você é analista de PLD/FT. Escreva um parecer só com o que está nas evidências abaixo. "
        "Não some valores, não compare com limites e não invente operação.\n"
        "As flags determinísticas já foram calculadas fora do modelo "
        f"(janelas de fracionamento={sinal.datas_fracionamento}, "
        f"ops atípicas={sinal.ids_atipicos}). Use isso só como contexto qualitativo.\n"
        "JSON puro com nivel_risco (baixo|médio|alto), tipologia_suspeita, red_flags, justificativa.\n"
        f"cliente_id={sinal.cliente_id}\n"
        f"evidencias={json.dumps(evidencias, ensure_ascii=False, default=str)}"
    )


def analisar_cliente(sinal: SinalizacaoCliente) -> dict[str, Any]:
    chamadas = decidir_ferramentas(sinal)
    evidencias = executar_ferramentas(chamadas)
    bruto, metricas = chamar_llm(montar_prompt(sinal, evidencias))
    avaliacao, fallback = parse_avaliacao(bruto)
    return {
        "cliente_id": sinal.cliente_id,
        "n_sinalizacoes": sinal.n_sinalizacoes,
        "n_janelas_fracionamento": sinal.n_janelas_fracionamento,
        "n_ops_atipicas": sinal.n_ops_atipicas,
        "volume_brl": sinal.volume_brl,
        "ferramentas_usadas": evidencias["ferramentas_usadas"],
        "parecer": avaliacao.model_dump(),
        "fallback_parse": fallback,
        "status": "ok",
        "erro": None,
        "metricas": metricas,
    }


def registro_de_falha(
    sinal: SinalizacaoCliente,
    erro: BaseException,
    latencia_s: float,
) -> dict[str, Any]:
    """Registro seguro para cliente que não pôde ser avaliado.

    Não atribui nível de risco: um cliente sem parecer entra na fila de revisão
    humana, e não como `médio` — que passaria por avaliação concluída no confronto.
    """
    try:
        ferramentas = [{"nome": nome, **kwargs} for nome, kwargs in decidir_ferramentas(sinal)]
    except Exception:
        ferramentas = []
    detalhe = f"{type(erro).__name__}: {erro}"[:300]
    return {
        "cliente_id": sinal.cliente_id,
        "n_sinalizacoes": sinal.n_sinalizacoes,
        "n_janelas_fracionamento": sinal.n_janelas_fracionamento,
        "n_ops_atipicas": sinal.n_ops_atipicas,
        "volume_brl": sinal.volume_brl,
        "ferramentas_usadas": ferramentas,
        "parecer": {
            "nivel_risco": RISCO_INDETERMINADO,
            "tipologia_suspeita": "indeterminada",
            "red_flags": [f"análise não concluída ({type(erro).__name__})"],
            "justificativa": (
                f"Cliente não avaliado pelo agente — {detalhe}. "
                "As flags determinísticas permanecem válidas; requer reprocessamento "
                "ou revisão humana."
            ),
        },
        "fallback_parse": True,
        "status": "erro",
        "erro": detalhe,
        "metricas": {
            "provedor": None,
            "modelo": None,
            "latencia_s": latencia_s,
            "tokens_prompt": 0,
            "tokens_resposta": 0,
            "tokens_total": 0,
        },
    }


def executar_lote(n: int = TOP_N) -> pd.DataFrame:
    DIR_OUTPUTS.mkdir(parents=True, exist_ok=True)
    top = top_clientes_sinalizados(n)
    caminho_json = DIR_OUTPUTS / "lote.json"

    registros: list[dict[str, Any]] = []
    for sinal in top:
        inicio = time.perf_counter()
        try:
            registros.append(analisar_cliente(sinal))
        except Exception as erro:
            registro = registro_de_falha(sinal, erro, round(time.perf_counter() - inicio, 3))
            registros.append(registro)
            print(f"[erro] {sinal.cliente_id}: {registro['erro']} — seguindo para o próximo.")
        # Grava a cada cliente: uma falha no fim do lote não descarta o que já rodou.
        caminho_json.write_text(
            json.dumps(registros, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    linhas = []
    for item in registros:
        parecer = item["parecer"]
        metricas = item["metricas"]
        nomes = sorted({f["nome"] for f in item["ferramentas_usadas"]})
        linhas.append(
            {
                "cliente_id": item["cliente_id"],
                "n_sinalizacoes": item["n_sinalizacoes"],
                "n_janelas_fracionamento": item["n_janelas_fracionamento"],
                "n_ops_atipicas": item["n_ops_atipicas"],
                "volume_brl": item["volume_brl"],
                "ferramentas": ",".join(nomes),
                "nivel_risco": parecer["nivel_risco"],
                "tipologia_suspeita": parecer["tipologia_suspeita"],
                "fallback_parse": item["fallback_parse"],
                "status": item.get("status", "ok"),
                "provedor": metricas.get("provedor"),
                "modelo": metricas.get("modelo"),
                "latencia_s": metricas.get("latencia_s"),
                "tokens_total": metricas.get("tokens_total") or 0,
            }
        )
    df = pd.DataFrame(linhas)
    df.to_csv(DIR_OUTPUTS / "lote.csv", index=False, encoding="utf-8")

    print("Top clientes sinalizados e pareceres:")
    print(df.to_string(index=False))
    print()
    falhas = [item for item in registros if item.get("status") == "erro"]
    avaliados = df.loc[df["status"] == "ok"]

    print("Totais de lote:")
    print(f"  clientes: {len(df)} (avaliados: {len(avaliados)}, com erro: {len(falhas)})")
    print(f"  latência soma (s): {df['latencia_s'].sum():.3f}")
    print(f"  latência média (s): {df['latencia_s'].mean():.3f}")
    print(f"  tokens soma: {int(df['tokens_total'].sum())}")
    print(f"  tokens média: {df['tokens_total'].mean():.1f}")
    print(f"  arquivos: {caminho_json} e {DIR_OUTPUTS / 'lote.csv'}")
    if falhas:
        print()
        print("Clientes sem parecer (revisão humana):")
        for item in falhas:
            print(f"  {item['cliente_id']}: {item['erro']}")
    return df


def executar() -> pd.DataFrame:
    return executar_lote(TOP_N)


if __name__ == "__main__":
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    executar()
