from django.urls import path

from .views import (
    create_ticket,
    dashboard,
    dashboard_data,
    finished_tickets,
    inventory_management,
    manage_users,
    profile,
    register,
    related_tickets,
    ticket_detail,
    ti_reports,
    whatsapp_config,
    whatsapp_send,
    whatsapp_groups,
    whatsapp_send_ti_chamados,
    whatsapp_notify_group,
)

urlpatterns = [
    path('registrar/', register, name='register'),
    path('dashboard/', dashboard, name='dashboard'),
    path('dashboard/data/', dashboard_data, name='dashboard_data'),
    path('chamado/<int:pk>/', ticket_detail, name='ticket_detail'),
    path('chamado/novo/', create_ticket, name='new_ticket'),
    path('finalizados/', finished_tickets, name='finished_tickets'),
    path('chamado/<int:pk>/relacionados/', related_tickets, name='ticket_related'),
    path('whatsapp-configurar/', whatsapp_config, name='whatsapp_config'),
    path('usuarios/', manage_users, name='manage_users'),
    path('inventario/', inventory_management, name='inventory_management'),
    path('perfil/', profile, name='profile'),
    path('relatorios/', ti_reports, name='ti_reports'),
    path('api/whatsapp/send/', whatsapp_send, name='whatsapp_send'),
    path('api/whatsapp/send-ti-chamados/', whatsapp_send_ti_chamados, name='whatsapp_send_ti_chamados'),
    path('api/whatsapp/groups/', whatsapp_groups, name='whatsapp_groups'),
    path('whatsapp-configurar/enviar-grupo/', whatsapp_notify_group, name='whatsapp_notify_group'),
]
