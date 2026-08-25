-- Executado UMA VEZ, na primeira inicializacao do container.
--
-- Com schema por tenant, uma extensao instalada no schema `public` nao fica
-- visivel de dentro de um schema de tenant da forma que o Django espera na
-- hora de rodar migrations. A solucao padrao e um schema dedicado, incluido no
-- search_path de toda conexao via PG_EXTRA_SEARCH_PATHS = ["extensions"].
--
-- Sem isto, o primeiro tenant funciona (o search_path ainda alcanca public) e
-- o segundo falha com: type "vector" does not exist.

CREATE SCHEMA IF NOT EXISTS extensions;

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;

-- Busca textual sem acento, para a camada BM25 da busca hibrida.
CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA extensions;

-- Deixa o schema visivel por padrao para novas conexoes deste banco.
-- Precisa de SQL dinamico: ALTER DATABASE nao aceita uma expressao no lugar
-- do nome, e o nome do banco vem da variavel de ambiente POSTGRES_DB.
DO $$
BEGIN
    EXECUTE format(
        'ALTER DATABASE %I SET search_path TO "$user", public, extensions',
        current_database()
    );
END
$$;
