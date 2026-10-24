from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ops", "0004_tarefa_avulsa"),
    ]

    operations = [
        migrations.AlterField(
            model_name="generationjob",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Aguardando"),
                    ("running", "Executando"),
                    ("waiting_capacity", "Aguardando capacidade"),
                    ("waiting_human", "Aguardando revisao humana"),
                    ("done", "Concluido"),
                    ("failed", "Falhou"),
                    ("canceled", "Cancelado"),
                ],
                default="pending",
                max_length=20,
                verbose_name="situacao",
            ),
        ),
    ]
