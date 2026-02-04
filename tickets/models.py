from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
import os

from .utils import normalize_phone_number

User = get_user_model()


class TicketStatus(models.TextChoices):
    NEW = 'new', 'Novo'
    IN_PROGRESS = 'in_progress', 'Em andamento'
    AWAITING = 'awaiting', 'Aguardando resposta'
    RESOLVED = 'resolved', 'Resolvido'


class TicketUrgency(models.TextChoices):
    LOW = 'low', 'Baixa'
    NORMAL = 'normal', 'Normal'
    MEDIUM = 'medium', 'Média'
    HIGH = 'high', 'Alta'
    URGENT = 'urgent', 'Urgente'


class TicketType(models.TextChoices):
    INCIDENT = 'incident', 'Incidente'
    REQUEST = 'request', 'Solicitação'
    IMPROVEMENT = 'improvement', 'Melhoria'
    PROGRAMMED = 'programado', 'Programado'


class Ticket(models.Model):
    title = models.CharField('Título', max_length=140)
    description = models.TextField('Descrição')
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_tickets')
    assigned_to = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_tickets'
    )
    ticket_type = models.CharField('Tipo', max_length=20, choices=TicketType.choices, default=TicketType.INCIDENT)
    status = models.CharField('Status', max_length=20, choices=TicketStatus.choices, default=TicketStatus.NEW)
    urgency = models.CharField('Urgência', max_length=10, choices=TicketUrgency.choices, default=TicketUrgency.NORMAL)
    created_at = models.DateTimeField('Criado em', auto_now_add=True)
    updated_at = models.DateTimeField('Última atualização', auto_now=True)
    resolution = models.TextField('Resolução', blank=True, null=True)
    resolved_at = models.DateTimeField('Resolvido em', blank=True, null=True)
    working_users = models.ManyToManyField(
        User,
        verbose_name='Responsáveis TI adicionais',
        blank=True,
        related_name='working_tickets',
    )

    class Meta:
        verbose_name = 'Chamado'
        verbose_name_plural = 'Chamados'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.title} ({self.get_status_display()})'

    @staticmethod
    def _format_user(user):
        if not user:
            return None
        return user.get_full_name() or user.username

    def get_working_user_names(self):
        users = list(self.working_users.all())
        names = [self._format_user(user) for user in users if user]
        if not names and self.assigned_to:
            primary = self._format_user(self.assigned_to)
            if primary:
                names.append(primary)
        return names

    @property
    def working_users_display(self):
        names = self.get_working_user_names()
        if not names:
            return '—'
        return ', '.join(names)


class TicketAttachment(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='attachments')
    message = models.ForeignKey('TicketMessage', on_delete=models.CASCADE, related_name='attachments', blank=True, null=True)
    file = models.FileField('Arquivo', upload_to='uploads/%Y/%m/%d')
    uploaded_at = models.DateTimeField('Enviado em', auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f'Anexo de {self.ticket} ({self.file.name})'

    @property
    def filename(self):
        return os.path.basename(self.file.name) if self.file.name else ''


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='perfil')
    setor = models.CharField('Setor', max_length=120)

    class Meta:
        verbose_name = 'Perfil'
        verbose_name_plural = 'Perfis'

    def __str__(self):
        return f'{self.user.username} ({self.setor})'


class TicketMessage(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='messages')
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='ticket_messages')
    text = models.TextField('Mensagem')
    is_internal = models.BooleanField('Nota interna (visível só para TI)', default=False)
    created_at = models.DateTimeField('Registrado em', auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Mensagem'
        verbose_name_plural = 'Mensagens'

    def __str__(self):
        author = self.author.get_full_name() if self.author else 'Sistema'
        return f'{author}: {self.text[:40]}'


class TicketEvent(models.Model):
    class EventType(models.TextChoices):
        CREATED = 'created', 'Criação'
        STATUS = 'status_change', 'Mudança de status'
        COMMENT = 'comment', 'Comentário'
        WORKING_USER = 'working_user', 'Responsável adicional'

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='events')
    event_type = models.CharField('Tipo do evento', max_length=30, choices=EventType.choices)
    description = models.TextField('Descrição', blank=True)
    status = models.CharField('Status relacionado', max_length=20, choices=TicketStatus.choices, blank=True, null=True)
    performed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    timestamp = models.DateTimeField('Registrado em', auto_now_add=True)

    class Meta:
        verbose_name = 'Evento de chamado'
        verbose_name_plural = 'Eventos de chamados'
        ordering = ['-timestamp']

    def __str__(self):
        actor = self.performed_by.get_full_name() if self.performed_by else 'Sistema'
        return f'{self.ticket} · {self.get_event_type_display()} por {actor} em {self.timestamp:%d/%m/%Y %H:%M}'


class InventoryStatus(models.TextChoices):
    IN_USE = 'in_use', 'Em uso'
    STOCK = 'stock', 'Estoque'
    MAINTENANCE = 'maintenance', 'Manutencao'
    DISCARDED = 'discarded', 'Descartado'


class InventoryItem(models.Model):
    name = models.CharField('Item', max_length=140)
    category = models.CharField('Categoria', max_length=80)
    asset_tag = models.CharField('Patrimonio', max_length=60, blank=True)
    serial_number = models.CharField('Numero de serie', max_length=80, blank=True)
    location = models.CharField('Localizacao', max_length=120, blank=True)
    assigned_to = models.CharField('Responsavel', max_length=120, blank=True)
    status = models.CharField('Status', max_length=20, choices=InventoryStatus.choices, default=InventoryStatus.STOCK)
    notes = models.TextField('Observacoes', blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='inventory_items_created')
    created_at = models.DateTimeField('Criado em', auto_now_add=True)
    updated_at = models.DateTimeField('Atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'Item de inventario'
        verbose_name_plural = 'Itens de inventario'
        ordering = ['name', 'id']

    def __str__(self):
        label = self.asset_tag or self.serial_number or str(self.pk)
        return f'{self.name} ({label})'


class WhatsAppRecipient(models.Model):
    name = models.CharField('Nome', max_length=120, default='')
    phone_number = models.CharField('Telefone', max_length=20, unique=True)
    original_input = models.CharField('Entrada original', max_length=32, blank=True)
    added_at = models.DateTimeField('Adicionado em', auto_now_add=True)

    class Meta:
        verbose_name = 'Destinatário WhatsApp'
        verbose_name_plural = 'Destinatários WhatsApp'
        ordering = ['-added_at']

    def clean(self):
        super().clean()
        try:
            normalized = normalize_phone_number(self.phone_number)
        except ValueError as exc:
            raise ValidationError({'phone_number': str(exc)})
        self.original_input = self.phone_number
        self.phone_number = normalized

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.name} ({self.phone_number})'
