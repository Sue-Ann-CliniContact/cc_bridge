from django.contrib import admin

from .models import ClientProfile


@admin.register(ClientProfile)
class ClientProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'monday_id', 'updated_at')
    search_fields = ('user__username', 'user__email', 'monday_id')
