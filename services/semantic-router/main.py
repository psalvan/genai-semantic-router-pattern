"""FastAPI semantic router: embeds user text and matches the closest intent anchor set.

Uses sentence-transformers cosine similarity against per-intent example phrases (Portuguese
samples are intentional for the banking demo domain).

Version: 1.0.0
Author: Pablo Salvanha
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer, util

app = FastAPI(title="Semantic Router")
# auto_error=False: if SMART_ROUTER_KEY is unset, /check-intent accepts requests without Bearer (local dev only).
security = HTTPBearer(auto_error=False)

TOPIC_SCORE_THRESHOLD = 0.60

print("Loading model...")
_model = SentenceTransformer("all-MiniLM-L6-v2")

# Example utterances per intent label (anchor phrases for similarity).
INTENT_ANCHOR_PHRASES: dict[str, list[str]] = {
    "Pix_Transfer_Balance": [
        "Quero fazer um PIX de 50 reais para o João.",
        "Manda um pix de 100 conto pra minha mãe.",
        "Transfere 200 pro meu irmão, por favor.",
        "Paga essa chave pix aqui: 12345678900.",
        "Quero enviar mil reais pelo PIX agora.",
        "Manda cinquentinha pro Carlos.",
        "Preciso fazer um pagamento via PIX no valor de 300 reais.",
        "Faz uma transferência de R$ 500 para a conta da Maria.",
        "Qual é o meu saldo atual?",
        "Quanto eu tenho na conta corrente hoje?",
        "Queria ver meu extrato dos últimos 3 dias.",
        "Tem como checar meu saldo?",
        "Consegue puxar meu saldo pra mim?",
    ],
    "FAQ_Policy": [
        "Como funciona o seguro viagem do meu cartão black?",
        "Como faço para pedir um aumento de limite?",
        "Tem cobrança de anuidade nesse cartão de crédito?",
        "Quais são as regras para acessar a sala VIP no aeroporto de Guarulhos?",
        "O cartão tem cobertura para aluguel de carros no exterior?",
        "Qual é a taxa de juros se eu entrar no rotativo?",
        "Quanto tempo demora para estornar uma compra cancelada?",
        "Posso parcelar a fatura em quantas vezes sem juros?",
        "Qual a tarifa para fazer saque internacional na Europa?",
        "O que acontece se eu atrasar o pagamento da fatura em 2 dias?",
        "Como funciona o acúmulo de pontos e milhas no meu plano?",
        "Como funciona o esquema de cashback dessa conta?",
        "Existe alguma taxa de manutenção da conta corrente?",
    ],
    "Fraud_Alert_Loss": [
        "Roubaram meu celular, bloqueia minha conta urgente!",
        "Perdi meu cartão de crédito, o que eu faço?",
        "Cancelar meu cartão agora, fui assaltado.",
        "Perdi minha carteira com todos os cartões do banco.",
        "Tem uma compra de 500 reais que eu não reconheço na minha fatura.",
        "Acho que meu cartão foi clonado na internet.",
        "Recebi uma notificação de compra aprovada que eu não fiz.",
        "Desconheço esse débito estranho na minha conta de hoje cedo.",
        "Como faço para contestar uma compra suspeita no crédito?",
        "Bloqueia tudo, acho que descobriram minha senha!",
        "Fizeram um PIX da minha conta que eu não autorizei.",
        "Sofri uma fraude na conta, me ajuda rápido pelo amor de Deus!",
    ],
    "ChitChat": [
        "oi",
        "olá, bom dia!",
        "boa noite, tudo bem?",
        "quem é você?",
        "o que você sabe fazer?",
        "valeu, muito obrigado!",
        "beleza, tchau!",
    ],
}

print("Encoding topic anchors into memory...")
_intent_anchor_embeddings: dict[str, object] = {
    name: _model.encode(phrases) for name, phrases in INTENT_ANCHOR_PHRASES.items()
}


def verify_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[str]:
    """Validate Bearer token when SMART_ROUTER_KEY is set; otherwise allow anonymous access."""
    expected = (os.environ.get("SMART_ROUTER_KEY") or "").strip()
    if not expected:
        return None
    if not credentials or credentials.credentials != expected:
        raise HTTPException(status_code=401, detail="Access denied")
    return credentials.credentials


class IntentCheckRequest(BaseModel):
    """Request body for POST /check-intent."""

    text: str


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe for load balancers and orchestrators."""
    return {"status": "ok"}


@app.post("/check-intent")
async def check_intent(
    data: IntentCheckRequest,
    _verified: Optional[str] = Depends(verify_token),
) -> dict[str, object]:
    """Return the best-matching intent label and cosine-similarity confidence vs anchor phrases."""
    user_embedding = _model.encode(data.text)

    best_intent = "Unknown"
    best_score = 0.0
    for intent_name, anchor_embeddings in _intent_anchor_embeddings.items():
        score = util.cos_sim(user_embedding, anchor_embeddings).max().item()
        if score > best_score:
            best_score = score
            best_intent = intent_name if score >= TOPIC_SCORE_THRESHOLD else "Unknown"

    return {
        "intent_suggested": best_intent,
        "confidence": round(best_score, 4),
    }
