from django.contrib.auth.models import User
from django.db import models


class AIProvider(models.Model):
    PROVIDER_ANTHROPIC = 'anthropic'
    PROVIDER_OPENAI = 'openai'
    PROVIDER_GEMINI = 'gemini'
    PROVIDER_CHOICES = [
        (PROVIDER_ANTHROPIC, 'Anthropic Claude'),
        (PROVIDER_OPENAI, 'OpenAI'),
        (PROVIDER_GEMINI, 'Google Gemini'),
    ]

    name = models.CharField(max_length=100, unique=True, help_text="Friendly name, e.g. 'Claude Sonnet 4.6'")
    provider_type = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    api_key = models.CharField(max_length=255, blank=True, help_text='If blank, falls back to env var per provider')
    model_name = models.CharField(max_length=100, help_text="e.g. 'claude-sonnet-4-6'")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.name} ({self.provider_type})'


class AIUsageLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    provider = models.ForeignKey(AIProvider, on_delete=models.SET_NULL, null=True, blank=True)
    function_name = models.CharField(max_length=100)
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    cost = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    prompt = models.TextField(null=True, blank=True)
    response = models.TextField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f'{self.user or "-"} · {self.function_name} · {self.timestamp:%Y-%m-%d %H:%M}'
