"""Mede quanto tempo o Docling leva NESTA maquina, e mostra o que ele produziu.

Existe para uma decisao concreta: CPU ou GPU. A resposta nao e a mesma em toda
maquina, e chutar sai caro nos dois sentidos — comprar placa sem precisar, ou
descobrir depois de meses que cada documento prende o worker por meia hora.

    python medir.py artigo.pdf              # como o servico esta configurado
    python medir.py artigo.pdf --cpu        # forcando CPU
    python medir.py artigo.pdf --cpu --threads 1
    python medir.py artigo.pdf --cuda

Rode na maquina que vai HOSPEDAR o servico, nunca na VM da nuvem: e ela que
converte. Com `--threads 1` da para estimar o pior caso de uma maquina de um
nucleo so.

Os dois tempos sao separados de proposito. A **carga** acontece uma vez por
processo e o servico a paga so na primeira conversao depois de subir; a
**conversao** e o que se paga por documento, e e o unico numero que importa
para decidir.

Primeira execucao baixa os modelos de layout do HuggingFace (algumas centenas
de MB). Numa rede que bloqueie `huggingface.co` isto falha, e a mensagem fala
de proxy, nao de Docling.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Mede a conversao do Docling nesta maquina.")
    parser.add_argument("pdf", help="Caminho do PDF a converter.")
    parser.add_argument("--cpu", action="store_true", help="Forca CPU.")
    parser.add_argument("--cuda", action="store_true", help="Forca a placa.")
    parser.add_argument(
        "--threads",
        type=int,
        default=0,
        help="Threads na CPU. 1 estima uma maquina de um nucleo so. 0 deixa o Docling decidir.",
    )
    parser.add_argument("--ocr", action="store_true", help="Liga o OCR (PDF digitalizado).")
    parser.add_argument(
        "--salvar", help="Grava o Markdown neste arquivo, para comparar com outra configuracao."
    )
    args = parser.parse_args()

    caminho = Path(args.pdf)
    if not caminho.is_file():
        print(f"ERRO: {caminho} nao existe.", file=sys.stderr)
        return 1

    if args.cpu and args.cuda:
        print("ERRO: escolha --cpu ou --cuda, nao os dois.", file=sys.stderr)
        return 1

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        AcceleratorDevice,
        AcceleratorOptions,
        PdfPipelineOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    if args.cpu:
        dispositivo = AcceleratorDevice.CPU
    elif args.cuda:
        dispositivo = AcceleratorDevice.CUDA
    else:
        dispositivo = AcceleratorDevice.AUTO

    acelerador = AcceleratorOptions(device=dispositivo)
    if args.threads:
        acelerador.num_threads = args.threads

    opcoes = PdfPipelineOptions()
    opcoes.accelerator_options = acelerador
    opcoes.do_ocr = args.ocr
    opcoes.do_table_structure = True

    tamanho = caminho.stat().st_size

    inicio = time.perf_counter()
    conversor = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opcoes)}
    )
    carga = time.perf_counter() - inicio

    inicio = time.perf_counter()
    resultado = conversor.convert(str(caminho))
    conversao = time.perf_counter() - inicio

    markdown = resultado.document.export_to_markdown()
    paginas = len(resultado.document.pages) or 1

    print()
    print(f"Arquivo:     {caminho.name}  ({tamanho / 1024:.0f} KB, {paginas} pagina(s))")
    print(f"Dispositivo: {dispositivo.value}  threads={args.threads or 'auto'}  ocr={args.ocr}")
    print(f"Carga:       {carga:6.1f}s   (uma vez por processo, nao por documento)")
    print(f"Conversao:   {conversao:6.1f}s   ({conversao / paginas:.1f}s por pagina)")
    print()
    print("Para decidir: o servico converte um documento por vez. O numero que")
    print("importa e a CONVERSAO, e a pergunta e se esse tempo cabe no seu ritmo")
    print("de envio de documentos — nao se ele e 'rapido'.")
    print()

    # Sinais de que a analise de layout funcionou. Sao o que distingue este
    # caminho do extrator local, e olhar o tempo sem olhar isto seria medir a
    # coisa errada.
    cabecalhos = [linha for linha in markdown.splitlines() if linha.startswith("#")]
    tabelas = markdown.count("\n|")
    print(f"Cabecalhos de secao reconhecidos: {len(cabecalhos)}")
    print(f"Linhas de tabela em Markdown:     {tabelas}")
    if not cabecalhos:
        print("  AVISO: nenhum cabecalho. Confira se o PDF tem camada de texto;")
        print("  se for digitalizado, rode de novo com --ocr.")
    print()

    if args.salvar:
        Path(args.salvar).write_text(markdown, encoding="utf-8")
        print(f"Markdown gravado em {args.salvar}")
    else:
        print("--- Markdown (primeiras 60 linhas) ---")
        print("\n".join(markdown.splitlines()[:60]))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
