from django.db import migrations, models


def _rename_scheduled_to_programado(apps, schema_editor):
    Ticket = apps.get_model('tickets', 'Ticket')
    Ticket.objects.filter(ticket_type='scheduled').update(ticket_type='programado')


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0007_whatsapprecipient'),
    ]

    operations = [
        migrations.RunPython(_rename_scheduled_to_programado, reverse_code=migrations.RunPython.noop),
        migrations.AlterField(
            model_name='ticket',
            name='ticket_type',
            field=models.CharField(
                choices=[
                    ('incident', 'Incidente'),
                    ('request', 'Solicitação'),
                    ('improvement', 'Melhoria'),
                    ('programado', 'Programado'),
                ],
                default='incident',
                max_length=20,
                verbose_name='Tipo',
            ),
        ),
    ]
