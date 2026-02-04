from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import override_settings
from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch

from .models import InventoryItem, Ticket, WhatsAppRecipient
from .views import _build_whatsapp_summary


class ManageUsersViewTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.ti_group = Group.objects.create(name='TI')
        self.manager = self.user_model.objects.create_user(
            username='manager',
            password='1234',
        )
        self.manager.groups.add(self.ti_group)
        self.manager.is_staff = True
        self.manager.save()

    def test_promote_button_is_available_for_regular_user(self):
        employee = self.user_model.objects.create_user(
            username='employee',
            password='1234',
        )

        self.client.force_login(self.manager)
        response = self.client.get(reverse('manage_users'))

        self.assertEqual(response.status_code, 200)
        self.assertIn(employee, response.context['promotable_users'])
        self.assertContains(response, 'Promover a TI')

    def test_grant_ti_promotes_user_to_group_and_staff(self):
        employee = self.user_model.objects.create_user(
            username='employee2',
            password='1234',
        )

        self.client.force_login(self.manager)
        response = self.client.post(reverse('manage_users'), {
            'action': 'grant_ti',
            'user_id': str(employee.id),
        })
        self.assertEqual(response.status_code, 302)

        employee.refresh_from_db()
        self.assertTrue(employee.is_staff)
        self.assertTrue(employee.groups.filter(name='TI').exists())


class WhatsAppSummaryTests(TestCase):
    def test_summary_is_concise_and_keeps_core_data(self):
        user_model = get_user_model()
        requester = user_model.objects.create_user(
            username='solicitante',
            password='1234',
            first_name='Solicitante Nome',
        )
        ticket = Ticket.objects.create(
            title='Sistema ERP nao abre na estacao do faturamento desde cedo',
            description='Erro ao iniciar o ERP com tela branca e sem resposta. '
                        'Ja reiniciamos a maquina duas vezes e continua igual.',
            created_by=requester,
        )

        summary = _build_whatsapp_summary(
            ticket,
            event_label='Status atualizado',
            extra_line='Responsavel: Joao da TI. Motivo da pausa: aguardando retorno do fornecedor com patch.',
        )

        self.assertTrue(summary.startswith('🔄 Atualizado:'))
        self.assertIn('Responsavel: Joao da TI', summary)
        self.assertEqual(len(summary.splitlines()), 1)

    def test_new_ticket_summary_includes_description(self):
        user_model = get_user_model()
        requester = user_model.objects.create_user(
            username='solicitante2',
            password='1234',
        )
        ticket = Ticket.objects.create(
            title='Impressora nao responde',
            description='A impressora do financeiro parou e mostra erro de conexao.',
            created_by=requester,
        )

        summary = _build_whatsapp_summary(ticket, event_label='Novo chamado')
        self.assertTrue(summary.startswith('🆕 Novo chamado:'))
        self.assertIn('Mensagem:', summary)
        self.assertIn('A impressora do financeiro', summary)


class InventoryManagementViewTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.ti_group = Group.objects.create(name='TI')
        self.ti_user = self.user_model.objects.create_user(
            username='ti_user',
            password='1234',
        )
        self.ti_user.groups.add(self.ti_group)

    def test_non_ti_cannot_access_inventory_page(self):
        normal_user = self.user_model.objects.create_user(
            username='normal_user',
            password='1234',
        )
        self.client.force_login(normal_user)
        response = self.client.get(reverse('inventory_management'))
        self.assertEqual(response.status_code, 403)

    def test_ti_can_create_inventory_item(self):
        self.client.force_login(self.ti_user)
        response = self.client.post(
            reverse('inventory_management'),
            {
                'name': 'Notebook Dell',
                'category': 'Notebook',
                'asset_tag': 'PAT-001',
                'serial_number': 'SN123456',
                'location': 'Financeiro',
                'assigned_to': 'Maria',
                'status': 'in_use',
                'notes': 'Equipamento principal',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(InventoryItem.objects.filter(asset_tag='PAT-001').exists())


class WhatsAppConfigViewTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.ti_group = Group.objects.create(name='TI')
        self.ti_user = self.user_model.objects.create_user(
            username='ti_whatsapp',
            password='1234',
        )
        self.ti_user.groups.add(self.ti_group)

    def test_can_add_recipient_with_name_and_phone(self):
        self.client.force_login(self.ti_user)
        response = self.client.post(
            reverse('whatsapp_config'),
            {
                'action': 'add_recipient',
                'recipient-name': 'Joao Silva',
                'recipient-phone_number': '(14) 99882-0134',
            },
        )
        self.assertEqual(response.status_code, 302)
        recipient = WhatsAppRecipient.objects.get(name='Joao Silva')
        self.assertEqual(recipient.phone_number, '5514998820134')

    @override_settings(WAPI_DEFAULT_GROUP_JID='120363421981424263@g.us')
    @patch('tickets.views.send_whatsapp_message')
    def test_group_notification_uses_configured_group_without_form_group_id(self, send_mock):
        self.client.force_login(self.ti_user)
        response = self.client.post(
            reverse('whatsapp_notify_group'),
            {'message': 'Teste de envio'},
        )
        self.assertEqual(response.status_code, 302)
        send_mock.assert_called_once_with('120363421981424263@g.us', 'Teste de envio')
