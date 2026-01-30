from django.urls import path

from .views import (
    create_ticket,
    dashboard,
    dashboard_data,
    finished_tickets,
    manage_users,
    profile,
    register,
    related_tickets,
    ticket_detail,
    ti_reports,
    whatsapp_config,
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
    path('perfil/', profile, name='profile'),
    path('relatorios/', ti_reports, name='ti_reports'),
]
