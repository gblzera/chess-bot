import logging
import json
import os
import httpx
import threading
import asyncio  # <-- MUDANÇA: Importar a biblioteca asyncio
from flask import Flask
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackContext

# --- CONFIGURAÇÃO INICIAL ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("Erro: O TELEGRAM_TOKEN não foi encontrado no arquivo .env")

# Lógica para usar o disco persistente do Render
DATA_DIR = os.environ.get('RENDER_DATA_DIR', '.')
NOME_ARQUIVO_DADOS = os.path.join(DATA_DIR, "data_bot.json")

dados = {}
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Criação do servidor Flask para responder aos Health Checks do Render
server = Flask(__name__)

@server.route('/')
def health_check():
    """Endpoint que o Render usará para verificar se o serviço está vivo."""
    return "Bot de Xadrez está vivo!", 200


# --- FUNÇÕES DE PERSISTÊNCIA ---
def salvar_dados():
    with open(NOME_ARQUIVO_DADOS, 'w') as f:
        dados_para_salvar = dados.copy()
        dados_para_salvar['partidas_notificadas'] = list(dados.get('partidas_notificadas', set()))
        json.dump(dados_para_salvar, f, indent=4)
    logger.info("Dados salvos com sucesso!")

def carregar_dados():
    global dados
    if os.path.exists(NOME_ARQUIVO_DADOS):
        with open(NOME_ARQUIVO_DADOS, 'r') as f:
            dados = json.load(f)
            dados['partidas_notificadas'] = set(dados.get('partidas_notificadas', []))
        logger.info(f"Dados carregados do arquivo {NOME_ARQUIVO_DADOS}.")
    else:
        dados = {
            "gms_a_monitorar": ["magnuscarlsen", "hikaru"],
            "partidas_notificadas": set(),
            "ritmos_permitidos": [],
            "chat_id": None
        }
        salvar_dados()
        logger.info(f"Nenhum arquivo de dados encontrado. Um novo ('{NOME_ARQUIVO_DADOS}') foi criado.")


# --- LÓGICA DE VERIFICAÇÃO (ASSÍNCRONA) ---
async def verificar_partidas(context: CallbackContext):
    chat_id = dados.get('chat_id')
    if not chat_id:
        logger.warning("Notificação automática pulada: Chat ID não configurado. Use /start.")
        return
    logger.info("Iniciando verificação de partidas...")
    async with httpx.AsyncClient() as client:
        for gm_username in dados.get('gms_a_monitorar', []):
            try:
                url = f"https://lichess.org/api/user/{gm_username}/current-game"
                headers = {'Accept': 'application/json'}
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    partida = response.json()
                    id_partida = partida.get('id')
                    if not id_partida or 'opponent' not in partida: continue
                    ritmo_partida = partida.get('speed', '').lower()
                    ritmos_permitidos = dados.get('ritmos_permitidos', [])
                    if ritmos_permitidos and ritmo_partida not in ritmos_permitidos: continue
                    if id_partida in dados.get('partidas_notificadas', set()): continue
                    oponente = partida['opponent'].get('username', 'Desconhecido')
                    cor = "Brancas" if partida['players']['white']['user']['name'].lower() == gm_username else "Negras"
                    link_partida = f"https://lichess.org/{id_partida}"
                    mensagem = (
                        f"📢 *GM {gm_username.capitalize()} está jogando!* 📢\n\n"
                        f"⚔️ *Oponente:* {oponente}\n"
                        f"⌛ *Ritmo:* {ritmo_partida.capitalize()}\n"
                        f"🎨 *Cor:* {cor}\n\n"
                        f"🔗 [Ver partida ao vivo]({link_partida})"
                    )
                    await context.bot.send_message(chat_id=chat_id, text=mensagem, parse_mode='Markdown')
                    dados['partidas_notificadas'].add(id_partida)
                    salvar_dados()
            except Exception as e:
                logger.error(f"Erro ao verificar {gm_username}: {e}", exc_info=False)


# --- COMANDOS DO TELEGRAM (ASSÍNCRONOS) ---
async def start(update: Update, context: CallbackContext):
    dados['chat_id'] = update.effective_chat.id
    salvar_dados()
    await update.message.reply_text(
        "Olá! Sou seu bot de monitoramento de Xadrez.\nUse o comando /ajuda para ver o que posso fazer."
    )
    logger.info(f"Bot iniciado/atualizado no chat {dados['chat_id']}")

async def ajuda(update: Update, context: CallbackContext):
    texto_ajuda = (
        "Aqui estão os comandos que você pode usar:\n\n"
        "*/verificar* - Verifica imediatamente se alguém está jogando.\n"
        "*/listar_gms* - Mostra a lista de GMs monitorados.\n"
        "*/adicionargm <username>* - Adiciona um GM à lista.\n"
        "*/removergm <username>* - Remove um GM da lista.\n"
        "*/filtroritmo <ritmo...>* - Define um filtro de ritmo.\n"
        "*/filtroritmo ver* - Mostra o filtro atual.\n"
        "*/filtroritmo todos* - Remove todos os filtros.\n"
        "*/ajuda* - Mostra esta mensagem de ajuda."
    )
    await update.message.reply_markdown(texto_ajuda)

async def verificar_agora(update: Update, context: CallbackContext):
    await update.message.reply_text("Verificando os GMs, um momento...")
    chat_id = dados.get('chat_id')
    if not chat_id:
        await update.message.reply_text("Por favor, use /start primeiro para eu saber quem você é.")
        return
    jogos_encontrados = 0
    async with httpx.AsyncClient() as client:
        for gm_username in dados.get('gms_a_monitorar', []):
            try:
                url = f"https://lichess.org/api/user/{gm_username}/current-game"
                headers = {'Accept': 'application/json'}
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    jogos_encontrados += 1
                    partida = response.json()
                    id_partida = partida.get('id')
                    if not id_partida or 'opponent' not in partida: continue
                    oponente = partida['opponent'].get('username', 'Desconhecido')
                    ritmo_partida = partida.get('speed', 'Desconhecido').capitalize()
                    link_partida = f"https://lichess.org/{id_partida}"
                    mensagem = (
                        f"✅ *{gm_username.capitalize()}* está jogando agora!\n"
                        f"Oponente: {oponente}\n"
                        f"Ritmo: {ritmo_partida}\n"
                        f"🔗 [Ver partida]({link_partida})"
                    )
                    await context.bot.send_message(chat_id=chat_id, text=mensagem, parse_mode='Markdown')
            except Exception as e:
                logger.error(f"Erro na verificação manual de {gm_username}: {e}")
    if jogos_encontrados == 0:
        await update.message.reply_text("Verifiquei toda a lista. Ninguém está jogando no momento.")

async def listar_gms(update: Update, context: CallbackContext):
    if dados.get('gms_a_monitorar'):
        lista_gms = "\n".join([f"- `{gm}`" for gm in dados['gms_a_monitorar']])
        await update.message.reply_markdown(f"Estou monitorando os seguintes GMs:\n{lista_gms}")
    else:
        await update.message.reply_text("A lista de monitoramento está vazia.")

async def adicionar_gm(update: Update, context: CallbackContext):
    if not context.args:
        await update.message.reply_text("Uso: /adicionargm <username>")
        return
    gm_username = context.args[0].lower()
    if gm_username in dados.get('gms_a_monitorar', []):
        await update.message.reply_text(f"{gm_username.capitalize()} já está na lista.")
    else:
        dados.setdefault('gms_a_monitorar', []).append(gm_username)
        salvar_dados()
        await update.message.reply_text(f"{gm_username.capitalize()} foi adicionado!")

async def remover_gm(update: Update, context: CallbackContext):
    if not context.args:
        await update.message.reply_text("Uso: /removergm <username>")
        return
    gm_username = context.args[0].lower()
    if gm_username in dados.get('gms_a_monitorar', []):
        dados['gms_a_monitorar'].remove(gm_username)
        salvar_dados()
        await update.message.reply_text(f"{gm_username.capitalize()} foi removido.")
    else:
        await update.message.reply_text(f"Não encontrei {gm_username.capitalize()} na lista.")

async def filtro_ritmo(update: Update, context: CallbackContext):
    if not context.args:
        await update.message.reply_text("Uso: /filtroritmo <ritmo1> ou ver/todos")
        return
    primeiro_arg = context.args[0].lower()
    if primeiro_arg == 'ver':
        if dados.get('ritmos_permitidos'):
            await update.message.reply_text(f"Filtro atual: {', '.join(dados['ritmos_permitidos'])}")
        else:
            await update.message.reply_text("Nenhum filtro de ritmo ativo.")
    elif primeiro_arg == 'todos':
        dados['ritmos_permitidos'] = []
        salvar_dados()
        await update.message.reply_text("Filtro de ritmo removido.")
    else:
        ritmos_validos = ['bullet', 'blitz', 'rapid', 'classical']
        novos_ritmos = [r.lower() for r in context.args if r.lower() in ritmos_validos]
        if novos_ritmos:
            dados['ritmos_permitidos'] = novos_ritmos
            salvar_dados()
            await update.message.reply_text(f"Filtro de ritmo atualizado para: {', '.join(novos_ritmos)}")
        else:
            await update.message.reply_text(f"Nenhum ritmo válido encontrado.")


# --- LÓGICA DE INICIALIZAÇÃO ---

def run_telegram_bot():
    """Esta função contém toda a lógica para configurar e rodar o bot do Telegram."""
    
    ### MUDANÇA: Criar e definir um novo event loop para esta thread ###
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    carregar_dados()
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Registra todos os Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ajuda", ajuda))
    application.add_handler(CommandHandler("verificar", verificar_agora))
    application.add_handler(CommandHandler("listar_gms", listar_gms))
    application.add_handler(CommandHandler("adicionar_gm", adicionar_gm))
    application.add_handler(CommandHandler("adicionargm", adicionar_gm))
    application.add_handler(CommandHandler("remover_gm", remover_gm))
    application.add_handler(CommandHandler("removergm", remover_gm))
    application.add_handler(CommandHandler("filtro_ritmo", filtro_ritmo))
    application.add_handler(CommandHandler("filtroritmo", filtro_ritmo))
    
    job_queue = application.job_queue
    job_queue.run_repeating(
        callback=verificar_partidas,
        interval=120,
        first=10,
        name="verificar_partidas"
    )
    
    logger.info("Bot do Telegram iniciado e tarefa de verificação agendada.")
    application.run_polling()

if __name__ == '__main__':
    # Inicia o bot do Telegram em uma thread separada
    logger.info("Iniciando o bot em uma thread separada...")
    bot_thread = threading.Thread(target=run_telegram_bot)
    bot_thread.start()
    
    # Inicia o servidor web na thread principal para responder aos health checks
    logger.info("Iniciando o servidor web para health checks...")
    port = int(os.environ.get("PORT", 8080))
    server.run(host="0.0.0.0", port=port)