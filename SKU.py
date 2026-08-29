from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError
import os
import re
import time
import requests

#Preciso dos ultimos ajustes. Não me falta muito; só organizar pra mais legibilidade e definir a loc do sku. O resto tá ok.



# ---------------------------------------------------------------------------
# CONFIGURACAO
# ---------------------------------------------------------------------------


PASTA_BASE = os.path.dirname(os.path.abspath(__file__))
# CAMINHO_NAVEGADORES = os.path.join(PASTA_BASE, "playwright_dependencies")
# os.environ["PLAYWRIGHT_BROWSERS_PATH"] = CAMINHO_NAVEGADORES

URL_SISTEMA = "https://balcao2-front.dpsp.io"
ARQUIVO_EANS = "ean_to_sku.txt"
ARQUIVO_ERROS = "sku_erros.txt"
ARQUIVO_RESULTADO = "ean_sku_resultado.txt"

NTFY_TOPIC = "SISTEMA_SKU"   
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

TIMEOUT_BUSCA_MS = 3000  # tempo maximo esperando resultado da busca

# ---------------------------------------------------------------------------
# LEITURA DA LISTA DE EANS
# ---------------------------------------------------------------------------

def carregar_eans(caminho: str) -> list[str]:
    print(f"[SETUP] Carregando lista de EANs de: {caminho}")
    with open(caminho, "r", encoding="utf-8") as arquivo:
        eans = [linha.strip() for linha in arquivo if linha.strip()]
    print(f"[SETUP] {len(eans)} EAN(s) carregado(s).")
    return eans

# ---------------------------------------------------------------------------
# BUSCA DE UM PRODUTO E VERIFICACAO DE ERRO
# ---------------------------------------------------------------------------

def buscar_produto(page: Page, ean: str) -> str:
    print(f"[BUSCA] Buscando EAN {ean}...")
    campo_busca = page.locator("#productDetailsSearchbar")
    campo_busca.fill(ean)
    campo_busca.press("Enter")

    erro_locator = page.locator('[data-testid="productDetailsSearchbar-error"]')   #locator do erro de produto nao encontrado

    try:
        erro_locator.wait_for(state="visible", timeout=TIMEOUT_BUSCA_MS)
        print(f"[BUSCA] EAN {ean} -> produto NAO encontrado.")
        return "Produto nao encontrado"

    except PlaywrightTimeoutError:
        print(f"[BUSCA] EAN {ean} -> produto encontrado (erro nao apareceu).")
        return "produto encontrado"

    # OBS: esses returns repassam ao restante do codigo o que aconteceu ao buscar um produto


# ---------------------------------------------------------------------------
# NOTIFICACOES (ntfy) -> Mi Band
# ---------------------------------------------------------------------------

def notificar(mensagem: str, titulo: str = "SKU") -> None:
    """Envia notificacao push via ntfy. Nao trava o script se falhar."""
    print(f"[NTFY] Enviando notificacao: {titulo} -> {mensagem[:50]}...")
    try:
        requests.post(
            NTFY_URL,
            data=mensagem.encode("utf-8"),
            headers={"Title": titulo},
            timeout=5,
        )
        print("[NTFY] Notificacao enviada com sucesso.")
    except requests.RequestException as erro:
        print(f"[aviso] Falha ao notificar via ntfy: {erro}")

# ---------------------------------------------------------------------------
# EXTRAÇÃO SKU 
# ---------------------------------------------------------------------------

def extrair_sku(page: Page) -> str:
    """
    Extrai o SKU da página do produto.
    Retorna o SKU como string, ou levanta ValueError se não encontrado.
    """
    try:  
        page.wait_for_url(re.compile(r"sku=\d+"), timeout=TIMEOUT_BUSCA_MS)   #aguarda a url carregar pra depois extrair o sku
        sku = re.search(r"sku=(\d+)", page.url).group(1) #armazena o codigo sku na variavel sku
        return sku

    except PlaywrightTimeoutError:
        raise ValueError("SKU não encontrado na URL. (timeout)")

    except AttributeError:
        raise ValueError("SKU não encontrado na URL (padrão não bateu).")

# ---------------------------------------------------------------------------
# FLUXO PRINCIPAL
# ---------------------------------------------------------------------------

def processar_ean(page: Page, ean: str, eans_com_erro: list[str], resultados: list[str]) -> str:
    print(f"\n[PROCESSAR] Iniciando conversão do EAN: {ean}...")
    resultado_busca = buscar_produto(page, ean)

    if resultado_busca == "Produto nao encontrado":
        print(f"[ERRO] EAN {ean} - produto nao encontrado")
        eans_com_erro.append(ean)
        return "erro"

    if resultado_busca == "erro_inesperado":
        print(f"[ERRO] EAN {ean} - timeout/erro inesperado")
        eans_com_erro.append(ean)
        return "erro"

    try:
        sku = extrair_sku(page)
        print(f"[OK] EAN {ean} - SKU {sku} extraido com sucesso.")
        resultados.append(f"{sku}") 
        return "sku"

    except ValueError as erro:
        print(f"[ERRO] EAN {ean} - nao foi possivel ler o sku: {erro}")
        eans_com_erro.append(ean)
        return "erro"


def salvar_erros(caminho: str, eans_com_erro: list[str]) -> None:
    if not eans_com_erro:
        print("[SALVAR] Nenhum erro para salvar.")
        return
    print(f"[SALVAR] Gravando {len(eans_com_erro)} EAN(s) com erro em {caminho}")
    with open(caminho, "w", encoding="utf-8") as arquivo:
        arquivo.write("\n".join(eans_com_erro))


def salvar_resultado(caminho: str, resultados: list[str]) -> None:
    if not resultados:
        print("[SALVAR] Nenhum resultado para salvar.")
        return
    print(f"[SALVAR] Gravando {len(resultados)} resultado(s) em {caminho}")
    with open(caminho, "w", encoding="utf-8") as arquivo:
        arquivo.write("\n".join(resultados))

# ---------------------------------------------------------------------------
# LOGIN MANUAL
# ---------------------------------------------------------------------------

def aguardar_login_manual(page: Page) -> None:
    """Abre o sistema e pausa ate voce logar manualmente."""
    print(f"[LOGIN] Abrindo {URL_SISTEMA}...")
    page.goto(URL_SISTEMA)
    input(">> Faca login manualmente na janela do navegador. Depois, pressione ENTER aqui para continuar...")
    print("[LOGIN] Login confirmado, iniciando processamento.")


def main() -> None:
    print("=== INICIO DO SCRIPT SKU ===")
    eans = carregar_eans(ARQUIVO_EANS) 
    if not eans:
        print("Nenhum EAN encontrado no arquivo. Encerrando.")
        return

    print(f"{len(eans)} codigos carregados.")

    total = len(eans)
    contagem = {"sku": 0, "ok": 0, "erro": 0}
    eans_com_erro: list[str] = []
    resultados: list[str] = []

    with sync_playwright() as pw:
        print("[NAVEGADOR] Iniciando Chromium...")
        navegador = pw.chromium.launch(headless=False)
        page = navegador.new_page()

        aguardar_login_manual(page)

        for indice, ean in enumerate(eans, start=1):
            print(f"\n--- Processando {indice}/{total} ---")
            categoria = processar_ean(page, ean, eans_com_erro, resultados)
            contagem[categoria] += 1
            time.sleep(1)  # pequena pausa entre buscas

        print("[NAVEGADOR] Fechando navegador...")
        navegador.close()               

    salvar_erros(ARQUIVO_ERROS, eans_com_erro)
    salvar_resultado(ARQUIVO_RESULTADO, resultados)

    resumo = (
        f"Total processado: {total}\n"
        f"Foram ao Falteiro: {contagem['sku']}\n"
        f"Nao precisaram: {contagem['ok']}\n"
        f"Com erro: {contagem['erro']}"
    )
    print("\n" + resumo)

    if eans_com_erro:
        notificar(
            f"Senhor, {contagem['erro']} produto(s) deram erro na busca. "
            f"Verifique o arquivo {ARQUIVO_ERROS} para tentar novamente."
        )
    else:
        notificar(f"Script SKU concluido sem erros.\n{resumo}")

    print("=== FIM DO SCRIPT SKU ===")


if __name__ == "__main__":
    main()



    # O QUE EXTRAI DA PAGINA NA FIRMA:

    #get_by_text("SKU: 701173EAN:") #opcao para botao sku, tirei com o playwright codegen    ### DESCARTADO
    #<span class="sc-lbldtA bHvpNG">SKU: 327875</span> #outra opcao, mas agr usei o inspecionar  #EXTRA: tirei print tb
    #https://balcao2-front.dpsp.io/product/Pristiq-Succinato-De-Desvenlafaxina-100mg-28-Comprimidos-Revestidos?sku=327875 # e mais outra opção