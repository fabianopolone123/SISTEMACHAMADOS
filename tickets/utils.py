import re

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.mail import send_mail


def serialize_ticket(ticket):
    assigned = ticket.assigned_to
    responsibles = ticket.get_working_user_names()
    responsibles_display = ', '.join(responsibles) if responsibles else '—'
    return {
        'id': ticket.id,
        'title': ticket.title,
        'status': ticket.get_status_display(),
        'status_code': ticket.status,
        'urgency': ticket.get_urgency_display(),
        'urgency_code': ticket.urgency,
        'created_by': ticket.created_by.get_full_name() or ticket.created_by.username,
        'assigned_to': assigned.get_full_name() or assigned.username if assigned else None,
        'created_at': ticket.created_at.isoformat(),
        'type': ticket.get_ticket_type_display(),
        'type_code': ticket.ticket_type,
        'responsibles': responsibles,
        'responsibles_display': responsibles_display,
    }


def broadcast_ticket_event(event_type, ticket, payload=None):
    channel_layer = get_channel_layer()
    if not channel_layer:
        return
    data = {
        'type': 'ticket_update',
        'event': event_type,
        'ticket': serialize_ticket(ticket),
    }
    if payload:
        data['payload'] = payload
    async_to_sync(channel_layer.group_send)('tickets', data)


def send_ticket_email(recipient: str, subject: str, message: str):
    if not recipient or not subject or not message:
        return
    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [recipient],
        fail_silently=False,
    )


def normalize_phone_number(value: str, default_country_code: str = '55') -> str:
    if not value:
        raise ValueError('Telefone não pode ficar vazio.')
    digits = re.sub(r'\D', '', value)
    if not digits:
        raise ValueError('Telefone deve conter dígitos.')
    if digits.startswith('00'):
        digits = digits[2:]
    if len(digits) in {10, 11}:
        digits = f'{default_country_code}{digits}'
    if len(digits) not in {12, 13}:
        raise ValueError('Telefone precisa ter o código internacional (ex: 55149988208134).')
    if not digits.startswith(default_country_code):
        raise ValueError('Telefone deve começar com o código internacional (ex: 55...).')
    return digits
