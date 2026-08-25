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
    """Escolhe ferramentas pelo tipo de alerta — não dispara as três em todo mundo.

    `historico_cliente` é sempre a linha de base: sem o comportamento habitual do
    cliente não dá para dizer se o recorte é rotina ou desvio. As outras duas
    seguem condicionais — fracionamento pede o recorte do dia, valor atípico pede
    a distribuição por canal —, então cliente nenhum recebe as três por padrão.
    """
    chamadas: list[tuple[str, dict[str, str]]] = [
        ("historico_cliente", {"cliente_id": sinal.cliente_id})
    ]
    if sinal.n_janelas_fracionamento > 0:
        for dia in sinal.datas_fracionamento:
            chamadas.append(("operacoes_do_dia", {"cliente_id": sinal.cliente_id, "data": dia}))
    if sinal.n_ops_atipicas > 0:
        chamadas.append(("perfil_canal", {"cliente_id": sinal.cliente_id}))
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


CAMPOS_CONTRATO = ("nivel_risco", "tipologia_suspeita", "red_flags", "justificativa")


def _reparar(texto: str) -> tuple[AvaliacaoPLD | None, list[str]]:
    """Tenta salvar o que veio utilizável, sem nunca arbitrar o nível de risco."""
    try:
        dados = json.loads(_extrair_json(texto))
    except Exception:
        return None, list(CAMPOS_CONTRATO)
    if not isinstance(dados, dict):
        return None, list(CAMPOS_CONTRATO)

    ausentes = [campo for campo in CAMPOS_CONTRATO if not dados.get(campo)]
    remendo = dict(dados)
    if not remendo.get("tipologia_suspeita"):
        remendo["tipologia_suspeita"] = "não informada pelo modelo"
    if not isinstance(remendo.get("red_flags"), list):
        remendo["red_flags"] = []
    if not remendo.get("justificativa"):
        remendo["justificativa"] = (
            "Justificativa não devolvida pelo modelo. Campos ausentes na resposta: "
            + ", ".join(ausentes)
        )
    try:
        # nivel_risco inválido derruba a validação de propósito: risco não se remenda.
        return AvaliacaoPLD.model_validate(remendo), ausentes
    except Exception:
        return None, ausentes


def parse_avaliacao(texto: str) -> tuple[AvaliacaoPLD | None, str, list[str]]:
    """Devolve (parecer, status, campos_ausentes).

    status é `ok` quando a resposta cumpre o contrato, `reparado` quando o nível
    de risco veio válido e só campos de texto faltaram, e `sem_parecer` quando
    não há nada aproveitável — aí o cliente vai para revisão humana.
    """
    try:
        return AvaliacaoPLD.model_validate_json(_extrair_json(texto)), "ok", []
    except Exception:
        pass
    parcial, ausentes = _reparar(texto)
    if parcial is None:
        return None, "sem_parecer", ausentes
    return parcial, "reparado", ausentes


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


def _instrucao_reparo(ausentes: list[str]) -> str:
    faltando = ", ".join(ausentes) if ausentes else "todas as chaves do contrato"
    return (
        "\nSua resposta anterior não cumpriu o contrato: "
        f"{faltando} ausente(s) ou inválido(s). Responda de novo, JSON puro, com as quatro "
        "chaves obrigatórias. Não mude a análise, apenas complete o formato."
    )


def _somar_metricas(primeira: dict[str, Any], segunda: dict[str, Any]) -> dict[str, Any]:
    def soma(chave: str) -> Any:
        valores = [primeira.get(chave), segunda.get(chave)]
        numeros = [v for v in valores if isinstance(v, (int, float))]
        return round(sum(numeros), 3) if numeros else None

    return {
        "provedor": segunda.get("provedor") or primeira.get("provedor"),
        "modelo": segunda.get("modelo") or primeira.get("modelo"),
        "latencia_s": soma("latencia_s"),
        "tokens_prompt": soma("tokens_prompt"),
        "tokens_resposta": soma("tokens_resposta"),
        "tokens_total": soma("tokens_total"),
    }


def analisar_cliente(sinal: SinalizacaoCliente) -> dict[str, Any]:
    chamadas = decidir_ferramentas(sinal)
    evidencias = executar_ferramentas(chamadas)
    prompt = montar_prompt(sinal, evidencias)
    bruto, metricas = chamar_llm(prompt)
    avaliacao, status, ausentes = parse_avaliacao(bruto)
    tentativas = 1

    if status != "ok":
        # Uma segunda tentativa citando o campo que faltou: perder um parecer por
        # erro de formato é pior que gastar mais uma chamada.
        try:
            bruto2, metricas2 = chamar_llm(prompt + _instrucao_reparo(ausentes))
            tentativas = 2
            metricas = _somar_metricas(metricas, metricas2)
            avaliacao2, status2, ausentes2 = parse_avaliacao(bruto2)
            if status2 == "ok" or (avaliacao is None and avaliacao2 is not None):
                avaliacao, status, ausentes = avaliacao2, status2, ausentes2
        except Exception as erro:
            print(f"  {sinal.cliente_id}: retry de formato falhou ({type(erro).__name__}).")

    if avaliacao is None:
        return registro_de_falha(
            sinal,
            ValueError(f"resposta fora do contrato após {tentativas} tentativa(s)"),
            metricas.get("latencia_s") or 0.0,
            evidencias["ferramentas_usadas"],
            metricas,
        )

    return {
        "cliente_id": sinal.cliente_id,
        "n_sinalizacoes": sinal.n_sinalizacoes,
        "n_janelas_fracionamento": sinal.n_janelas_fracionamento,
        "n_ops_atipicas": sinal.n_ops_atipicas,
        "volume_brl": sinal.volume_brl,
        "ferramentas_usadas": evidencias["ferramentas_usadas"],
        "parecer": avaliacao.model_dump(),
        "fallback_parse": status != "ok",
        "status": status,
        "campos_reparados": ausentes if status == "reparado" else [],
        "tentativas": tentativas,
        "erro": None,
        "metricas": metricas,
    }


def registro_de_falha(
    sinal: SinalizacaoCliente,
    erro: BaseException,
    latencia_s: float,
    ferramentas_usadas: list[dict[str, Any]] | None = None,
    metricas: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Registro seguro para cliente que não pôde ser avaliado.

    `parecer` fica nulo em vez de receber um nível de risco: o contrato do
    enunciado (baixo/médio/alto) vale para pareceres de verdade, e cliente sem
    parecer vai para revisão humana em vez de entrar como avaliado no confronto.
    """
    if ferramentas_usadas is None:
        try:
            ferramentas = [{"nome": nome, **kwargs} for nome, kwargs in decidir_ferramentas(sinal)]
        except Exception:
            ferramentas = []
    else:
        ferramentas = ferramentas_usadas
    detalhe = f"{type(erro).__name__}: {erro}"[:300]
    return {
        "cliente_id": sinal.cliente_id,
        "n_sinalizacoes": sinal.n_sinalizacoes,
        "n_janelas_fracionamento": sinal.n_janelas_fracionamento,
        "n_ops_atipicas": sinal.n_ops_atipicas,
        "volume_brl": sinal.volume_brl,
        "ferramentas_usadas": ferramentas,
        "parecer": None,
        "fallback_parse": True,
        "status": "erro",
        "campos_reparados": [],
        "tentativas": 0 if metricas is None else 2,
        "erro": detalhe,
        "metricas": metricas
        or {
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
        parecer = item["parecer"] or {}
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
                "nivel_risco": parecer.get("nivel_risco", ""),
                "tipologia_suspeita": parecer.get("tipologia_suspeita", ""),
                "fallback_parse": item["fallback_parse"],
                "status": item.get("status", "ok"),
                "tentativas": item.get("tentativas", 1),
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
