from django.contrib import admin

from .models import AIProvider, AIUsageLog


@admin.register(AIProvider)
class AIProviderAdmin(admin.ModelAdmin):
    list_display = ('name', 'provider_type', 'model_name', 'is_active')
    list_filter = ('provider_type', 'is_active')


@admin.register(AIUsageLog)
class AIUsageLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'user', 'function_name', 'input_tokens', 'output_tokens', 'cost')
    list_filter = ('function_name',)
    date_hierarchy = 'timestamp'
    readonly_fields = ('timestamp',)
