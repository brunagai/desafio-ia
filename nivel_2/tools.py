"""Ferramentas de consulta à base do nível 2 (`dados/dados_nivel_2.json`)."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, TypedDict


class Operacao(TypedDict):
    id: str
    cliente_id: str
    data: str | None
    valor: float
    moeda: str
    valor_brl: float
    canal: str
    tipo: str
    contraparte: str
    observacao: str


def _texto(valor: Any) -> str:
    if valor is None:
        return ""
    return str(valor).strip()


def _parse_data(valor: Any) -> str | None:
    if valor is None:
        return None
    texto = _texto(valor)
    if texto == "" or texto.lower() in {"nan", "nat", "none", "null"}:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_valor(valor: Any) -> float | None:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    if numero < 0:
        return None
    return numero


def _para_brl(valor: float, moeda: str, taxa_usd_brl: float) -> float | None:
    codigo = moeda.upper()
    if codigo == "BRL":
        return valor
    if codigo == "USD":
        return valor * taxa_usd_brl
    return None


def _resolver_caminho(caminho: Path | None) -> Path:
    if caminho is not None and caminho.is_file():
        return caminho
    nome = Path("dados") / "dados_nivel_2.json"
    candidatos = [
        Path.cwd() / nome,
        Path(__file__).resolve().parent.parent / nome,
        Path.cwd().parent / nome,
    ]
    for candidato in candidatos:
        if candidato.is_file():
            return candidato
    raise FileNotFoundError("dados_nivel_2.json não encontrado.")


def _normalizar_registro(bruto: Any, taxa: float) -> Operacao | None:
    if not isinstance(bruto, dict):
        return None
    try:
        op_id = _texto(bruto["id"])
        cliente_id = _texto(bruto["cliente_id"])
        moeda = _texto(bruto.get("moeda")).upper()
        valor = _parse_valor(bruto.get("valor"))
    except KeyError:
        return None
    if not op_id or not cliente_id or valor is None:
        return None
    valor_brl = _para_brl(valor, moeda, taxa)
    if valor_brl is None:
        return None
    return {
        "id": op_id,
        "cliente_id": cliente_id,
        "data": _parse_data(bruto.get("data")),
        "valor": valor,
        "moeda": moeda,
        "valor_brl": round(valor_brl, 2),
        "canal": _texto(bruto.get("canal")).lower(),
        "tipo": _texto(bruto.get("tipo")).lower(),
        "contraparte": _texto(bruto.get("contraparte")),
        "observacao": _texto(bruto.get("observacao")),
    }


@lru_cache(maxsize=1)
def carregar_base(caminho: str | None = None) -> tuple[float, tuple[Operacao, ...]]:
    """Lê o envelope, converte USD→BRL e remove duplicata de id (mantém a primeira)."""
    arquivo = _resolver_caminho(Path(caminho) if caminho else None)
    with arquivo.open(encoding="utf-8") as handle:
        bruto: Any = json.load(handle)
    if not isinstance(bruto, dict):
        raise TypeError("A raiz do JSON deve ser um objeto.")
    taxa = float(bruto["taxa_cambio_usd_brl"])
    if taxa <= 0:
        raise ValueError("taxa_cambio_usd_brl inválida.")
    vistos: set[str] = set()
    limpas: list[Operacao] = []
    for item in bruto.get("operacoes") or []:
        op = _normalizar_registro(item, taxa)
        if op is None or op["id"] in vistos:
            continue
        vistos.add(op["id"])
        limpas.append(op)
    return taxa, tuple(limpas)


def _por_cliente(cliente_id: str) -> list[Operacao]:
    chave = _texto(cliente_id)
    _, operacoes = carregar_base()
    return [op for op in operacoes if op["cliente_id"] == chave]


def historico_cliente(cliente_id: str) -> dict[str, Any]:
    ops = _por_cliente(cliente_id)
    if not ops:
        return {
            "cliente_id": _texto(cliente_id),
            "encontrado": False,
            "n_operacoes": 0,
            "volume_brl": 0.0,
            "menor_valor_brl": None,
            "maior_valor_brl": None,
            "tipos": {},
        }
    valores = [op["valor_brl"] for op in ops]
    tipos: dict[str, int] = defaultdict(int)
    for op in ops:
        tipos[op["tipo"] or "desconhecido"] += 1
    return {
        "cliente_id": ops[0]["cliente_id"],
        "encontrado": True,
        "n_operacoes": len(ops),
        "volume_brl": round(sum(valores), 2),
        "menor_valor_brl": min(valores),
        "maior_valor_brl": max(valores),
        "tipos": dict(tipos),
    }


def operacoes_do_dia(cliente_id: str, data: str) -> list[Operacao]:
    dia = _parse_data(data)
    if dia is None:
        return []
    return [op for op in _por_cliente(cliente_id) if op["data"] == dia]


def perfil_canal(cliente_id: str) -> dict[str, Any]:
    ops = _por_cliente(cliente_id)
    canais: dict[str, int] = defaultdict(int)
    for op in ops:
        canais[op["canal"] or "desconhecido"] += 1
    total = len(ops)
    percentual = {
        canal: round(100.0 * qtd / total, 2) if total else 0.0
        for canal, qtd in canais.items()
    }
    return {
        "cliente_id": _texto(cliente_id),
        "encontrado": total > 0,
        "n_operacoes": total,
        "contagem": dict(canais),
        "percentual": percentual,
    }
