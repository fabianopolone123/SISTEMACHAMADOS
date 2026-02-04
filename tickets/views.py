import json
import logging
from datetime import datetime
from textwrap import shorten

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db import models
from django.db.models import Case, Count, IntegerField, Value, When, Q
from django.db.models.functions import TruncMonth
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from requests import RequestException

from .forms import (
    InventoryItemForm,
    WhatsAppRecipientForm,
    RegisterForm,
    ResolutionForm,
    TicketForm,
    TicketMessageForm,
    ProfileUpdateForm,
    PasswordUpdateForm,
)
from .models import (
    InventoryItem,
    WhatsAppRecipient,
    Ticket,
    TicketStatus,
    TicketUrgency,
    TicketAttachment,
    TicketType,
    TicketEvent,
)
from .utils import broadcast_ticket_event, serialize_ticket, send_ticket_email
from .wapi import send_whatsapp_message, list_wapi_groups

logger = logging.getLogger(__name__)
TI_CHAMADOS_GROUP_JID = getattr(settings, "WAPI_DEFAULT_GROUP_JID", "120363421981424263@g.us")

User = get_user_model()
TI_GROUP_NAME = 'TI'

URGENCY_PRIORITY = {
    TicketUrgency.URGENT: 5,
    TicketUrgency.HIGH: 4,
    TicketUrgency.MEDIUM: 3,
    TicketUrgency.NORMAL: 2,
    TicketUrgency.LOW: 1,
}


def _is_ti(user):
    return user.groups.filter(name=TI_GROUP_NAME).exists() or user.is_staff


def _get_ti_group():
    group, _ = Group.objects.get_or_create(name=TI_GROUP_NAME)
    return group


def _ticket_priority_case():
    return Case(
        *(When(urgency=urgency, then=Value(priority)) for urgency, priority in URGENCY_PRIORITY.items()),
        output_field=IntegerField(),
    )


def _filtered_dashboard_queryset(user):
    is_ti_user = _is_ti(user)
    base_qs = Ticket.objects.select_related('created_by', 'assigned_to').prefetch_related('working_users')
    if is_ti_user:
        filtered = base_qs.exclude(status=TicketStatus.RESOLVED)
    else:
        filtered = base_qs.filter(created_by=user)
    return is_ti_user, filtered


def _ordered_dashboard_queryset(queryset, is_ti_user):
    if is_ti_user:
        return (
            queryset
            .annotate(
                priority=_ticket_priority_case(),
                non_ti_requester=Case(
                    When(
                        Q(created_by__is_staff=True) | Q(created_by__groups__name='TI'),
                        then=Value(0)
                    ),
                    default=Value(1),
                    output_field=IntegerField(),
                ),
            )
            .order_by('-priority', '-non_ti_requester', 'created_at')
        )
    return queryset.order_by('-created_at')


def _build_urgency_counts(queryset):
    counts = {choice[0]: 0 for choice in TicketUrgency.choices}
    for row in queryset.values('urgency').annotate(count=Count('id')):
        counts[row['urgency']] = row['count']
    return counts


def _gather_dashboard(request_user):
    is_ti_user, filtered_qs = _filtered_dashboard_queryset(request_user)
    ticket_queryset = _ordered_dashboard_queryset(filtered_qs, is_ti_user)
    urgency_counts = _build_urgency_counts(filtered_qs)
    if is_ti_user:
        dashboard_title = 'Painel de Atendimento TI'
        highlight = 'Priorize chamados mais urgentes antes dos demais.'
    else:
        dashboard_title = 'Meus Chamados'
        highlight = 'Acompanhe o andamento do seu chamado e responda aos contatos da equipe de TI.'
    return {
        'is_ti': is_ti_user,
        'dashboard_title': dashboard_title,
        'highlight': highlight,
        'ticket_queryset': ticket_queryset,
        'ticket_count': ticket_queryset.count(),
        'urgency_counts': urgency_counts,
    }


def _update_ticket_status(ticket, *, status, assigned=None, resolution_text=None, extra_payload=None, performed_by=None):
    ticket.status = status
    if assigned is not None:
        ticket.assigned_to = assigned
    if status == TicketStatus.RESOLVED:
        ticket.resolved_at = timezone.now()
        ticket.resolution = resolution_text or ticket.resolution
    else:
        ticket.resolved_at = None
    ticket.save()
    payload = {'status': ticket.status}
    if extra_payload:
        payload.update(extra_payload)
    description_parts = [f"Status alterado para {ticket.get_status_display()}"]
    if assigned:
        description_parts.append(f"Responsável: {assigned.get_full_name() or assigned.username}")
    if resolution_text:
        description_parts.append(f"Resolução: {shorten(resolution_text.strip(), width=200, placeholder='...')}")
    pause_reason = extra_payload.get('pause_reason') if extra_payload else None
    if pause_reason:
        description_parts.append(f"Motivo da pausa: {shorten(pause_reason.strip(), width=200, placeholder='...')}")
    TicketEvent.objects.create(
        ticket=ticket,
        event_type=TicketEvent.EventType.STATUS,
        description=' · '.join(description_parts),
        status=status,
        performed_by=performed_by,
    )
    if assigned:
        ticket.working_users.add(assigned)
    broadcast_ticket_event('ticket_status_changed', ticket, payload)
    extra_lines = [f"Status atual: {ticket.get_status_display()}"]
    if ticket.assigned_to:
        extra_lines.append(f"Responsável: {ticket.assigned_to.get_full_name() or ticket.assigned_to.username}")
    if resolution_text:
        extra_lines.append(f"Resolução: {shorten(resolution_text.strip(), width=200, placeholder='...')}")
    if pause_reason:
        extra_lines.append(f"Motivo da pausa: {shorten(pause_reason.strip(), width=200, placeholder='...')}")
    _notify_whatsapp(ticket, event_label="Status atualizado", extra_line="\n".join(extra_lines))
    _notify_ticket_email(
        ticket,
        f"[Chamado #{ticket.id}] Status atualizado",
        "\n".join([
            f"Status atual: {ticket.get_status_display()}",
            *(extra_lines[1:] if len(extra_lines) > 1 else []),
        ])
    )


def _handle_ticket_action(request, ticket, is_ti_user, resolution_form):
    action = request.POST.get('action')
    if not action:
        return None
    next_url = request.POST.get('next')
    if not next_url:
        next_url = reverse('ticket_detail', kwargs={'pk': ticket.pk})
    if action == 'resolve' and is_ti_user:
        if resolution_form.is_valid():
            _update_ticket_status(
                ticket,
                status=TicketStatus.RESOLVED,
                resolution_text=resolution_form.cleaned_data['resolution'],
                performed_by=request.user,
            )
            return redirect('dashboard')
        return None
    if action == 'in_progress' and is_ti_user:
        _update_ticket_status(ticket, status=TicketStatus.IN_PROGRESS, assigned=request.user, performed_by=request.user)
        return redirect(next_url)
    if action == 'awaiting' and is_ti_user:
        pause_reason = request.POST.get('pause_reason', '').strip()
        if not pause_reason:
            messages.error(request, 'Informe o motivo da pausa antes de marcar o chamado como pendente.')
            return None
        _update_ticket_status(
            ticket,
            status=TicketStatus.AWAITING,
            performed_by=request.user,
            extra_payload={'pause_reason': pause_reason},
        )
        return redirect(next_url)
    if action == 'reopen' and ticket.status == TicketStatus.RESOLVED:
        _update_ticket_status(ticket, status=TicketStatus.IN_PROGRESS, performed_by=request.user)
        return redirect(next_url)
    if action == 'join_work' and is_ti_user:
        assigned = ticket.assigned_to or request.user
        if ticket.status != TicketStatus.IN_PROGRESS:
            _update_ticket_status(
                ticket,
                status=TicketStatus.IN_PROGRESS,
                assigned=assigned,
                performed_by=request.user,
            )
        _add_working_user(ticket, request.user)
        messages.success(request, 'Você agora aparece como responsável ativo por este chamado.')
        return redirect(next_url)
    return None


def _build_search_query(texts):
    query = models.Q()
    for text in texts:
        clean_text = (text or '').strip()
        if not clean_text:
            continue
        query |= models.Q(title__icontains=clean_text)
        query |= models.Q(description__icontains=clean_text)
        query |= models.Q(resolution__icontains=clean_text)
    return query


@login_required
def whatsapp_config(request):
    if not _is_ti(request.user):
        raise PermissionDenied

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'delete_recipient':
            recipient_id = request.POST.get('recipient_id')
            recipient = get_object_or_404(WhatsAppRecipient, pk=recipient_id)
            recipient.delete()
            messages.success(request, 'Contato removido da lista de envio particular.')
            return redirect('whatsapp_config')

        recipient_form = WhatsAppRecipientForm(request.POST, prefix='recipient')
        if recipient_form.is_valid():
            recipient_form.save()
            messages.success(request, 'Contato WhatsApp salvo com sucesso.')
            return redirect('whatsapp_config')
        messages.error(request, 'Não foi possível salvar o contato. Revise os campos e tente novamente.')
    else:
        recipient_form = WhatsAppRecipientForm(prefix='recipient')

    group_name = TI_CHAMADOS_GROUP_JID
    group_error = None
    try:
        groups = list_wapi_groups()
        match = next((group for group in groups if group.get('id') == TI_CHAMADOS_GROUP_JID), None)
        if match:
            group_name = match.get('name') or TI_CHAMADOS_GROUP_JID
    except RequestException as exc:
        group_error = str(exc)

    context = {
        'configured_group_id': TI_CHAMADOS_GROUP_JID,
        'configured_group_name': group_name,
        'group_error': group_error,
        'recipient_form': recipient_form,
        'recipients': WhatsAppRecipient.objects.all(),
    }
    return render(request, 'whatsapp_config.html', context)


@login_required
def whatsapp_notify_group(request):
    if not _is_ti(request.user):
        raise PermissionDenied
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    message = request.POST.get('message', '').strip()
    if not message:
        messages.error(request, 'Informe uma mensagem para o grupo.')
        return redirect('whatsapp_config')
    try:
        send_whatsapp_message(TI_CHAMADOS_GROUP_JID, message)
    except ValueError as exc:
        messages.error(request, f'Erro ao enviar para o grupo: {exc}')
    except RequestException as exc:
        messages.error(request, f'Falha no envio via WAPI: {exc}')
    else:
        messages.success(request, 'Notificação enviada para o grupo configurado.')
    return redirect('whatsapp_config')

def _destination_type(destination: str) -> str:
    return "group" if destination and destination.lower().endswith("@g.us") else "contact"


@login_required
def whatsapp_send(request):
    if not _is_ti(request.user):
        raise PermissionDenied
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "JSON inválido"}, status=400)
    to = payload.get("to")
    message = payload.get("message")
    if not to or not message:
        return JsonResponse({"ok": False, "error": "Informe 'to' e 'message'."}, status=400)
    try:
        result = send_whatsapp_message(to, message)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except RequestException as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=502)
    return JsonResponse({
        "ok": True,
        "to": to,
        "type": _destination_type(to),
        "provider": "wapi",
        "response": result,
    })


@login_required
def whatsapp_send_ti_chamados(request):
    if not _is_ti(request.user):
        raise PermissionDenied
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "JSON inválido"}, status=400)
    message = payload.get("message")
    if not message:
        return JsonResponse({"ok": False, "error": "Campo 'message' é obrigatório."}, status=400)
    try:
        result = send_whatsapp_message(TI_CHAMADOS_GROUP_JID, message)
    except RequestException as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=502)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({
        "ok": True,
        "to": TI_CHAMADOS_GROUP_JID,
        "type": "group",
        "provider": "wapi",
        "response": result,
    })


@login_required
def whatsapp_groups(request):
    if not _is_ti(request.user):
        raise PermissionDenied
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    try:
        groups = list_wapi_groups()
    except RequestException as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=502)
    return JsonResponse({"ok": True, "groups": groups})


def _build_whatsapp_summary(ticket, event_label="Novo chamado", extra_line=None):
    label = (event_label or '').strip().lower()
    title = shorten((ticket.title or '').strip(), width=120, placeholder='...')
    description = shorten((ticket.description or '').strip(), width=160, placeholder='...')
    detail = shorten(((extra_line or '').replace('\n', ' ')).strip(), width=180, placeholder='...')

    if 'nova mensagem' in label:
        return f"💬 Nova mensagem no chamado: {detail or description or title}"
    if 'status atualizado' in label or 'atualizado' in label:
        return f"🔄 Atualizado: {detail or ticket.get_status_display()}"
    return f"🆕 Novo chamado: {title} | Mensagem: {description or '-'}"


def _notify_whatsapp(ticket, event_label="Novo chamado", extra_line=None):
    summary = _build_whatsapp_summary(ticket, event_label=event_label, extra_line=extra_line)
    try:
        send_whatsapp_message(TI_CHAMADOS_GROUP_JID, summary)
    except RequestException:
        logger.exception("Nao foi possivel notificar o grupo WhatsApp %s", TI_CHAMADOS_GROUP_JID)


def _notify_ticket_email(ticket, subject: str, body: str):
    recipient = ticket.created_by.email
    if not recipient:
        return
    try:
        send_ticket_email(recipient, subject, body)
    except Exception:
        logger.exception("Erro ao enviar e-mail para %s", recipient)


def _add_working_user(ticket, user):
    if not user or ticket.status == TicketStatus.RESOLVED:
        return
    ticket.working_users.add(user)
    TicketEvent.objects.create(
        ticket=ticket,
        event_type=TicketEvent.EventType.WORKING_USER,
        description=f"{user.get_full_name() or user.username} passou a acompanhar o chamado",
        performed_by=user,
    )


@login_required
def dashboard(request):
    sprint = _gather_dashboard(request.user)
    ticket_queryset = sprint['ticket_queryset']
    recent_tickets = ticket_queryset if sprint['is_ti'] else ticket_queryset[:10]
    context = {
        'is_ti': sprint['is_ti'],
        'dashboard_title': sprint['dashboard_title'],
        'highlight': sprint['highlight'],
        'recent_tickets': recent_tickets,
        'ticket_count': sprint['ticket_count'],
        'urgency_counts': sprint['urgency_counts'],
        'urgency_choices': TicketUrgency.choices,
        'urgency_cards': [
            {
                'value': value,
                'label': label,
                'count': sprint['urgency_counts'].get(value, 0),
            }
            for value, label in TicketUrgency.choices
        ],
    }
    return render(request, 'dashboard.html', context)


@login_required
def dashboard_data(request):
    sprint = _gather_dashboard(request.user)
    ticket_queryset = sprint['ticket_queryset']
    recent_tickets = ticket_queryset if sprint['is_ti'] else ticket_queryset[:10]
    return JsonResponse({
        'tickets': [serialize_ticket(ticket) for ticket in recent_tickets],
        'ticket_count': sprint['ticket_count'],
        'urgency_counts': sprint['urgency_counts'],
    })


@login_required
def create_ticket(request):
    form = TicketForm(request.POST or None, request.FILES or None)
    if form.is_valid():
        ticket = form.save(commit=False)
        ticket.created_by = request.user
        ticket.save()
        attachments = request.FILES.getlist('attachments')
        for attachment in attachments:
            TicketAttachment.objects.create(ticket=ticket, file=attachment)
        TicketEvent.objects.create(
            ticket=ticket,
            event_type=TicketEvent.EventType.CREATED,
            description=f"Chamado criado por {ticket.created_by.get_full_name() or ticket.created_by.username}",
            status=ticket.status,
            performed_by=ticket.created_by,
        )
        messages.success(request, 'Chamado registrado! Você será redirecionado ao dashboard.')
        broadcast_ticket_event('ticket_created', ticket)
        _notify_whatsapp(ticket)
        return redirect('dashboard')
    return render(request, 'ticket_form.html', {'form': form})


@login_required
def ticket_detail(request, pk):
    ticket = Ticket.objects.select_related('assigned_to').prefetch_related('working_users').filter(pk=pk).first()
    if not ticket:
        context = {
            'message_title': 'Chamado não encontrado',
            'message_body': 'O chamado que você está tentando acessar foi removido ou o número está incorreto. Volte ao painel e abra um chamado válido.',
            'back_url': reverse('dashboard'),
        }
        return render(request, 'ticket_not_found.html', context, status=404)
    if not (_is_ti(request.user) or ticket.created_by == request.user):
        raise PermissionDenied

    resolution_form = ResolutionForm(request.POST or None)
    message_form = TicketMessageForm(request.POST or None, request.FILES or None)
    is_ti_user = _is_ti(request.user)
    if request.method == 'POST':
        action_response = _handle_ticket_action(request, ticket, is_ti_user, resolution_form)
        if action_response:
            return action_response
        if message_form.is_valid():
            message = message_form.save(commit=False)
            message.ticket = ticket
            message.author = request.user
            is_internal_flag = message_form.cleaned_data.get('internal_note', False)
            message.is_internal = bool(is_internal_flag) if is_ti_user else False
            message.save()
            broadcast_ticket_event(
                'ticket_message',
                ticket,
                {
                    'message': {
                        'author': request.user.get_full_name() or request.user.username,
                        'text': message.text,
                        'created_at': message.created_at.isoformat(),
                    }
                }
            )
            if _is_ti(request.user):
                _notify_ticket_email(
                    ticket,
                    f"[Chamado #{ticket.id}] Mensagem da TI",
                    "\n".join([
                        f"Mensagem de {request.user.get_full_name() or request.user.username}",
                        shorten(message.text.strip(), width=400, placeholder='...')
                    ])
                )
            _notify_whatsapp(
                ticket,
                event_label="Nova mensagem no chamado",
                extra_line=(
                    f"📩 Mensagem de {request.user.get_full_name() or request.user.username}:\n"
                    f"{shorten(message.text.strip(), width=200, placeholder='...')}"
                )
            )
            attachments = message_form.cleaned_data.get('attachments') or []
            for attachment in attachments:
                TicketAttachment.objects.create(ticket=ticket, file=attachment, message=message)
            return redirect('ticket_detail', pk=pk)

    messages_qs = ticket.messages.select_related('author').prefetch_related('attachments')
    working_user_names = ticket.get_working_user_names()
    is_working_user = ticket.working_users.filter(pk=request.user.pk).exists()
    public_messages = [msg for msg in messages_qs if not msg.is_internal]
    internal_messages = [msg for msg in messages_qs if msg.is_internal]

    orphan_attachments = ticket.attachments.filter(message__isnull=True)
    has_working_users = ticket.working_users.exists()
    context = {
        'ticket': ticket,
        'messages': messages_qs,
        'message_form': message_form,
        'resolution_form': resolution_form,
        'is_ti': is_ti_user,
        'ticket_status': TicketStatus,
        'urgency_choices': TicketUrgency.choices,
        'working_user_names': working_user_names,
        'is_working_user': is_working_user,
        'public_messages': public_messages,
        'internal_messages': internal_messages,
        'orphan_attachments': orphan_attachments,
        'has_working_users': has_working_users,
    }
    return render(request, 'ticket_detail.html', context)


@login_required
def finished_tickets(request):
    if not _is_ti(request.user):
        raise PermissionDenied
    query_text = request.GET.get('q', '')
    tickets = Ticket.objects.filter(status=TicketStatus.RESOLVED)
    search_query = _build_search_query([query_text])
    if search_query:
        tickets = tickets.filter(search_query)
    tickets = tickets.order_by('-resolved_at', '-created_at')
    context = {
        'tickets': tickets,
        'query': query_text or '',
    }
    return render(request, 'finished_tickets.html', context)


@login_required
def related_tickets(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    if not _is_ti(request.user):
        raise PermissionDenied
    search_text = request.GET.get('q')
    source_texts = [search_text.strip()] if search_text else []
    if not source_texts:
        source_texts = [ticket.title or '']
        if ticket.description:
            source_texts.append(ticket.description)
        if ticket.resolution:
            source_texts.append(ticket.resolution)
    query = _build_search_query(source_texts)
    suggestions = (
        Ticket.objects.filter(status=TicketStatus.RESOLVED)
        .filter(query)
        .exclude(pk=ticket.pk)
        .order_by('-resolved_at')[:6]
    )
    data = [
        {
            'title': t.title,
            'urgency': t.get_urgency_display(),
            'resolved_at': t.resolved_at.isoformat() if t.resolved_at else '',
            'resolution': t.resolution,
            'url': f'/chamado/{t.pk}/',
        }
        for t in suggestions
    ]
    return JsonResponse({'tickets': data})


def register(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    form = RegisterForm(request.POST or None)
    if form.is_valid():
        user = form.save()
        login(request, user)
        return redirect('dashboard')

    return render(request, 'register.html', {'form': form})


def logout_view(request):
    logout(request)
    return redirect('login')


@login_required
def manage_users(request):
    if not _is_ti(request.user):
        raise PermissionDenied
    users = list(User.objects.select_related('perfil').prefetch_related('groups').all())
    for user in users:
        group_names = [group.name for group in user.groups.all()]
        user.is_ti_member = user.is_staff or TI_GROUP_NAME in group_names
        if user.is_staff and TI_GROUP_NAME not in group_names:
            group_names.append(TI_GROUP_NAME)
        user.group_names = ', '.join(group_names) if group_names else 'Usuario'
    promotable_users = [user for user in users if not user.is_ti_member]
    if request.method == 'POST':
        action = request.POST.get('action')
        user_id = request.POST.get('user_id')
        target = get_object_or_404(User, pk=user_id)
        if action == 'reset_password':
            target.set_password('1234')
            target.save()
            messages.success(request, f"Senha de {target.username} foi resetada para 1234.")
        elif action == 'grant_ti':
            ti_group = _get_ti_group()
            target.groups.add(ti_group)
            if not target.is_staff:
                target.is_staff = True
            target.save()
            messages.success(request, f"O usuario {target.username} agora faz parte do time TI.")
        elif action == 'revoke_ti' and target != request.user:
            ti_group = _get_ti_group()
            target.groups.remove(ti_group)
            if not target.is_superuser:
                target.is_staff = False
            target.save()
            messages.success(request, f"Acesso ao time TI removido para {target.username}.")
        elif action == 'delete_user' and target != request.user:
            target.delete()
            messages.success(request, f"Usuário {target.username} excluído.")
        return redirect('manage_users')
    return render(request, 'users_management.html', {
        'users': users,
        'promotable_users': promotable_users,
    })


@login_required
def inventory_management(request):
    if not _is_ti(request.user):
        raise PermissionDenied
    form = InventoryItemForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        item = form.save(commit=False)
        item.created_by = request.user
        item.save()
        messages.success(request, f'Item "{item.name}" adicionado ao inventario.')
        return redirect('inventory_management')
    items = InventoryItem.objects.all()
    return render(request, 'inventory_management.html', {
        'form': form,
        'items': items,
        'is_ti': True,
    })


@login_required
def profile(request):
    profile_form = ProfileUpdateForm(request.POST or None, user=request.user)
    password_form = PasswordUpdateForm(request.POST or None)
    if request.method == 'POST':
        if 'profile_submit' in request.POST and profile_form.is_valid():
            profile_form.save()
            messages.success(request, 'Dados atualizados.')
            return redirect('profile')
        if 'password_submit' in request.POST and password_form.is_valid():
            password_form.save(request.user)
            update_session_auth_hash(request, request.user)
            messages.success(request, 'Senha alterada.')
            return redirect('profile')
    return render(request, 'profile.html', {
        'profile_form': profile_form,
        'password_form': password_form,
        'is_ti': _is_ti(request.user),
    })


@login_required
def ti_reports(request):
    if not _is_ti(request.user):
        raise PermissionDenied

    filters = {
        'status': request.GET.get('status', ''),
        'urgency': request.GET.get('urgency', ''),
        'ticket_type': request.GET.get('ticket_type', ''),
        'from_date': request.GET.get('from_date', ''),
        'to_date': request.GET.get('to_date', ''),
    }

    queryset = Ticket.objects.select_related('created_by', 'assigned_to')
    if filters['status']:
        queryset = queryset.filter(status=filters['status'])
    if filters['urgency']:
        queryset = queryset.filter(urgency=filters['urgency'])
    if filters['ticket_type']:
        queryset = queryset.filter(ticket_type=filters['ticket_type'])

    def _parse_date(value):
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return None

    start_date = _parse_date(filters['from_date'])
    if start_date:
        queryset = queryset.filter(created_at__date__gte=start_date)
    end_date = _parse_date(filters['to_date'])
    if end_date:
        queryset = queryset.filter(created_at__date__lte=end_date)

    status_labels = dict(TicketStatus.choices)
    urgency_labels = dict(TicketUrgency.choices)
    type_labels = dict(TicketType.choices)
    status_counts = {key: 0 for key in status_labels}
    for row in queryset.values('status').annotate(count=Count('id')):
        status_counts[row['status']] = row['count']

    urgency_counts = {key: 0 for key in urgency_labels}
    for row in queryset.values('urgency').annotate(count=Count('id')):
        urgency_counts[row['urgency']] = row['count']

    type_counts = {key: 0 for key in type_labels}
    for row in queryset.values('ticket_type').annotate(count=Count('id')):
        type_counts[row['ticket_type']] = row['count']

    monthly_qs = (
        queryset
        .annotate(month=TruncMonth('created_at'))
        .values('month')
        .annotate(count=Count('id'))
        .order_by('month')
    )
    monthly_data = [
        {
            'label': row['month'].strftime('%b %Y') if row['month'] else 'Sem data',
            'count': row['count'],
        }
        for row in monthly_qs
    ]

    event_qs = (
        TicketEvent.objects.select_related('ticket', 'performed_by')
        .filter(ticket__in=queryset)
    )
    if start_date:
        event_qs = event_qs.filter(timestamp__date__gte=start_date)
    if end_date:
        event_qs = event_qs.filter(timestamp__date__lte=end_date)
    event_qs = event_qs.order_by('timestamp')

    events_by_day = {}
    for event in event_qs:
        local_date = timezone.localtime(event.timestamp).date()
        events_by_day.setdefault(local_date, []).append(event)
    events_timeline = [
        {'date': day, 'events': events_by_day[day]}
        for day in sorted(events_by_day)
    ]

    status_chart = [{'label': status_labels[key], 'count': status_counts[key]} for key in status_labels]
    urgency_chart = [{'label': urgency_labels[key], 'count': urgency_counts[key]} for key in urgency_labels]
    type_chart = [{'label': type_labels[key], 'count': type_counts[key]} for key in type_labels]

    status_breakdown = [
        {'label': label, 'count': status_counts[key]}
        for key, label in TicketStatus.choices
    ]
    urgency_breakdown = [
        {'label': label, 'count': urgency_counts[key]}
        for key, label in TicketUrgency.choices
    ]
    type_breakdown = [
        {'label': label, 'count': type_counts[key]}
        for key, label in TicketType.choices
    ]

    active_filters = []
    if filters['status']:
        active_filters.append(f"Status: {status_labels.get(filters['status'], filters['status'])}")
    if filters['urgency']:
        active_filters.append(f"Urgência: {urgency_labels.get(filters['urgency'], filters['urgency'])}")
    if filters['ticket_type']:
        active_filters.append(f"Tipo: {type_labels.get(filters['ticket_type'], filters['ticket_type'])}")
    if filters['from_date']:
        active_filters.append(f"A partir de {filters['from_date']}")
    if filters['to_date']:
        active_filters.append(f"Até {filters['to_date']}")

    total_tickets = queryset.count()
    resolved_count = status_counts.get(TicketStatus.RESOLVED, 0)
    pending_count = total_tickets - resolved_count

    context = {
        'filters': filters,
        'status_chart_data': json.dumps(status_chart),
        'urgency_chart_data': json.dumps(urgency_chart),
        'type_chart_data': json.dumps(type_chart),
        'monthly_chart_data': json.dumps(monthly_data),
        'recent_tickets': queryset.order_by('-created_at')[:15],
        'total_tickets': total_tickets,
        'status_counts': status_counts,
        'urgency_counts': urgency_counts,
        'type_counts': type_counts,
        'resolved_count': resolved_count,
        'pending_count': pending_count,
        'status_breakdown': status_breakdown,
        'urgency_breakdown': urgency_breakdown,
        'type_breakdown': type_breakdown,
        'filters_summary': ' • '.join(active_filters) if active_filters else 'Nenhum filtro aplicado.',
        'events_timeline': events_timeline,
    }
    context.update({
        'status_choices': TicketStatus.choices,
        'urgency_choices': TicketUrgency.choices,
        'type_choices': TicketType.choices,
        'status_labels': status_labels,
        'urgency_labels': urgency_labels,
        'type_labels': type_labels,
    })
    return render(request, 'reports.html', context)
