"""Semente do catalogo: as instituicoes de dados, por nicho.

So instituicoes, com dominios e o caminho para chegar aos dados. As SERIES
entram depois: pelo adaptador (o catalogo da instituicao), pelo acervo (o que
as fontes citam) ou a mao. Os codigos de tabela nao estao aqui de proposito:
saem do proprio catalogo, conferidos, e nao de memoria.

Nichos: "geral", "saude" (clinicas), "ia" (consultoria de IA e tecnologia),
"obras" (construcao e engenharia).
"""

INSTITUICOES = [
    # --- geral ---------------------------------------------------------------
    {
        "sigla": "IBGE",
        "nome": "Instituto Brasileiro de Geografia e Estatistica",
        "site": "https://www.ibge.gov.br/",
        "dominios": ["ibge.gov.br", "sidra.ibge.gov.br", "servicodados.ibge.gov.br"],
        "nichos": ["geral", "saude", "ia", "obras"],
        "adaptador": "ibge",
        "notas": (
            "SIDRA: catalogo em servicodados.ibge.gov.br/api/v3/agregados, valores em "
            "apisidra.ibge.gov.br. Censo, PNAD Continua (inclui modulo TIC), Pesquisa "
            "Nacional de Saude, PINTEC, PAIC (industria da construcao), SINAPI (custo da "
            "construcao, com a Caixa). Recortes: Brasil, UF, municipio."
        ),
    },
    {
        "sigla": "BCB",
        "nome": "Banco Central do Brasil",
        "site": "https://www.bcb.gov.br/",
        "dominios": ["bcb.gov.br", "api.bcb.gov.br"],
        "nichos": ["geral", "obras"],
        "adaptador": "bcb",
        "notas": (
            "SGS (series temporais): api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"
            "?formato=json. IPCA, Selic, credito, e indices de terceiros como o INCC."
        ),
    },
    {
        "sigla": "IPEA",
        "nome": "Instituto de Pesquisa Economica Aplicada (Ipeadata)",
        "site": "http://www.ipeadata.gov.br/",
        "dominios": ["ipeadata.gov.br", "ipea.gov.br"],
        "nichos": ["geral"],
        "notas": "Ipeadata: API OData com milhares de series economicas e sociais.",
    },
    {
        "sigla": "DADOS.GOV.BR",
        "nome": "Portal Brasileiro de Dados Abertos",
        "site": "https://dados.gov.br/",
        "dominios": ["dados.gov.br"],
        "nichos": ["geral"],
        "notas": (
            "Catalogo (CKAN) dos orgaos federais: serve para DESCOBRIR conjuntos; os "
            "arquivos ficam em cada orgao. Qualidade varia."
        ),
    },
    # --- saude (clinicas) ------------------------------------------------------
    {
        "sigla": "DATASUS",
        "nome": "DATASUS / OpenDataSUS — Ministerio da Saude",
        "site": "https://opendatasus.saude.gov.br/",
        "dominios": ["datasus.saude.gov.br", "opendatasus.saude.gov.br", "tabnet.datasus.gov.br"],
        "nichos": ["saude"],
        "notas": (
            "Mortalidade (SIM), nascidos vivos (SINASC), internacoes (SIH), notificacoes "
            "(SINAN), estabelecimentos (CNES). Muito vem em arquivo grande (CSV/DBC) ou no "
            "TabNet; o adaptador provavelmente pre-agrega e guarda."
        ),
    },
    {
        "sigla": "MS",
        "nome": "Ministerio da Saude (Vigitel e boletins)",
        "site": "https://www.gov.br/saude/",
        "dominios": ["gov.br/saude"],
        "nichos": ["saude"],
        "notas": "Vigitel (fatores de risco por capital), boletins epidemiologicos. Em geral PDF.",
    },
    {
        "sigla": "ANS",
        "nome": "Agencia Nacional de Saude Suplementar",
        "site": "https://www.gov.br/ans/",
        "dominios": ["gov.br/ans", "dadosabertos.ans.gov.br", "ans.gov.br"],
        "nichos": ["saude"],
        "notas": "Beneficiarios de planos por regiao e operadora; dados abertos em CSV.",
    },
    {
        "sigla": "INCA",
        "nome": "Instituto Nacional de Cancer",
        "site": "https://www.gov.br/inca/",
        "dominios": ["gov.br/inca", "inca.gov.br"],
        "nichos": ["saude"],
        "notas": "Estimativa de incidencia de cancer por tipo e UF (publicacao periodica).",
    },
    {
        "sigla": "CFM",
        "nome": "Conselho Federal de Medicina (Demografia Medica)",
        "site": "https://portal.cfm.org.br/",
        "dominios": ["portal.cfm.org.br", "cfm.org.br"],
        "nichos": ["saude"],
        "notas": "Demografia Medica: medicos por especialidade e UF. Relatorio periodico.",
    },
    {
        "sigla": "OMS",
        "nome": "Organizacao Mundial da Saude (Global Health Observatory)",
        "site": "https://www.who.int/data/gho",
        "dominios": ["who.int", "ghoapi.azureedge.net"],
        "nichos": ["saude"],
        "notas": "GHO: API OData com indicadores por pais. Bom para comparar Brasil e mundo.",
    },
    # --- IA e tecnologia ---------------------------------------------------------
    {
        "sigla": "CETIC.BR",
        "nome": "Cetic.br / NIC.br (TIC Empresas, TIC Domicilios)",
        "site": "https://cetic.br/",
        "dominios": ["cetic.br", "nic.br"],
        "nichos": ["ia"],
        "notas": (
            "Uso de internet, nuvem e IA por empresas e domicilios no Brasil; tabelas por "
            "porte e regiao (planilhas)."
        ),
    },
    {
        "sigla": "SEBRAE",
        "nome": "Sebrae (pesquisas com pequenos negocios)",
        "site": "https://sebrae.com.br/",
        "dominios": ["sebrae.com.br", "datasebrae.com.br"],
        "nichos": ["ia", "geral"],
        "notas": "DataSebrae: numero de empresas, sobrevivencia, adocao de tecnologia.",
    },
    {
        "sigla": "OECD.AI",
        "nome": "OCDE — Observatorio de Politicas de IA",
        "site": "https://oecd.ai/",
        "dominios": ["oecd.ai", "oecd.org"],
        "nichos": ["ia"],
        "notas": "Investimento, adocao e politicas de IA por pais; OCDE tem API SDMX.",
    },
    {
        "sigla": "AI INDEX",
        "nome": "Stanford AI Index",
        "site": "https://aiindex.stanford.edu/",
        "dominios": ["aiindex.stanford.edu", "hai.stanford.edu"],
        "nichos": ["ia"],
        "notas": "Relatorio anual (PDF e dados publicos): custo de modelos, adocao, investimento.",
    },
    {
        "sigla": "EUROSTAT",
        "nome": "Eurostat",
        "site": "https://ec.europa.eu/eurostat",
        "dominios": ["ec.europa.eu/eurostat"],
        "nichos": ["ia"],
        "notas": "Empresas que usam IA, por pais e porte (API JSON-stat). Comparacao externa.",
    },
    # --- obras e engenharia --------------------------------------------------------
    {
        "sigla": "CAIXA/SINAPI",
        "nome": "SINAPI — Caixa e IBGE (custos e indices da construcao)",
        "site": "https://www.caixa.gov.br/poder-publico/modernizacao-gestao/sinapi/",
        "dominios": ["caixa.gov.br"],
        "nichos": ["obras"],
        "notas": (
            "Custo do m2 por UF (tambem no SIDRA do IBGE) e precos de insumos e composicoes "
            "(planilhas mensais por UF)."
        ),
    },
    {
        "sigla": "CBIC",
        "nome": "Camara Brasileira da Industria da Construcao (CUB e banco de dados)",
        "site": "https://cbic.org.br/",
        "dominios": ["cbic.org.br", "cbicdados.com.br", "cub.org.br"],
        "nichos": ["obras"],
        "notas": "CUB/m2 por estado (Sinduscons), emprego e PIB da construcao.",
    },
    {
        "sigla": "FGV IBRE",
        "nome": "FGV IBRE (INCC, sondagem da construcao)",
        "site": "https://portalibre.fgv.br/",
        "dominios": ["portalibre.fgv.br"],
        "nichos": ["obras"],
        "notas": "INCC-M/DI (o INCC tambem esta no SGS do Banco Central), sondagens.",
    },
    {
        "sigla": "MTE/CAGED",
        "nome": "Novo Caged — Ministerio do Trabalho",
        "site": "https://www.gov.br/trabalho-e-emprego/",
        "dominios": ["gov.br/trabalho-e-emprego", "pdet.mte.gov.br"],
        "nichos": ["obras", "geral"],
        "notas": "Admissoes e desligamentos por setor (construcao) e UF; mensal.",
    },
    {
        "sigla": "ABRAINC",
        "nome": "Abrainc (lancamentos e vendas de imoveis)",
        "site": "https://www.abrainc.org.br/",
        "dominios": ["abrainc.org.br"],
        "nichos": ["obras"],
        "notas": "Indicadores do mercado imobiliario (com a Fipe). Relatorios periodicos.",
    },
]
