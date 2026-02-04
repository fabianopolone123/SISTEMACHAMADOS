from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer


class TicketConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope['user']
        if not user.is_authenticated:
            await self.close()
            return

        is_ti = await self._is_ti_user(user)
        await self.channel_layer.group_add('tickets', self.channel_name)
        if is_ti:
            await self.channel_layer.group_add('ti', self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self.channel_layer.group_discard('tickets', self.channel_name)
        await self.channel_layer.group_discard('ti', self.channel_name)

    async def ticket_update(self, event):
        await self.send_json(event)

    @staticmethod
    @sync_to_async
    def _is_ti_user(user):
        return user.groups.filter(name='TI').exists() or user.is_staff
