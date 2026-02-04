import json
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

WAPI_TOKEN = os.getenv("WAPI_TOKEN", "o8bWQDnlomrsOaBF2CqnlHguBKIbX87By")
WAPI_INSTANCE = os.getenv("WAPI_INSTANCE", "LITE-F75JN4-FWW3NA")
WAPI_BASE = os.getenv("WAPI_BASE_URL", "https://api.w-api.app/v1")
WAPI_SEND_URL = f"{WAPI_BASE}/message/send-text?instanceId={WAPI_INSTANCE}"
WAPI_GROUPS_URL = os.getenv("WAPI_GROUPS_URL", f"{WAPI_BASE}/groups?instanceId={WAPI_INSTANCE}")
WAPI_LEGACY_GROUPS_URL = f"{WAPI_BASE}/whatsapp/group/list?instanceId={WAPI_INSTANCE}"
WAPI_API_GROUPS_URL = os.getenv("WAPI_API_GROUPS_URL", f"{WAPI_BASE}/api/{WAPI_INSTANCE}/groups")
WAPI_GET_ALL_GROUPS_URL = os.getenv("WAPI_GET_ALL_GROUPS_URL", f"{WAPI_BASE}/group/get-all-groups?instanceId={WAPI_INSTANCE}")
SUCCESS_STATUSES = {"success", "sent", "ok", "queued"}

_group_cache: dict[str, float] = {}


def _normalize_destination(destination: str) -> tuple[str, str]:
    normalized = (destination or "").strip()
    if not normalized:
        raise ValueError("Destino da mensagem não pode ficar vazio.")
    if normalized.lower().endswith("@g.us"):
        return normalized, "group"
    if normalized.lower().endswith("@c.us"):
        return normalized, "contact"
    digits = "".join(ch for ch in normalized if ch.isdigit())
    if not digits:
        raise ValueError("Destino inválido. Informe um número ou JID válido.")
    return digits, "contact"


def _build_payload(destination: str, message: str) -> dict:
    return {
        "token": WAPI_TOKEN,
        "phone": destination,
        "message": message,
    }


def _normalize_response(result: dict) -> tuple[str | None, str | None]:
    return (
        result.get("status") or result.get("state"),
        result.get("messageId") or result.get("insertedId"),
    )


def _fetch_groups_from(url: str, headers: dict, timeout: float) -> requests.Response:
    return requests.get(url, headers=headers, timeout=timeout)


def _normalize_groups_payload(payload: dict | list) -> list[dict]:
    result = payload
    if isinstance(payload, dict):
        result = payload.get("result") or payload.get("data") or payload
    groups = []
    if isinstance(result, dict):
        groups = result.get("groups") or result.get("chats") or result.get("sessions") or []
    elif isinstance(result, list):
        groups = result
    normalized = []
    for raw in groups:
        if not isinstance(raw, dict):
            continue
        group_id = raw.get("id") or raw.get("jid") or raw.get("chatId")
        if not group_id:
            continue
        normalized.append({
            "id": group_id,
            "name": raw.get("subject") or raw.get("name") or raw.get("title") or "Sem nome",
            "participantsCount": raw.get("participantsCount") or raw.get("participants") or raw.get("size"),
        })
    return normalized


def list_wapi_groups(timeout: float = 10.0) -> list[dict]:
    headers = {
        "Authorization": f"Bearer {WAPI_TOKEN}",
        "Content-Type": "application/json",
        "apikey": WAPI_TOKEN,
    }
    endpoints = [
        WAPI_GET_ALL_GROUPS_URL,
        WAPI_API_GROUPS_URL,
        WAPI_GROUPS_URL,
        WAPI_LEGACY_GROUPS_URL,
    ]
    response = None
    for url in endpoints:
        try:
            response = _fetch_groups_from(url, headers, timeout)
        except requests.RequestException:
            logger.warning("Falha ao consultar grupos WAPI (%s)", url)
            continue
        if response.status_code == 404:
            logger.warning("Endpoint de grupos WAPI respondeu 404: %s", url)
            continue
        break
    if not response:
        logger.warning("Nenhum endpoint de grupos respondeu com sucesso.")
        return []
    if response.status_code == 404:
        return []
    response.raise_for_status()
    payload = response.json()
    return _normalize_groups_payload(payload)


def ensure_group_exists(group_id: str, timeout: float = 10.0) -> None:
    now = time.time()
    cached = _group_cache.get("ts")
    groups = _group_cache.get("groups")
    if not groups or not cached or now - cached > 30:
        try:
            groups = list_wapi_groups(timeout=timeout)
            _group_cache["groups"] = groups
            _group_cache["ts"] = now
        except requests.RequestException as exc:
            logger.exception("Erro ao consultar grupos WAPI")
            raise ValueError("Não foi possível validar o grupo (verifique a sessão do WAPI).") from exc
    if not any(entry["id"] == group_id for entry in groups):
        logger.warning("Grupo %s não encontrado na sessão WAPI", group_id)
        raise ValueError("Grupo não encontrado na sessão atual do WhatsApp.")


def _log_send(to: str, dest_type: str, message: str, response: dict, ok: bool) -> None:
    log_payload = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "to": to,
        "type": dest_type,
        "message_length": len(message or "") if message else 0,
        "wapi_response": response,
        "ok": ok,
    }
    logger.info("WAPI send log: %s", log_payload)


def send_whatsapp_message(destination: str, message: str, timeout: float = 10.0) -> dict:
    to, dest_type = _normalize_destination(destination)
    if dest_type == "group":
        ensure_group_exists(to, timeout=timeout)
    payload = _build_payload(to, message)
    headers = {
        "Authorization": f"Bearer {WAPI_TOKEN}",
        "Content-Type": "application/json"
    }
    response = requests.post(WAPI_SEND_URL, headers=headers, json=payload, timeout=timeout)
    try:
        response.raise_for_status()
    except requests.RequestException:
        logger.exception("Erro ao enviar mensagem para %s via WAPI", to)
        _log_send(to, dest_type, message, {"error": "http_error"}, False)
        raise
    result = response.json()
    status, message_id = _normalize_response(result)
    ok_result = status in SUCCESS_STATUSES or bool(message_id)
    if not ok_result:
        logger.warning("Resposta inesperada do WAPI (%s): %s", to, result)
    _log_send(to, dest_type, message, result, ok_result)
    if not ok_result:
        raise requests.RequestException(f"WAPI retornou status {status} para {to}")
    return result
