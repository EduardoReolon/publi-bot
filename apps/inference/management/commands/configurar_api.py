"""Cadastra uma API externa: a "outra IA" (Claude, ChatGPT, Gemini...) ou a voz
dos videos (ElevenLabs, OpenAI, Google), com o endereco certo de cada uma.

    manage.py configurar_api anthropic --chave sk-ant-... --testar
    manage.py configurar_api openai --chave sk-... --modelo gpt-5
    manage.py configurar_api gemini --chave AIza...
    manage.py configurar_api elevenlabs --chave ... --modelo eleven_multilingual_v2
    manage.py configurar_api openai_voz --chave sk-...
    manage.py configurar_api google_voz --chave AIza...
    manage.py configurar_api worker_voz          # a placa propria (endereco e segredo do .env)
    manage.py configurar_api worker_animacao
    manage.py configurar_api openai_video --chave sk-...   # Sora
    manage.py configurar_api anthropic --chave ... --tenant clinica_a   # so desse cliente

A chave fica cifrada no banco (como as outras conexoes) e nunca e mostrada de
novo. Rodar de novo atualiza a mesma conexao (pelo nome).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.inference.models import InferenceConnection
from apps.inference.security import guardar_chave

K, W = InferenceConnection.Kind, InferenceConnection.Workload

# servico: (tipo, endereco, carga, modelo padrao, nome)
SERVICOS = {
    "anthropic": (
        K.ANTHROPIC,
        "https://api.anthropic.com",
        W.OUTRA_IA,
        "claude-opus-5-5",
        "Claude (API)",
    ),
    "openai": (K.OPENAI_COMPATIBLE, "https://api.openai.com", W.OUTRA_IA, "gpt-5", "ChatGPT (API)"),
    "gemini": (
        K.OPENAI_COMPATIBLE,
        "https://generativelanguage.googleapis.com/v1beta/openai",
        W.OUTRA_IA,
        "gemini-2.5-pro",
        "Gemini (API)",
    ),
    "openrouter": (
        K.OPENAI_COMPATIBLE,
        "https://openrouter.ai/api",
        W.OUTRA_IA,
        "",
        "OpenRouter (API)",
    ),
    "deepseek": (
        K.OPENAI_COMPATIBLE,
        "https://api.deepseek.com",
        W.OUTRA_IA,
        "deepseek-chat",
        "DeepSeek (API)",
    ),
    "groq": (K.OPENAI_COMPATIBLE, "https://api.groq.com/openai", W.OUTRA_IA, "", "Groq (API)"),
    "elevenlabs": (
        K.ELEVENLABS,
        "https://api.elevenlabs.io",
        W.VOZ,
        "eleven_multilingual_v2",
        "ElevenLabs (voz)",
    ),
    "openai_voz": (
        K.OPENAI_COMPATIBLE,
        "https://api.openai.com",
        W.VOZ,
        "gpt-4o-mini-tts",
        "OpenAI (voz)",
    ),
    "google_voz": (K.GOOGLE_TTS, "https://texttospeech.googleapis.com", W.VOZ, "", "Google (voz)"),
    "openai_video": (
        K.OPENAI_COMPATIBLE,
        "https://api.openai.com",
        W.ANIMACAO,
        "sora-2",
        "Sora (animacao)",
    ),
    # A placa propria (docs/WORKER_VOZ_E_ANIMACAO.md): endereco e segredo vem do
    # .env (os mesmos da imagem), entao --chave e opcional.
    "worker_voz": (K.OPENAI_COMPATIBLE, "", W.VOZ, "kokoro", "Worker (voz)"),
    "worker_animacao": (K.OPENAI_COMPATIBLE, "", W.ANIMACAO, "ltx-video", "Worker (animacao)"),
}
DO_WORKER = {"worker_voz", "worker_animacao"}


class Command(BaseCommand):
    help = "Cadastra a API da outra IA (Claude, ChatGPT, Gemini...) ou da voz (ElevenLabs...)."

    def add_arguments(self, parser):
        parser.add_argument("servico", choices=sorted(SERVICOS))
        parser.add_argument(
            "--chave", default="", help="a chave da API (fica cifrada); no worker, o do .env"
        )
        parser.add_argument("--modelo", default="", help="o modelo (vazio: o padrao do servico)")
        parser.add_argument("--endereco", default="", help="outro endereco (servidor proprio)")
        parser.add_argument("--nome", default="", help="nome da conexao (vazio: o do servico)")
        parser.add_argument("--tenant", default="", help="schema do cliente dono (vazio: todos)")
        parser.add_argument("--testar", action="store_true", help="chama a API para conferir")

    def handle(self, *args, servico, chave, modelo, endereco, nome, tenant, testar, **opcoes):
        from django.conf import settings

        from apps.accounts.models import Tenant

        tipo, padrao_endereco, carga, padrao_modelo, padrao_nome = SERVICOS[servico]
        if servico in DO_WORKER:
            padrao_endereco = settings.IMAGEM_BASE_URL or settings.CONVERSAO_BASE_URL
            chave = chave or settings.IMAGEM_SEGREDO or settings.CONVERSAO_SEGREDO
            if not (endereco or padrao_endereco):
                raise CommandError(
                    "Sem endereco do worker: preencha IMAGEM_BASE_URL no .env ou use --endereco."
                )
        if not chave:
            raise CommandError("Falta --chave (a chave da API).")
        dono = None
        if tenant:
            dono = Tenant.objects.filter(schema_name=tenant).first()
            if dono is None:
                raise CommandError(f"Tenant '{tenant}' nao existe.")
        nome = nome or padrao_nome
        conexao = InferenceConnection.objects.filter(name=nome, tenant=dono).first()
        conexao = conexao or InferenceConnection(name=nome, tenant=dono)
        conexao.kind = tipo
        conexao.base_url = endereco or padrao_endereco
        conexao.workloads = [carga]
        conexao.default_model = modelo or padrao_modelo
        conexao.max_concurrency = 2
        conexao.lease_seconds = 600
        conexao.is_active = True
        conexao.consecutive_failures = 0
        conexao.circuit_open_until = None
        guardar_chave(conexao, chave)
        conexao.save()
        self.stdout.write(
            self.style.SUCCESS(
                f"Conexao {nome!r} pronta: {conexao.base_url}, modelo {conexao.default_model!r}, "
                f"para {carga.label.lower()}."
            )
        )
        if testar:
            self._testar(conexao, carga)

    def _testar(self, conexao, carga) -> None:
        try:
            if carga == W.ANIMACAO:
                from apps.inference.providers.animacao import get_animation_provider

                cliente = get_animation_provider(conexao)
                resposta = cliente._chamar("GET", "/models")
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Respondeu {resposta.status_code}: endereco e chave valem (o teste nao "
                        "gera video, que leva minutos e custa)."
                    )
                )
                return
            if carga == W.VOZ:
                from apps.inference.providers.voz import get_voice_provider

                audio = get_voice_provider(conexao).falar(
                    texto="Teste de voz.", voz="", modelo=conexao.default_model
                )
                self.stdout.write(self.style.SUCCESS(f"Respondeu: {len(audio)} bytes de audio."))
                return
            from apps.inference.providers.base import get_provider

            resposta = get_provider(conexao, timeout=60).chat(
                model=conexao.default_model,
                system="Responda so: ok",
                user="ok?",
                max_tokens=10,
            )
            self.stdout.write(self.style.SUCCESS(f"Respondeu: {resposta.text[:60]!r}"))
        except Exception as exc:  # o teste mostra o motivo, sem esconder
            self.stdout.write(self.style.ERROR(f"Nao respondeu: {exc}"))
