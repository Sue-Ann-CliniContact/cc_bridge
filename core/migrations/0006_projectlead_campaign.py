from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_partnerprofile_indication_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='projectlead',
            name='campaign',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name='project_leads',
                to='core.campaign',
            ),
        ),
    ]
