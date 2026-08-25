import re
import codecs
import sys
import os

# Lista de pacotes para EXCLUIR da produção (Linux)
# Adicione aqui qualquer lib que seja exclusiva de Windows ou Development
IGNORE_PACKAGES = [
    "pywin32",
    "pypiwin32",
    "pywintypes",
    "win32-setctime",
    "pywinpty"
]

# Define o nome do arquivo de entrada padrão caso nenhum seja fornecido
DEFAULT_INPUT_FILE = "requirements.txt"

def generate_flexible_requirements():
    """
    Processa um arquivo requirements.txt para criar uma versão 'flexível',
    substituindo ==X.Y.Z por <X+1.0.0, e excluindo pacotes específicos.
    O nome do arquivo de entrada pode ser fornecido como argumento de linha de comando, 
    caso contrário, o padrão é DEFAULT_INPUT_FILE.
    """
    
    # 1. Verifica e Obtém o Parâmetro do Arquivo de Entrada
    if len(sys.argv) < 2:
        input_file = DEFAULT_INPUT_FILE
        print(f"Nenhum arquivo especificado. Usando o padrão: '{input_file}'")
        print("Uso opcional: python generate_flexible_req.py <nome_do_arquivo.txt>")
    else:
        input_file = sys.argv[1]

    if not os.path.exists(input_file):
        print(f"Erro: Arquivo '{input_file}' não encontrado.")
        sys.exit(1)

    # 2. Gera o Nome do Arquivo de Saída
    # Adiciona o sufixo '_flexible' antes da extensão, se houver
    # Exemplo: requirements.txt -> requirements_flexible.txt
    if "." in input_file:
        # Divide no último ponto para separar nome e extensão
        base_name, ext = input_file.rsplit('.', 1)
        output_file = f"{base_name}_flexible.{ext}"
    else:
        # Se não houver extensão, apenas adiciona o sufixo
        output_file = f"{input_file}_flexible"

    # tenta várias codificações comuns do Windows
    encodings = ["utf-8-sig", "utf-16", "latin1", "utf-8"]
    lines = None

    for enc in encodings:
        try:
            print(f"Tentando ler o arquivo com codificação: {enc}")
            with codecs.open(input_file, "r", encoding=enc) as f:
                lines = f.readlines()
            # Se a leitura for bem-sucedida, para o loop
            break
        except UnicodeDecodeError:
            continue
    
    if lines is None:
        raise Exception(f"Não foi possível ler o arquivo '{input_file}'. Tente salvá-lo como UTF-8.")

    new_lines = []
    for line in lines:
        line = line.strip()
        # remove qualquer caractere de controle invisível
        line = re.sub(r"[\u0000-\u001F\u007F-\u009F]", "", line)

        if not line or line.startswith("#"):
            new_lines.append(line)
            continue
        
        # Extrai o nome do pacote para verificar a ignore list
        # Pega tudo antes de ==, >=, <, etc.
        # Usa um lookahead negativo para evitar dividir em ==, <= ou >=.
        pkg_name = re.split(r'[<>]|==|!=|~=', line, 1)[0].strip()
        
        if pkg_name.lower() in [p.lower() for p in IGNORE_PACKAGES]:
            print(f"Ignorando pacote exclusivo de Windows/Dev: {pkg_name}")
            continue

        if "==" in line:
            # Garante que não está pegando a URL (ex: git+https://...)
            if line.startswith("git+") or line.startswith("http"):
                 new_lines.append(line)
                 continue

            parts = line.split("==")
            if len(parts) >= 2:
                pkg = parts[0].strip()
                ver = parts[1].strip()
            else:
                # Linha com '==' malformada, mantém a original
                new_lines.append(line)
                continue

            # Trata casos onde a versão pode ter sufixos estranhos ou ser complexa
            try:
                # Tenta extrair o número principal (major version)
                major_match = re.match(r'^(\d+)\.', ver)
                
                if major_match:
                    major = major_match.group(1)
                    # Se major for numérico, cria a regra flexible (major < next_major)
                    major_int = int(major)
                    new_lines.append(f"{pkg}<{major_int+1}.0.0")
                else:
                    # Se não conseguir ler a versão (ex: git hash, versão não numérica), mantém a linha original
                    new_lines.append(line)
            except Exception as e:
                # Em caso de erro na manipulação da versão, mantém a linha original
                print(f"Aviso: Não foi possível flexibilizar a versão de {pkg}. Mantendo a linha original. Erro: {e}")
                new_lines.append(line)
        else:
            # Mantém linhas que já são flexíveis (ex: >=, ~>) ou não tem versão
            new_lines.append(line)

    # 3. Salva o Arquivo de Saída
    # salva com quebras de linha Unix (LF)
    try:
        with open(output_file, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(new_lines))
        print(f"\nSucesso! '{output_file}' gerado com sucesso a partir de '{input_file}'.")
    except Exception as e:
        print(f"Erro ao escrever o arquivo de saída '{output_file}': {e}")
        sys.exit(1)

if __name__ == "__main__":
    generate_flexible_requirements()