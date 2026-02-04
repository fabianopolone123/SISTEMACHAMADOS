from django.conf import settings
from django.db import migrations, models


def _copy_assigned_to_working(apps, schema_editor):
    Ticket = apps.get_model('tickets', 'Ticket')
    for ticket in Ticket.objects.exclude(assigned_to__isnull=True):
        ticket.working_users.add(ticket.assigned_to)


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0008_programado_ticket_type'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='working_users',
            field=models.ManyToManyField(blank=True, related_name='working_tickets', to=settings.AUTH_USER_MODEL, verbose_name='Responsáveis TI adicionais'),
        ),
        migrations.RunPython(_copy_assigned_to_working, reverse_code=migrations.RunPython.noop),
    ]
