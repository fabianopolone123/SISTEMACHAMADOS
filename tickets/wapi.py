import logging
import os

import requests

logger = logging.getLogger(__name__)

WAPI_TOKEN = os.getenv("WAPI_TOKEN", "o8bWQDnlomrsOaBF2CqnlHguBKIbX87By")
WAPI_INSTANCE = os.getenv("WAPI_INSTANCE", "LITE-F75JN4-FWW3NA")
WAPI_URL = f"https://api.w-api.app/v1/message/send-text?instanceId={WAPI_INSTANCE}"
SUCCESS_STATUSES = {"success", "sent", "ok", "queued"}


def _build_payload(phone: str, message: str) -> dict:
    return {
        "token": WAPI_TOKEN,
        "phone": phone,
        "message": message,
    }


def _normalize_response(result: dict) -> tuple[str | None, str | None]:
    return (
        result.get("status") or result.get("state"),
        result.get("messageId") or result.get("insertedId"),
    )


def send_whatsapp_message(phone: str, message: str, timeout: float = 10.0) -> dict:
    """
    Envia um texto para o número informado usando a API WAPI.

    Retorna o JSON retornado pela API e lança `requests.RequestException`
    caso o endpoint responda com erro HTTP.
    """
    if not phone:
        raise ValueError("Telefone alvo não pode estar vazio.")
    payload = _build_payload(phone, message)
    headers = {
        "Authorization": f"Bearer {WAPI_TOKEN}",
        "Content-Type": "application/json"
    }
    response = requests.post(WAPI_URL, headers=headers, json=payload, timeout=timeout)
    try:
        response.raise_for_status()
    except requests.RequestException:
        logger.exception("Erro ao enviar mensagem para %s via WAPI", phone)
        raise
    result = response.json()
    status, message_id = _normalize_response(result)
    if status not in SUCCESS_STATUSES and not message_id:
        logger.warning("Resposta inesperada do WAPI (%s): %s", phone, result)
    elif message_id and status not in SUCCESS_STATUSES:
        logger.info("WAPI retornou messageId %s para %s (status=%s)", message_id, phone, status)
    return result
