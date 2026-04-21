from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AIProvider',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text="Friendly name, e.g. 'Claude Sonnet 4.6'", max_length=100, unique=True)),
                ('provider_type', models.CharField(choices=[('anthropic', 'Anthropic Claude'), ('openai', 'OpenAI'), ('gemini', 'Google Gemini')], max_length=20)),
                ('api_key', models.CharField(blank=True, help_text='If blank, falls back to env var per provider', max_length=255)),
                ('model_name', models.CharField(help_text="e.g. 'claude-sonnet-4-6'", max_length=100)),
                ('is_active', models.BooleanField(default=True)),
            ],
        ),
        migrations.CreateModel(
            name='AIUsageLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('function_name', models.CharField(max_length=100)),
                ('input_tokens', models.IntegerField(default=0)),
                ('output_tokens', models.IntegerField(default=0)),
                ('cost', models.DecimalField(decimal_places=6, default=0, max_digits=10)),
                ('prompt', models.TextField(blank=True, null=True)),
                ('response', models.TextField(blank=True, null=True)),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('provider', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, to='ai_manager.aiprovider')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-timestamp']},
        ),
    ]
