from django.contrib import admin

from .models import InventoryItem, Ticket, TicketMessage


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ('title', 'status', 'urgency', 'created_by', 'assigned_to', 'created_at')
    list_filter = ('status', 'urgency')
    search_fields = ('title', 'description', 'created_by__username', 'assigned_to__username')


@admin.register(TicketMessage)
class TicketMessageAdmin(admin.ModelAdmin):
    list_display = ('ticket', 'author', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('ticket__title', 'text', 'author__username')


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'asset_tag', 'assigned_to', 'status', 'location', 'updated_at')
    list_filter = ('status', 'category', 'location')
    search_fields = ('name', 'asset_tag', 'serial_number', 'assigned_to', 'notes')
