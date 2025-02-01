import logging
import io
from PIL import Image
import chess
import chess.svg
import chess.engine
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
import os

load_dotenv()
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TOKEN = os.getenv('TOKEN')
STOCKFISH_PATH = os.getenv('STOCKFISH_PATH')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("Play", callback_data='play')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('Привет! Я бот для игры в шахматы.', reply_markup=reply_markup)

async def play_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await play(update, context)

async def play(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("Белый", callback_data='white')],
        [InlineKeyboardButton("Черный", callback_data='black')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message:
        await update.message.reply_text('Выберите цвет, которым хотите играть:', reply_markup=reply_markup)
    else:
        query = update.callback_query
        await query.edit_message_text(text='Выберите цвет, которым хотите играть:', reply_markup=reply_markup)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    color = query.data
    if color not in ['white', 'black']:
        await query.edit_message_text(text="Пожалуйста, выберите правильный цвет.")
        return
    context.user_data['color'] = color
    context.user_data['move_history'] = []
    context.user_data['board_stack'] = []  # Стек для хранения позиций доски
    context.user_data['messages_to_delete'] = []  # Список для хранения идентификаторов сообщений для удаления
    context.user_data['board_messages'] = []  # Список для хранения идентификаторов сообщений с доской
    context.user_data[
        'user_error_messages'] = []  # Список хранения идентификаторов сообщений с ошибками пользователя
    if color == 'white':
        context.user_data['board'] = chess.Board()
        context.user_data['engine_color'] = chess.BLACK
    else:
        context.user_data['board'] = chess.Board()
        context.user_data['engine_color'] = chess.WHITE
    await send_board(update, context, query)
    if context.user_data['engine_color'] == chess.WHITE:
        await engine_move(update, context, query)
    else:
        await query.edit_message_text(
            text='Игра начинается! Ваш ход. Введите ход в формате "e2e4" или выберите фигуру.')
        context.user_data['first_user_move'] = True

async def send_board(update: Update, context: ContextTypes.DEFAULT_TYPE, query=None) -> None:
    board = context.user_data['board']
    flipped = context.user_data.get('color') == 'black'
    board_svg = chess.svg.board(board, flipped=flipped)
    image = svg_to_png(board_svg)
    bio = io.BytesIO()
    image.save(bio, format='PNG')
    bio.seek(0)
    messages_to_delete = context.user_data.get('messages_to_delete', [])
    for message_id in messages_to_delete:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=message_id)
        except Exception as e:
            logging.error(f"Ошибка при удалении сообщения: {e}")
    context.user_data['messages_to_delete'] = []
    board_messages = context.user_data.get('board_messages', [])
    for message_id in board_messages:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=message_id)
        except Exception as e:
            logging.error(f"Ошибка при удалении сообщения с доской: {e}")
    context.user_data['board_messages'] = []
    chat_id = update.effective_chat.id
    if query:
        msg = await query.message.reply_photo(photo=bio)
    else:
        msg = await context.bot.send_photo(chat_id=chat_id, photo=bio)
    context.user_data['board_messages'].append(msg.message_id)
    keyboard = [
        [InlineKeyboardButton("Показать историю ходов", callback_data='show_history')],
        [InlineKeyboardButton("Вернуть ход", callback_data='undo_move')],
        [InlineKeyboardButton("Выбрать фигуру", callback_data='select_piece')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    history_msg = await msg.reply_text('Выберите действие или введите ваш ход вручную в формате  e2e4', reply_markup=reply_markup)
    context.user_data['messages_to_delete'].append(history_msg.message_id)
    context.user_data['last_menu_message_id'] = history_msg.message_id
async def make_move(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    move_text = update.message.text.strip().lower()
    if move_text in ['exit', 'quit']:
        await update.message.reply_text('Игра завершена.')
        return
    board = context.user_data.get('board', None)
    if not board:
        await update.message.reply_text('Начните новую игру командой /play')
        return
    try:
        move = chess.Move.from_uci(move_text)
        if move not in board.legal_moves:
            if board.is_check():
                check_msg = await update.message.reply_text('Неверный ход, вам шах.')
                context.user_data['messages_to_delete'].append(check_msg.message_id)
            elif board.is_pinned(board.turn, move.from_square):
                pinned_msg = await update.message.reply_text('Неверный ход, ваша фигура связана на короля.')
                context.user_data['messages_to_delete'].append(pinned_msg.message_id)
            else:
                invalid_move_msg = await update.message.reply_text('Неверный ход. Попробуйте снова.')
                context.user_data['messages_to_delete'].append(invalid_move_msg.message_id)
            context.user_data['user_error_messages'].append(update.message.message_id)
            return
        user_error_messages = context.user_data.get('user_error_messages', [])
        for message_id in user_error_messages:
            try:
                await context.bot.delete_message(chat_id=update.message.chat_id, message_id=message_id)
            except Exception as e:
                logging.error(f"Ошибка при удалении сообщения пользователя с ошибкой: {e}")
        context.user_data['user_error_messages'] = []
        # Сохранение текущей позиции доски в стеке
        context.user_data['board_stack'].append(board.copy())
        board.push(move)
        context.user_data['move_history'].append(f'Пользователь: {move}')
        await send_board(update, context)
        await context.bot.delete_message(chat_id=update.message.chat_id, message_id=update.message.message_id)
        if board.is_game_over():
            game_over_msg = await update.message.reply_text(f'Игра окончена! Результат: {board.result()}')
            context.user_data['messages_to_delete'].append(game_over_msg.message_id)
            keyboard = [
                [InlineKeyboardButton("Play", callback_data='play')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await game_over_msg.reply_text('Хотите сыграть еще?', reply_markup=reply_markup)
            return
        await engine_move(update, context)
    except ValueError:
        invalid_format_msg = await update.message.reply_text('Неверный формат хода. Попробуйте снова.')
        context.user_data['messages_to_delete'].append(invalid_format_msg.message_id)
        context.user_data['user_error_messages'].append(update.message.message_id)
    finally:
        if context.user_data.get('first_user_move'):
            del context.user_data['first_user_move']

async def engine_move(update: Update, context: ContextTypes.DEFAULT_TYPE, query=None) -> None:
    board = context.user_data['board']
    engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
    best_move = engine.play(board, chess.engine.Limit(time=1.0)).move
    board.push(best_move)
    context.user_data['move_history'].append(f'Stockfish: {best_move}')
    engine.quit()
    await send_board(update, context, query)
    if board.is_game_over():
        game_over_msg = await (query.message if query else update.message).reply_text(
            f'Игра окончена! Результат: {board.result()}')
        context.user_data['messages_to_delete'].append(game_over_msg.message_id)
        keyboard = [
            [InlineKeyboardButton("Play", callback_data='play')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await game_over_msg.reply_text('Хотите сыграть еще?', reply_markup=reply_markup)
    elif board.turn != context.user_data['engine_color']:
        if context.user_data.get('first_user_move'):
            turn_msg = await (query.message if query else update.message).reply_text(
                'Ваш ход. Введите ход в формате "e2e4" или выберите фигуру.')
            del context.user_data['first_user_move']
        else:
            turn_msg = await (query.message if query else update.message).reply_text('Ваш ход.')
        if 'last_turn_message_id' in context.user_data and context.user_data['last_turn_message_id']:
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id,
                                                 message_id=context.user_data['last_turn_message_id'])
            except Exception as e:
                logging.error(f"Ошибка при удалении сообщения: {e}")
        context.user_data['last_turn_message_id'] = turn_msg.message_id

async def show_move_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    move_history = context.user_data.get('move_history', [])
    if move_history:
        history_text = '\n'.join(move_history)
    else:
        history_text = 'История ходов пуста.'
    history_msg = await query.message.reply_text(f'Показать историю ходов:\n{history_text}')
    context.user_data['messages_to_delete'].append(history_msg.message_id)

async def undo_move(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    board_stack = context.user_data.get('board_stack', [])
    if not board_stack:
        undo_msg = await query.message.reply_text('Нет ходов для возврата.')
        context.user_data['messages_to_delete'].append(undo_msg.message_id)
        return
    context.user_data['board'] = board_stack.pop()
    context.user_data['move_history'].pop()
    await send_board(update, context, query)

async def select_piece(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if 'last_menu_message_id' in context.user_data and context.user_data['last_menu_message_id']:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id,
                                             message_id=context.user_data['last_menu_message_id'])
        except Exception as e:
            logging.error(f"Ошибка при удалении сообщения: {e}")
    board = context.user_data['board']
    turn = board.turn
    pieces = {
        chess.PAWN: 'Пешки',
        chess.KNIGHT: 'Кони',
        chess.BISHOP: 'Слоны',
        chess.ROOK: 'Ладьи',
        chess.QUEEN: 'Ферзи',
        chess.KING: 'Король'
    }
    piece_buttons = []
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece and piece.color == turn:
            piece_name = pieces[piece.piece_type]
            piece_buttons.append((piece_name, square))
    if not piece_buttons:
        no_pieces_msg = await query.message.reply_text('У вас нет доступных фигур для хода.')
        context.user_data['messages_to_delete'].append(no_pieces_msg.message_id)
        return
    piece_dict = {}
    for piece_name, square in piece_buttons:
        if piece_name not in piece_dict:
            piece_dict[piece_name] = []
        piece_dict[piece_name].append(square)
    piece_keyboard = []
    for piece_name in sorted(piece_dict.keys()):
        piece_keyboard.append([InlineKeyboardButton(piece_name, callback_data=piece_name)])
    piece_keyboard.append([InlineKeyboardButton("Назад", callback_data='back')])
    reply_markup = InlineKeyboardMarkup(piece_keyboard)
    pieces_msg = await query.message.reply_text('Выберите фигуру:', reply_markup=reply_markup)
    context.user_data['messages_to_delete'].append(pieces_msg.message_id)
    context.user_data['last_menu_message_id'] = pieces_msg.message_id

async def show_piece_moves(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if 'last_menu_message_id' in context.user_data and context.user_data['last_menu_message_id']:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id,
                                             message_id=context.user_data['last_menu_message_id'])
        except Exception as e:
            logging.error(f"Ошибка при удалении сообщения: {e}")
    piece_name = query.data
    board = context.user_data['board']
    turn = board.turn
    pieces = {
        chess.PAWN: 'Пешки',
        chess.KNIGHT: 'Кони',
        chess.BISHOP: 'Слоны',
        chess.ROOK: 'Ладьи',
        chess.QUEEN: 'Ферзи',
        chess.KING: 'Король'
    }
    piece_type = None
    for k, v in pieces.items():
        if v == piece_name:
            piece_type = k
            break
    if piece_type is None:
        no_moves_msg = await query.message.reply_text('Неизвестный тип фигуры.')
        context.user_data['messages_to_delete'].append(no_moves_msg.message_id)
        return
    squares = [square for square in chess.SQUARES if
               board.piece_at(square) and board.piece_at(square).color == turn and board.piece_at(
                   square).piece_type == piece_type]
    if not squares:
        no_moves_msg = await query.message.reply_text('У этой фигуры нет доступных ходов.')
        context.user_data['messages_to_delete'].append(no_moves_msg.message_id)
        await select_piece(update, context)
        return
    move_buttons = []
    for square in squares:
        legal_moves = [move for move in board.legal_moves if move.from_square == square]
        for move in legal_moves:
            move_buttons.append(InlineKeyboardButton(chess.Move.uci(move), callback_data=str(move)))
    if not move_buttons:
        no_moves_msg = await query.message.reply_text('У этой фигуры нет доступных ходов.')
        context.user_data['messages_to_delete'].append(no_moves_msg.message_id)
        await select_piece(update, context)
        return
    move_buttons.sort(key=lambda btn: btn.text)
    move_keyboard = [move_buttons[i:i + 3] for i in range(0, len(move_buttons), 3)]
    move_keyboard.append([InlineKeyboardButton("Назад", callback_data='back')])
    reply_markup = InlineKeyboardMarkup(move_keyboard)
    moves_msg = await query.message.reply_text('Выберите ход, либо введите вручную:', reply_markup=reply_markup)
    context.user_data['messages_to_delete'].append(moves_msg.message_id)
    context.user_data['last_menu_message_id'] = moves_msg.message_id  # Сохранение идентификатора последнего меню

async def execute_move(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    move_text = query.data
    move = chess.Move.from_uci(move_text)
    board = context.user_data['board']
    if move not in board.legal_moves:
        invalid_move_msg = await query.message.reply_text('Неверный ход. Попробуйте снова.')
        context.user_data['messages_to_delete'].append(invalid_move_msg.message_id)
        return
    # Сохранение текущей позиции доски в стеке
    context.user_data['board_stack'].append(board.copy())
    board.push(move)
    context.user_data['move_history'].append(f'Пользователь: {move}')
    await send_board(update, context, query)
    if board.is_game_over():
        game_over_msg = await query.message.reply_text(f'Игра окончена! Результат: {board.result()}')
        context.user_data['messages_to_delete'].append(game_over_msg.message_id)
        keyboard = [
            [InlineKeyboardButton("Play", callback_data='play')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await game_over_msg.reply_text('Хотите сыграть еще?', reply_markup=reply_markup)
        return
    await engine_move(update, context, query)  # Передаем query

async def go_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if 'last_menu_message_id' in context.user_data and context.user_data['last_menu_message_id']:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id,
                                             message_id=context.user_data['last_menu_message_id'])
        except Exception as e:
            logging.error(f"Ошибка при удалении сообщения: {e}")
    await send_board(update, context, query)

def svg_to_png(svg_data: str) -> Image:
    from cairosvg import svg2png
    from PIL import Image
    png_data = svg2png(bytestring=svg_data)
    return Image.open(io.BytesIO(png_data))

def main() -> None:
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(play_callback, pattern='^play$'))
    application.add_handler(CallbackQueryHandler(button, pattern='^(white|black)$'))
    application.add_handler(CallbackQueryHandler(show_move_history, pattern='^show_history$'))
    application.add_handler(CallbackQueryHandler(undo_move, pattern='^undo_move$'))
    application.add_handler(CallbackQueryHandler(select_piece, pattern='^select_piece$'))
    application.add_handler(CallbackQueryHandler(show_piece_moves, pattern='^(Пешки|Кони|Слоны|Ладьи|Ферзи|Король)$'))
    application.add_handler(CallbackQueryHandler(execute_move, pattern='^[a-h][1-8][a-h][1-8]$'))
    application.add_handler(CallbackQueryHandler(go_back, pattern='^back$'))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, make_move))
    application.run_polling()

if __name__ == '__main__':
    main()