"""Do estudo ao cliente: um tenant novo, ja com os primeiros passos feitos.

O estudo ja sabe o que a empresa vende, onde, e o que as pessoas da regiao
buscam. Um pedido so para a outra IA devolve o que os primeiros passos de
todo tenant pedem (o Negocio, as sementes do Radar e o Guia editorial, nos
mesmos blocos de `apps/editorial/primeiros_passos.py`, lidos pelos mesmos
leitores). "Criar o tenant" cria o registro (como o cadastro), e uma tarefa
cria o schema e preenche o Negocio, o Radar e o Guia la dentro.

O site do cliente (endereco, chave) fica para ele, em "Site e cadencia".
"""

from __future__ import annotations

import logging

from apps.editorial import primeiros_passos as passos
from apps.estudos import coleta, municipios
from apps.estudos.models import Estudo
from core.resposta_ia import ler_blocos

logger = logging.getLogger("publibot.estudos")

ROTULOS = passos.ROTULOS_DO_NEGOCIO + passos.ROTULOS_DAS_SEMENTES + passos.ROTULOS_DO_GUIA
NO_PEDIDO = 60  # palavras do estudo que vao no pedido

PEDIDO = """\
Voce e um estrategista de negocio, de SEO e de conteudo organico. Esta empresa
vai ganhar um site com artigos (trafego organico, no Google), escritos por um
sistema com revisao humana. Monte a referencia desse sistema a partir do que
eu ja estudei dela.

A EMPRESA (o que me contaram):
{descricao}

ONDE ATENDE: {onde}

O QUE AS PESSOAS DA REGIAO BUSCAM NO GOOGLE (do meu estudo; buscas/mes na area):
{palavras}
{campanha}
Regras:
- OFERTA na lingua de quem COMPRA, sem jargao; DORES: o que a pessoa digitaria
  no Google ANTES de saber que precisa da empresa. Use as buscas acima.
- TEMA amplo o bastante para artigos de topo de funil (o assunto, nao o produto).
- 3 a 5 PILARES de conteudo; 15 a 20 SEMENTES (1 a 4 palavras, como alguem
  digitaria), com * nas 5 para testar primeiro. As buscas com mais volume e as
  duvidas de quem ainda pesquisa sao boas sementes; nada de marca de concorrente.
- O tom (HUMOR, FORMALIDADE, RESPEITO, ENTUSIASMO, de 1 a 5) pelo publico e pelo
  assunto; TERMOS que o site nao deve usar, com a troca e o motivo.
- Nao invente numeros.

Responda EXATAMENTE neste formato, sem nada antes, e termine com a linha FIM:

TEMA: uma frase sobre o assunto do site
PUBLICO: uma frase: quem le e quem compra, e o quanto entende do assunto
OFERTA: uma ou duas frases, como o cliente descreveria o que a empresa vende
DORES:
- de 5 a 10 problemas, cada um como a pessoa diria
FRENTES:
- de 3 a 5 areas vizinhas que o negocio atende ou poderia atender
PILARES:
- nome do pilar: subtema; subtema; subtema
SEMENTES:
- semente
HUMOR: numero de 1 a 5
FORMALIDADE: numero de 1 a 5
RESPEITO: numero de 1 a 5
ENTUSIASMO: numero de 1 a 5
PESSOA: como falar com o leitor (voce, o senhor, a gente...)
REGRA DE OURO: uma frase que resume como o site escreve
SOMOS:
- somos X | nao somos Y
TERMOS:
- termo | troca sugerida | motivo
CONVITE: como o fim do artigo convida para a oferta, sem pressao
EXEMPLOS:
paragrafo 1

paragrafo 2
FIM
"""


def onde_atende(estudo: Estudo) -> str:
    centro = municipios.todos().get(estudo.cidade)
    if centro is None:
        return ""
    return f"{centro.nome} ({centro.sigla}) e regiao" if estudo.raio_km else centro.rotulo


def pedido(estudo: Estudo) -> str:
    dados = coleta.consolidado(estudo)
    ordem = sorted(dados.items(), key=lambda x: -(x[1].get("volume_area") or 0))
    palavras = "\n".join(f"- {p}: {round(i.get('volume_area') or 0)}" for p, i in ordem[:NO_PEDIDO])
    campanha = (
        "Palavras escolhidas para anunciar: " + ", ".join(estudo.campanha) + "\n"
        if estudo.campanha
        else ""
    )
    return PEDIDO.format(
        descricao=estudo.descricao.strip() or "(sem descricao)",
        onde=onde_atende(estudo) or "(nao definido)",
        palavras=palavras or "- (o estudo ainda nao tem buscas)",
        campanha=campanha,
    )


def ler(resposta: str) -> dict:
    """O Negocio, as sementes e o Guia da resposta, prontos para a tarefa (JSON)."""
    blocos = ler_blocos(resposta, ROTULOS)
    return {
        "negocio": passos.ler_negocio(resposta, blocos=blocos),
        "sementes": [s for s, _prioritaria, _pilar in passos.ler_sementes(resposta, blocos=blocos)],
        "guia": passos.ler_guia(resposta, blocos=blocos),
    }


def dados_do_estudo(estudo: Estudo, resposta: str) -> dict:
    """Tudo o que o tenant novo recebe, num dicionario (vai para a fila)."""
    from django.db import connection

    lido = ler(resposta) if resposta.strip() else {"negocio": {}, "sementes": [], "guia": {}}
    centro = municipios.todos().get(estudo.cidade)
    regiao = None
    if centro is not None:
        codigo = municipios.local_da_cidade(centro)
        if codigo:
            regiao = {"codigo": codigo, "nome": f"{centro.nome}, {centro.estado}"}
    return {
        **lido,
        "onde_atende": onde_atende(estudo),
        "regiao": regiao,
        "valor": str(estudo.valor_do_cliente) if estudo.valor_do_cliente else None,
        "cotacao": str(estudo.cotacao_do_dolar),
        "origem": connection.schema_name,
    }


def semear(dados: dict, *, copiar_contas: bool) -> None:
    """Dentro do schema novo: o Negocio, o Radar e o Guia ja preenchidos."""
    from decimal import Decimal

    from django.db import connection
    from django.forms.models import model_to_dict
    from django_tenants.utils import schema_context

    from apps.editorial.forms import PerfilEditorialForm, somos_para_texto, termos_para_texto
    from apps.editorial.models import EditorialProfile, PerfilDoNegocio
    from apps.radar.models import ConfiguracaoDoRadar, ContasExternas

    negocio = dados.get("negocio") or {}
    perfil = PerfilDoNegocio.carregar()
    for campo in ("tema", "publico", "oferta"):
        if negocio.get(campo):
            setattr(
                perfil, campo, negocio[campo][: perfil._meta.get_field(campo).max_length or None]
            )
    if negocio.get("frentes"):
        perfil.frentes = passos.unir(perfil.frentes, negocio["frentes"])
    perfil.onde_atende = (dados.get("onde_atende") or perfil.onde_atende)[:200]
    if dados.get("valor"):
        perfil.valor_da_conversao = Decimal(dados["valor"])
    if dados.get("cotacao"):
        perfil.cotacao_do_dolar = Decimal(dados["cotacao"])
    perfil.save()

    config = ConfiguracaoDoRadar.carregar()
    config.dores = passos.unir(config.dores, negocio.get("dores") or [])
    config.sementes = passos.unir(config.sementes, dados.get("sementes") or [])
    if dados.get("regiao") and not config.regioes:
        config.regioes = [dados["regiao"]]
    config.save()

    guia = dados.get("guia") or {}
    if guia:
        editorial = EditorialProfile.carregar()
        atual = model_to_dict(editorial, fields=PerfilEditorialForm.Meta.fields)
        atual["somos_texto"] = somos_para_texto(editorial.somos)
        atual["termos_texto"] = termos_para_texto(editorial.termos)
        form = PerfilEditorialForm({**atual, **guia}, instance=editorial)
        if form.is_valid():
            form.save()
        else:
            logger.warning("Guia do estudo nao aplicado: %s", form.errors.as_json())

    if copiar_contas and dados.get("origem"):
        from apps.radar.management.commands.copiar_contas_externas import CAMPOS, _bytes

        destino_schema = connection.schema_name
        with schema_context(dados["origem"]):
            origem = ContasExternas.carregar()
            valores = {campo: _bytes(getattr(origem, campo)) for campo, _r, _c in CAMPOS}
        with schema_context(destino_schema):
            contas = ContasExternas.carregar()
            for campo, valor in valores.items():
                if valor and not getattr(contas, campo):
                    setattr(contas, campo, valor)
            contas.save()
