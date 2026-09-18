"""Servico HTTP que gera imagem de capa na placa desta maquina.

Irmao do `docling_api.py`, e pela mesma razao: o trabalho que precisa de GPU
roda onde a GPU esta, e a VM da nuvem so faz a requisicao (ADR-0007).

Fala o dialeto de imagem da OpenAI — `POST /v1/images/generations` com
`b64_json` — porque e esse o unico que o PubliBot conhece
(`OpenAICompatibleImageClient`). Assim este servico e intercambiavel com um
provedor pago: trocar e mudar a `base_url` da conexao, sem tocar em codigo.

O Ollama nao gera imagem. Ele serve modelos de texto, e nao ha nele um
endpoint equivalente — e por isso este processo existe em vez de reaproveitar
o que ja esta de pe.

## Dividir uma placa de 8 GB com o Ollama e o Docling

Este e o problema real, e nao ha solucao perfeita. Ha quatro camadas, e cada
uma cobre o que a anterior deixa passar:

1. **A reserva do PubliBot conta por MAQUINA** (`leases.vizinhas_de_hardware`).
   Conexoes que compartilham o host da `base_url` disputam as mesmas vagas,
   entao uma geracao de texto e uma de imagem nao sao despachadas juntas.
   Cobre o caminho normal, e so ele.
2. **Uma geracao por vez aqui dentro.** A segunda recebe 503, como no Docling.
   Cobre quem chamar este servico por fora do PubliBot.
3. **Os pesos ficam na RAM, nao na VRAM** (`enable_model_cpu_offload`). O
   diffusers sobe para a placa so o submodulo em uso: o pico cai de ~7 GB para
   algo em torno de 3,5 GB, que e o que permite o Ollama continuar carregado ao
   lado.
4. **A placa e devolvida depois de um tempo ocioso** (`IMAGEM_OCIOSO_SEGUNDOS`).
   Terminado o lote, um temporizador descarrega o pipeline e esvazia o cache do
   torch. O custo e recarregar no proximo pedido; num volume baixo, vale.

O que nenhuma delas cobre: o Ollama decide sozinho quando carregar e
descarregar modelo, e nao participa de reserva nenhuma. Se ele resolver
carregar um modelo grande no meio de uma geracao, a VRAM pode faltar.

Para isso existe a quinta camada, que e a unica que NAO esconde o problema:
faltando VRAM, o pedido e refeito em CPU e o log diz, em WARNING, que isso
aconteceu — e `/health/` passa a informar `ultimo_dispositivo=cpu`. A geracao
demora minutos em vez de segundos, mas nao falha. Cair para CPU em silencio,
que e o comportamento natural de varias dessas bibliotecas, produziria
exatamente o diagnostico impossivel de "hoje esta lento".

## Modelo

O padrao e o SDXL base, em 1024x1024 — o tamanho que o PubliBot pede. Cabe em
8 GB com a camada 3 acima. Para trocar, `IMAGEM_MODELO` no `.env`; qualquer
modelo de texto-para-imagem que o diffusers carregue com
`AutoPipelineForText2Image` serve.
"""

from __future__ import annotations

import base64
import gc
import hmac
import io
import logging
import os
import threading
import time

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(asctime)s %(message)s")
logger = logging.getLogger("imagem-api")

SEGREDO = os.environ.get("WORKER_SHARED_SECRET", "")

# Qualquer repositorio que o `AutoPipelineForText2Image` carregue. O padrao faz
# 1024x1024 nativo, que e o tamanho que o PubliBot pede.
MODELO = os.environ.get("IMAGEM_MODELO", "stabilityai/stable-diffusion-xl-base-1.0")

# cpu | cuda | auto.
DISPOSITIVO = os.environ.get("IMAGEM_DEVICE", "auto").lower()

# Passos de difusao. Mais passos, mais detalhe e mais tempo — a curva achata
# perto de 30 no SDXL. Modelos "turbo" querem 1 a 4, e guidance 0.
PASSOS = int(os.environ.get("IMAGEM_PASSOS", 25))
GUIDANCE = float(os.environ.get("IMAGEM_GUIDANCE", 7.0))

# O que nao se quer na imagem. Vale para toda geracao, entao serve para o que e
# sempre indesejado numa capa — texto rabiscado, marca d'agua, moldura.
NEGATIVO = os.environ.get(
    "IMAGEM_NEGATIVO",
    "texto, letras, palavras, marca d'agua, logotipo, assinatura, moldura, "
    "baixa qualidade, borrado, deformado",
)

# Segundos sem pedido ate devolver a placa. Zero desliga a descarga automatica
# e mantem o modelo carregado — util numa maquina dedicada a imagem.
OCIOSO_SEGUNDOS = int(os.environ.get("IMAGEM_OCIOSO_SEGUNDOS", 300))

# Teto de imagens por chamada. O PubliBot pede 3 (um lote); o teto existe para
# um cliente distraido nao pedir 50 e segurar a placa por meia hora.
MAXIMO_POR_CHAMADA = int(os.environ.get("IMAGEM_MAXIMO", 4))

# Limite de lado, em pixels. Acima disto o SDXL em 8 GB nao passa nem com
# offload, e o erro de VRAM sai de dentro do torch, sem dizer que o tamanho era
# o problema.
LADO_MAXIMO = int(os.environ.get("IMAGEM_LADO_MAXIMO", 1024))

app = FastAPI(title="PubliBot — servico de imagem", version="1.0.0")

# Uma geracao por vez. Mesmo motivo do Docling: duas ao mesmo tempo estouram a
# VRAM e o resultado nao e erro, e lentidao sem causa visivel.
_uma_por_vez = threading.Semaphore(1)

_pipeline = None
_dispositivo_do_pipeline = ""
_ultimo_dispositivo = ""
_trava = threading.Lock()
_temporizador: threading.Timer | None = None


# ---------------------------------------------------------------------------
# Dispositivo e pipeline
# ---------------------------------------------------------------------------
def _resolver_dispositivo() -> str:
    """cpu ou cuda, ja resolvido — nunca "auto" para dentro do codigo."""
    if DISPOSITIVO == "cpu":
        return "cpu"

    try:
        import torch
    except ImportError:
        return "cpu"

    tem_placa = torch.cuda.is_available()

    if DISPOSITIVO == "cuda" and not tem_placa:
        # Pediram a placa e ela nao esta la. Avisar aqui e barato; descobrir
        # pelo tempo de geracao custa uma tarde.
        logger.warning(
            "IMAGEM_DEVICE=cuda, mas o torch nao ve nenhuma GPU. "
            "Vou gerar em CPU, o que leva minutos por imagem. "
            "Confira o driver e se o torch foi instalado com suporte a CUDA."
        )
        return "cpu"

    return "cuda" if tem_placa else "cpu"


def _montar_pipeline(dispositivo: str):
    """Carrega o modelo, com os pesos fora da VRAM quando ha placa."""
    import torch
    from diffusers import AutoPipelineForText2Image

    meia = dispositivo == "cuda"
    argumentos = {
        "torch_dtype": torch.float16 if meia else torch.float32,
        "use_safetensors": True,
    }
    if meia:
        # Metade dos bytes para baixar e para guardar. So existe em CUDA: em
        # CPU o float16 e emulado e fica mais lento que o float32.
        argumentos["variant"] = "fp16"

    logger.info("Carregando %s em %s...", MODELO, dispositivo)
    inicio = time.perf_counter()
    try:
        pipe = AutoPipelineForText2Image.from_pretrained(MODELO, **argumentos)
    except Exception:
        if not meia:
            raise
        # Nem todo repositorio publica a variante fp16. Sem esta segunda
        # tentativa, trocar de modelo no `.env` falharia com um erro sobre
        # arquivo ausente que nao menciona `variant`.
        logger.warning("%s nao tem variante fp16; carregando os pesos completos.", MODELO)
        argumentos.pop("variant")
        pipe = AutoPipelineForText2Image.from_pretrained(MODELO, **argumentos)

    if dispositivo == "cuda":
        # `enable_model_cpu_offload`, e NAO `.to("cuda")`. Este e o ponto do
        # arquivo inteiro: os pesos ficam na RAM e cada submodulo sobe para a
        # placa na hora de rodar. O pico de VRAM cai para cerca de metade, que
        # e o que deixa o Ollama continuar carregado ao lado.
        #
        # Chamar `.to("cuda")` depois disto desfaz o arranjo em silencio.
        pipe.enable_model_cpu_offload()
        pipe.enable_vae_slicing()
    else:
        pipe.to("cpu")

    pipe.set_progress_bar_config(disable=True)
    logger.info("Modelo pronto em %.1fs.", time.perf_counter() - inicio)
    return pipe


def _obter_pipeline(dispositivo: str):
    """Carga preguicosa, e recarga quando o dispositivo muda."""
    global _pipeline, _dispositivo_do_pipeline

    with _trava:
        if _pipeline is not None and _dispositivo_do_pipeline != dispositivo:
            _descarregar_sem_trava()
        if _pipeline is None:
            _pipeline = _montar_pipeline(dispositivo)
            _dispositivo_do_pipeline = dispositivo
        return _pipeline


def _descarregar_sem_trava() -> None:
    """Solta o modelo e devolve a VRAM. Quem chama ja segura `_trava`."""
    global _pipeline, _dispositivo_do_pipeline

    if _pipeline is None:
        return

    logger.info("Descarregando o modelo e devolvendo a placa.")
    _pipeline = None
    _dispositivo_do_pipeline = ""
    gc.collect()

    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def _agendar_descarga() -> None:
    """Devolve a placa depois de um tempo sem pedido.

    Sem isto, o modelo fica residente para sempre e o Ollama disputa o que
    sobra pelo resto do dia. Com isto, quem paga e o proximo pedido depois da
    pausa, que espera o carregamento — e num volume baixo essa troca compensa.
    """
    global _temporizador

    if OCIOSO_SEGUNDOS <= 0:
        return

    if _temporizador is not None:
        _temporizador.cancel()

    def descarregar():
        with _trava:
            _descarregar_sem_trava()

    _temporizador = threading.Timer(OCIOSO_SEGUNDOS, descarregar)
    # Daemon: um temporizador pendente nao pode segurar o desligamento do
    # servico por cinco minutos.
    _temporizador.daemon = True
    _temporizador.start()


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _conferir_segredo(autorizacao: str | None, cabecalho_proprio: str | None) -> None:
    """Aceita `Authorization: Bearer` e `X-Worker-Secret`.

    O primeiro e o que o `OpenAICompatibleImageClient` envia, porque e o que o
    dialeto da OpenAI manda. O segundo e o do `docling_api`, e existe aqui para
    um `curl` de diagnostico ser igual nos dois servicos.

    `compare_digest` e nao `!=`: a comparacao natural e curto-circuitada byte a
    byte, e o tempo de resposta revela quantos bytes iniciais estao certos.
    """
    if not SEGREDO:
        raise HTTPException(500, "WORKER_SHARED_SECRET nao configurado.")

    recebido = cabecalho_proprio or ""
    if not recebido and autorizacao and autorizacao.lower().startswith("bearer "):
        recebido = autorizacao[7:].strip()

    if not recebido or not hmac.compare_digest(recebido, SEGREDO):
        raise HTTPException(401, "Credencial invalida.")


def _medidas(tamanho: str) -> tuple[int, int]:
    """ "1024x1024" -> (1024, 1024), recusando o que o modelo nao aceita."""
    try:
        largura, _, altura = tamanho.lower().partition("x")
        largura, altura = int(largura), int(altura)
    except ValueError as exc:
        raise HTTPException(422, f"size invalido: {tamanho!r}. Use algo como 1024x1024.") from exc

    for medida in (largura, altura):
        if medida <= 0 or medida % 8:
            # Os modelos de difusao trabalham num espaco latente 8x menor. Um
            # lado que nao e multiplo de 8 nao falha: ele e arredondado por
            # dentro, e a imagem volta com tamanho diferente do pedido.
            raise HTTPException(422, f"size {tamanho!r}: cada lado precisa ser multiplo de 8.")
        if medida > LADO_MAXIMO:
            raise HTTPException(
                422,
                f"size {tamanho!r} passa de {LADO_MAXIMO}px por lado, que e o que "
                f"esta placa comporta. Ajuste IMAGEM_LADO_MAXIMO se ela comportar mais.",
            )

    return largura, altura


class PedidoDeImagem(BaseModel):
    """O corpo do `POST /v1/images/generations`, no dialeto da OpenAI."""

    prompt: str
    model: str = ""
    n: int = 1
    size: str = "1024x1024"
    # Aceito e ignorado: este servico so devolve base64. Um link temporario
    # expiraria antes da publicacao e viraria imagem quebrada no site.
    response_format: str = "b64_json"
    seed: int | None = Field(default=None)


@app.get("/health/")
async def health():
    return {
        "status": "ok",
        "service": "imagem-api",
        "busy": not _uma_por_vez._value,
        "model": MODELO,
        # O configurado e o que de fato rodou por ultimo. Os dois, porque a
        # diferenca entre eles e exatamente o caso que interessa: pediram
        # `cuda` e a VRAM faltou.
        "device": DISPOSITIVO,
        "ultimo_dispositivo": _ultimo_dispositivo or "(nada gerado ainda)",
        "carregado": _pipeline is not None,
        "steps": PASSOS,
    }


@app.get("/v1/models")
async def modelos():
    """O `health()` do cliente do PubliBot bate aqui."""
    return {"object": "list", "data": [{"id": MODELO, "object": "model"}]}


@app.post("/v1/images/generations")
async def gerar(
    pedido: PedidoDeImagem,
    authorization: str | None = Header(default=None),
    x_worker_secret: str | None = Header(default=None),
):
    global _ultimo_dispositivo

    _conferir_segredo(authorization, x_worker_secret)

    if not pedido.prompt.strip():
        raise HTTPException(422, "prompt vazio.")

    quantas = max(1, min(pedido.n, MAXIMO_POR_CHAMADA))
    largura, altura = _medidas(pedido.size)

    # Recusa em vez de enfileirar, igual ao Docling: o PubliBot ja tem fila e
    # sabe tentar de novo. Uma segunda fila aqui seria invisivel para ele.
    if not _uma_por_vez.acquire(blocking=False):
        return JSONResponse(
            {"error": {"code": "busy", "message": "Ja ha uma geracao em curso."}},
            status_code=503,
            headers={"Retry-After": "60"},
        )

    inicio = time.perf_counter()
    try:
        dispositivo = _resolver_dispositivo()
        try:
            imagens = _gerar_imagens(dispositivo, pedido, quantas, largura, altura)
        except Exception as exc:
            if not _e_falta_de_vram(exc) or dispositivo == "cpu":
                raise
            # A quinta camada descrita no cabecalho. Em WARNING e nao em
            # DEBUG: uma geracao que passou a levar minutos precisa ter deixado
            # rastro, ou o sintoma vira "hoje esta lento" sem causa.
            logger.warning(
                "VRAM insuficiente para gerar em CUDA (%s). Refazendo em CPU — "
                "vai levar minutos. Provavel causa: outro modelo ocupando a "
                "placa (Ollama, Docling).",
                exc,
            )
            with _trava:
                _descarregar_sem_trava()
            dispositivo = "cpu"
            imagens = _gerar_imagens(dispositivo, pedido, quantas, largura, altura)

        _ultimo_dispositivo = dispositivo
        duracao = int((time.perf_counter() - inicio) * 1000)
        logger.info(
            "%s imagem(ns) %sx%s em %sms (%s).", len(imagens), largura, altura, duracao, dispositivo
        )

        return {
            "created": int(time.time()),
            "data": [
                {"b64_json": base64.b64encode(png).decode("ascii"), "revised_prompt": pedido.prompt}
                for png in imagens
            ],
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Falha ao gerar imagem")
        raise HTTPException(500, f"Falha na geracao: {exc}") from exc
    finally:
        _uma_por_vez.release()
        _agendar_descarga()


def _gerar_imagens(
    dispositivo: str, pedido: PedidoDeImagem, quantas: int, largura: int, altura: int
) -> list[bytes]:
    """As imagens em PNG. O PubliBot converte para WebP do lado dele."""
    import torch

    pipe = _obter_pipeline(dispositivo)

    gerador = None
    if pedido.seed is not None:
        # Semente fixa serve para reproduzir uma imagem exata — util ao ajustar
        # o prompt. Sem ela cada opcao do lote e diferente, que e o ponto de um
        # lote.
        gerador = torch.Generator(device="cpu").manual_seed(pedido.seed)

    saida = pipe(
        prompt=pedido.prompt,
        negative_prompt=NEGATIVO or None,
        num_images_per_prompt=quantas,
        num_inference_steps=PASSOS,
        guidance_scale=GUIDANCE,
        width=largura,
        height=altura,
        generator=gerador,
    )

    bytes_das_imagens = []
    for imagem in saida.images:
        memoria = io.BytesIO()
        imagem.save(memoria, format="PNG")
        bytes_das_imagens.append(memoria.getvalue())
    return bytes_das_imagens


def _e_falta_de_vram(exc: Exception) -> bool:
    """Distingue "faltou memoria na placa" de um defeito de verdade.

    Pelo texto, e nao so pelo tipo: o `torch.cuda.OutOfMemoryError` cobre o
    caso direto, mas a mesma falta de memoria chega tambem como `RuntimeError`
    vinda do cuBLAS ou do cuDNN, com a mensagem dentro.
    """
    try:
        import torch

        if isinstance(exc, torch.cuda.OutOfMemoryError):
            return True
    except (ImportError, AttributeError):
        pass

    texto = str(exc).lower()
    return "out of memory" in texto or "cuda error" in texto
