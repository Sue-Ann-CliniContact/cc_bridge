from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_projectlead_campaign'),
    ]

    operations = [
        migrations.AddField(
            model_name='lead',
            name='linkedin_url',
            field=models.URLField(
                blank=True,
                max_length=500,
                help_text='LinkedIn profile URL — captured from Apollo, web search, or manual entry',
            ),
        ),
    ]
