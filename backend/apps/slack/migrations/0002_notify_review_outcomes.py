from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("slack", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="slackintegration",
            name="notify_review_outcomes",
            field=models.BooleanField(default=True),
        ),
    ]
