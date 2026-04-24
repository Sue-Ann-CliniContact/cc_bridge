from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_lead_classification'),
    ]

    operations = [
        migrations.AddField(
            model_name='lead',
            name='organization_email',
            field=models.EmailField(blank=True, max_length=255, null=True),
        ),
    ]
