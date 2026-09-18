"""Mostra (e solta) as reservas de capacidade ativas.

Existe porque uma reserva presa e invisivel e cara. Ela dura
`lease_seconds` — uma hora, por padrao, para caber a inferencia mais longa —
e enquanto existe nenhum trabalho novo entra naquela maquina. O sintoma na
tela e:

    todas as conexoes de inferencia estao ocupadas

que e verdade e nao ajuda: nao diz QUAL conexao, nem desde quando, nem se
alguem ainda esta do outro lado. Sem este comando, a unica saida era esperar
a expiracao ou abrir o banco na mao.

    manage.py reservas
    manage.py reservas --liberar         solta as que estao ativas
    manage.py reservas --liberar --tudo  inclusive as recentes

Rode no schema `public`: as conexoes e as reservas sao compartilhadas.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.inference.models import InferenceLease

# Uma reserva recem-criada quase sempre e trabalho de verdade em curso, e
# solta-la faria duas tarefas disputarem a mesma placa — o problema que a
# reserva existe para evitar. Acima disto, a suspeita se inverte.
MINUTOS_PARA_SUSPEITAR = 15


class Command(BaseCommand):
    help = "Lista as reservas de capacidade ativas, e opcionalmente as solta."

    def add_arguments(self, parser):
        parser.add_argument(
            "--liberar",
            action="store_true",
            help="Solta as reservas suspeitas (mais de 15 minutos sem terminar).",
        )
        parser.add_argument(
            "--tudo",
            action="store_true",
            help=(
                "Com --liberar, solta TAMBEM as recentes. Use so quando tiver "
                "certeza de que nenhuma inferencia esta em curso: soltar uma "
                "reserva viva poe duas tarefas na mesma placa."
            ),
        )

    def handle(self, *args, **options):
        agora = timezone.now()
        ativas = list(
            InferenceLease.objects.filter(released_at__isnull=True, expires_at__gt=agora)
            .select_related("connection")
            .order_by("acquired_at")
        )

        if not ativas:
            self.stdout.write("Nenhuma reserva ativa. A capacidade esta livre.")
            return

        self.stdout.write(f"{len(ativas)} reserva(s) ativa(s):\n")
        for lease in ativas:
            idade = agora - lease.acquired_at
            minutos = int(idade.total_seconds() // 60)
            marca = "  <- suspeita" if minutos >= MINUTOS_PARA_SUSPEITAR else ""
            self.stdout.write(
                f"  {lease.connection.name!r} ({lease.connection.maquina})"
                f"  modelo={lease.model_name or '-'}"
                f"  dono={lease.owner_key}"
                f"  ha {minutos} min"
                f"  expira {lease.expires_at:%H:%M}{marca}"
            )

        if not options["liberar"]:
            self.stdout.write(
                "\nSe alguma esta presa (o processo que a tomou morreu), solte com:"
                "\n    manage.py reservas --liberar"
            )
            return

        if options["tudo"]:
            alvo = ativas
        else:
            alvo = [
                lease
                for lease in ativas
                if (agora - lease.acquired_at).total_seconds() >= MINUTOS_PARA_SUSPEITAR * 60
            ]

        if not alvo:
            self.stdout.write(
                f"\nNenhuma passa de {MINUTOS_PARA_SUSPEITAR} min: provavelmente sao "
                f"trabalho em curso.\nPara soltar assim mesmo: --liberar --tudo"
            )
            return

        soltas = InferenceLease.objects.filter(
            pk__in=[lease.pk for lease in alvo], released_at__isnull=True
        ).update(released_at=agora)

        self.stdout.write(self.style.SUCCESS(f"\n{soltas} reserva(s) solta(s)."))
        self.stdout.write(
            "Os trabalhos parados voltam sozinhos: o varredor do beat os retoma "
            "em ate cinco minutos."
        )
