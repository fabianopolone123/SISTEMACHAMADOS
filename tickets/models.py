from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models

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
    file = models.FileField('Arquivo', upload_to='uploads/%Y/%m/%d')
    uploaded_at = models.DateTimeField('Enviado em', auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f'Anexo de {self.ticket} ({self.file.name})'


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
    created_at = models.DateTimeField('Registrado em', auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Mensagem'
        verbose_name_plural = 'Mensagens'

    def __str__(self):
        author = self.author.get_full_name() if self.author else 'Sistema'
        return f'{author}: {self.text[:40]}'


class WhatsAppRecipient(models.Model):
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
        return f'{self.phone_number}'
