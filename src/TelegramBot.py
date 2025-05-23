import base64
from contextlib import suppress
import logging
import traceback
from typing import List
import os
import json

from mistralai import Mistral
from aiogram import Dispatcher, Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.markdown import hide_link, hcode
from redis.asyncio import Redis
from aiogram import F
from Storage import TCRedisStorage
from TransactionHandler import test_swap_transaction, get_swap_route, create_swap_transaction
from SwapCoffeeAPI import get_tokens
from tonutils.tonconnect import TonConnect
from tonutils.tonconnect.models import WalletApp, Event, EventError, SendTransactionResponse
from tonutils.tonconnect.utils.exceptions import TonConnectError, UserRejectsError, RequestTimeoutError
from tonutils.wallet.messages import TransferMessage
from aiogram.fsm.state import State, StatesGroup


# ----------------------------
# Configuration and Initialization
# ----------------------------
BOT_TOKEN = os.getenv("BOT_API_KEY")
REDIS_DSN = os.getenv("REDIS_DSN")
TC_MANIFEST_URL = "https://raw.githubusercontent.com/tg-dex-swap-bot/tonconnect-manifest/refs/heads/main/tonconnect-manifest.json"
TOKENS = {}
client = Mistral(api_key=os.getenv("MISTRAL_API"))

redis = Redis.from_url(url=REDIS_DSN)
dp = Dispatcher(storage=RedisStorage(redis))
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
tc = TonConnect(
    storage=TCRedisStorage(redis),
    manifest_url=TC_MANIFEST_URL,
    wallets_fallback_file_path="../wallets.json"
)


class SwapStates(StatesGroup):
    waiting_input = State()
    setting_max_splits = State()
    setting_max_length = State()
    setting_slippage = State()
    token1 = State()
    token2 = State()
    amount = State()
    directiom = State()
    waiting_for_swap_text = State()


# ----------------------------
# State and Message Helpers
# ----------------------------
async def delete_last_message(user_id: int, message_id: int) -> None:
    state = dp.fsm.resolve_context(bot, user_id, user_id)
    last_message_id = (await state.get_data()).get("last_message_id")

    if last_message_id is not None:
        with suppress(Exception):
            await bot.delete_message(chat_id=user_id, message_id=last_message_id)

    await state.update_data(last_message_id=message_id)


async def _is_valid_token(token: str) -> bool:
    return token in TOKENS


def _load_tokens():
    global TOKENS
    try:
        tokens_list = get_tokens()
        TOKENS = {
            token["metadata"]["symbol"]: token["address"]["address"]
            for token in tokens_list
            if token["address"]["address"]
        }
        print(f"Loaded {len(TOKENS)} tokens.")
    except Exception as e:
        print(f"Failed to load tokens: {e}")
        TOKENS = {}


# ----------------------------
# Markup Builders
# ----------------------------
def _connect_wallet_markup(
        wallets: List[WalletApp],
        selected_wallet: WalletApp,
        connect_url: str,
) -> InlineKeyboardMarkup:
    wallets_button = [
        *[
            InlineKeyboardButton(
                text=f"• {wallet.name} •" if wallet.app_name == selected_wallet.app_name else wallet.name,
                callback_data=f"app_wallet:{wallet.app_name}",
            ) for wallet in wallets
        ]
    ]
    connect_wallet_button = InlineKeyboardButton(
        text=f"Connect {selected_wallet.name}",
        url=connect_url,
    )
    builder = InlineKeyboardBuilder()
    builder.row(connect_wallet_button)
    builder.row(*wallets_button, width=2)

    return builder.as_markup()


def _confirm_transaction_markup(url: str, wallet_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Open {wallet_name}", url=url)],
            [InlineKeyboardButton(text="Cancel", callback_data="cancel_transaction")],
        ]
    )


def _go_to_main_menu_markup() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Main menu", callback_data="main_menu"))
    return builder.as_markup()


async def _swap_menu_markup(direction: str = "input") -> InlineKeyboardMarkup:

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Build route", callback_data="build_route"))
    builder.row(
        InlineKeyboardButton(text="Input token", callback_data="edit_token1"),
        InlineKeyboardButton(text="Output token", callback_data="edit_token2"),
        InlineKeyboardButton(text="Amount", callback_data="edit_amount"),
    )
    builder.row(InlineKeyboardButton(text=f"Amount: {direction}", callback_data="edit_direction"))
    builder.row(InlineKeyboardButton(text="Options", callback_data="swap_options"), 
                InlineKeyboardButton(text="Cancel", callback_data="cancel"))
    return builder.as_markup()


def _swap_options_markup() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Slippage", callback_data="set_slippage"))
    builder.row(InlineKeyboardButton(text="Max Splits", callback_data="set_max_splits"))
    builder.row(InlineKeyboardButton(text="Max Length", callback_data="set_max_length"))
    builder.row(InlineKeyboardButton(text="Back", callback_data="back"))
    return builder.as_markup()


def _main_menu_markup() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Swap", callback_data="swap_menu"))
    builder.row(InlineKeyboardButton(text="Options", callback_data="swap_options"))
    builder.row(InlineKeyboardButton(text="Disconnect wallet", callback_data="disconnect_wallet"))
    return builder.as_markup()


def _confirm_build_route_markup() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Initiate swap", callback_data="confirm_transaction"),
        InlineKeyboardButton(text="Back", callback_data="back"),
        width=2,
    )
    return builder.as_markup()


def _back_only_markup():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Back", callback_data="back")]
    ])


# ----------------------------
# Window Rendering Functions
# ----------------------------
async def connect_wallet_window(state: FSMContext, user_id: int) -> None:
    connector = await tc.init_connector(user_id)
    state_data = await state.get_data()
    wallets = await tc.get_wallets()
    wallets = [w for w in wallets if w.app_name.lower() in ("tonkeeper", "telegram-wallet")]

    selected_wallet = state_data.get("selected_wallet", wallets[0].app_name)
    selected_wallet = next(w for w in wallets if w.app_name == selected_wallet)
    connect_url = await connector.connect_wallet(wallet_app=selected_wallet)

    qrcode_url = (
        f"https://qrcode.ness.su/create?"
        f"box_size=20&border=7&image_padding=20"
        f"&data={base64.b64encode(connect_url.encode()).decode()}"
        f"&image_url={base64.b64encode(selected_wallet.image.encode()).decode()}"
    )

    text = f"{hide_link(qrcode_url)}Connect your wallet!"
    reply_markup = _connect_wallet_markup(wallets, selected_wallet, connect_url)

    message = await bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup)
    await delete_last_message(user_id, message.message_id)


async def wallet_connected_window(user_id: int, state: FSMContext) -> None:
    connector = await tc.init_connector(user_id)
    wallet_address = connector.wallet.account.address.to_str(is_bounceable=False)

    reply_markup = _main_menu_markup()
    text = f"Connected wallet:\n{hcode(wallet_address)}\n\nChoose an action:"

    await state.update_data(back_state="main_menu")

    message = await bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup)
    await delete_last_message(user_id, message.message_id)


async def send_transaction_window(user_id: int) -> None:
    connector = await tc.init_connector(user_id)
    reply_markup = _confirm_transaction_markup(
        url=connector.wallet_app.direct_url,
        wallet_name=connector.wallet_app.name,
    )

    text = "Please confirm the transaction in your wallet."

    message = await bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup)
    await delete_last_message(user_id, message.message_id)


async def transaction_sent_window(user_id: int, transaction: SendTransactionResponse) -> None:
    text = (
        "Transaction sent!\n\n"
        f"Transaction msg hash:\n{hcode(transaction.normalized_hash)}\n"
        f"Transaction BoC:\n{hcode(transaction.boc)}\n"
    )
    reply_markup = _go_to_main_menu_markup()

    await bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup)


async def error_window(user_id: int, message_text: str, button_text: str, callback_data: str) -> None:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=button_text, callback_data=callback_data))
    builder.row(InlineKeyboardButton(text="Cancel", callback_data="cancel"))
    reply_markup = builder.as_markup()

    message = await bot.send_message(chat_id=user_id, text=message_text, reply_markup=reply_markup)
    await delete_last_message(user_id, message.message_id)


async def swap_menu_window(user_id: int, state: FSMContext) -> None:
    data = await state.get_data()
    if "slippage" not in data:
        await state.update_data(slippage=0.05)
    token1 = data.get("token1", "N/A")
    token2 = data.get("token2", "N/A")
    amount = data.get("amount", "N/A")
    direction = data.get("direction", "input")

    if direction == "input":
        direction_text = f"Amount to send ({token1})"
    else:
        direction_text = f"Amount to receive ({token2})"

    text = (
        f"<b>Swap:</b> <code>{token1}</code> → <code>{token2}</code>\n"
        f"<b>{direction_text}:</b> {amount}"
    )

    await state.update_data(back_state="swap_menu")

    reply_markup = await _swap_menu_markup(direction)
    msg = await bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup)
    await delete_last_message(user_id, msg.message_id)


async def swap_options_window(user_id: int, state: FSMContext) -> None:
    data = await state.get_data()
    slippage = data.get("slippage", 0.05)
    max_splits = data.get("max_splits", 1)
    max_length = data.get("max_length", 2)

    text = (
        f"<b>Options:</b>\n"
        f"• Slippage: <code>{slippage}</code>\n"
        f"• Max Splits: <code>{max_splits}</code>\n"
        f"• Max Length: <code>{max_length}</code>"
    )

    markup = _swap_options_markup()
    msg = await bot.send_message(chat_id=user_id, text=text, reply_markup=markup)
    await delete_last_message(user_id, msg.message_id)


# ----------------------------
# TonConnect Event Handlers
# ----------------------------
@tc.on_event(Event.CONNECT)
async def connect_event(user_id: int) -> None:
    state = dp.fsm.resolve_context(bot, user_id, user_id)
    await wallet_connected_window(user_id, state)


@tc.on_event(EventError.CONNECT)
async def connect_error_event(error: TonConnectError, user_id: int) -> None:
    button_text, callback_data = "Try again", "connect_wallet"
    if isinstance(error, UserRejectsError):
        message_text = "You rejected the wallet connection."
    elif isinstance(error, RequestTimeoutError):
        message_text = "Connection request timed out."
    else:
        message_text = "Connection error. Error: {error.message}"
    await error_window(user_id, message_text, button_text, callback_data)


@tc.on_event(Event.DISCONNECT)
async def disconnect_event(user_id: int) -> None:
    state = dp.fsm.resolve_context(bot, user_id, user_id)
    await connect_wallet_window(state, user_id)


@tc.on_event(EventError.DISCONNECT)
async def disconnect_error_event(error: TonConnectError, user_id: int) -> None:
    button_text, callback_data = "Try again", "connect_wallet"
    if isinstance(error, UserRejectsError):
        message_text = "You rejected the wallet disconnection."
    elif isinstance(error, RequestTimeoutError):
        message_text = "Disconnect request timed out."
    else:
        message_text = "Disconnect error. Error: {error.message}"

    await error_window(user_id, message_text, button_text, callback_data)


@tc.on_event(Event.TRANSACTION)
async def transaction_event(user_id: int, transaction: SendTransactionResponse) -> None:
    await transaction_sent_window(user_id, transaction)


@tc.on_event(EventError.TRANSACTION)
async def transaction_error_event(error: TonConnectError, user_id: int) -> None:
    button_text, callback_data = "Try again", "main_menu"
    if isinstance(error, UserRejectsError):
        message_text = "You rejected the transaction."
    elif isinstance(error, RequestTimeoutError):
        message_text = "Transaction request timed out."
    else:
        message_text = "Transaction error. Error: {error.message}"

    await error_window(user_id, message_text, button_text, callback_data)


# ----------------------------
# Bot Command, Input and Callback Handlers
# ----------------------------
@dp.callback_query(F.data == "edit_token1")
async def edit_token1_handler(callback: CallbackQuery, state: FSMContext):
    msg = await callback.message.answer("Enter Token 1 symbol (e.g. TON):")
    await delete_last_message(callback.from_user.id, msg.message_id)
    await state.set_state(SwapStates.token1)
    await callback.answer()


@dp.message(SwapStates.token1)
async def token1_input_handler(message: Message, state: FSMContext):
    await delete_last_message(message.from_user.id, message.message_id)
    token = message.text.strip().upper()
    if not await _is_valid_token(token):
        msg = await message.answer("Invalid token symbol. Please try again.")
        await delete_last_message(message.from_user.id, msg.message_id)
        return
    await state.update_data(token1=token)
    await swap_menu_window(message.from_user.id, state)
    await state.set_state(None)


@dp.callback_query(F.data == "edit_token2")
async def edit_token2_handler(callback: CallbackQuery, state: FSMContext):
    msg = await callback.message.answer("Enter Token 2 symbol (e.g. USDT):")
    await delete_last_message(callback.from_user.id, msg.message_id)
    await state.set_state(SwapStates.token2)
    await callback.answer()


@dp.message(SwapStates.token2)
async def token2_input_handler(message: Message, state: FSMContext):
    await delete_last_message(message.from_user.id, message.message_id)
    token = message.text.strip().upper()
    if not await _is_valid_token(token):
        msg = await message.answer("Invalid token symbol. Please try again.")
        await delete_last_message(message.from_user.id, msg.message_id)
        return
    await state.update_data(token2=token)
    await swap_menu_window(message.from_user.id, state)
    await state.set_state(None)


@dp.callback_query(F.data == "edit_amount")
async def edit_amount_handler(callback: CallbackQuery, state: FSMContext):
    msg = await callback.message.answer("Enter amount (number):")
    await delete_last_message(callback.from_user.id, msg.message_id)
    await state.set_state(SwapStates.amount)
    await callback.answer()


@dp.message(SwapStates.amount)
async def amount_input_handler(message: Message, state: FSMContext):
    await delete_last_message(message.from_user.id, message.message_id)
    try:
        amount = float(message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        msg = await message.answer("Invalid amount. Please enter a positive number.")
        await delete_last_message(message.from_user.id, msg.message_id)
        return
    await state.update_data(amount=str(amount))
    await swap_menu_window(message.from_user.id, state)
    await state.set_state(None)



@dp.callback_query(F.data == "edit_direction")
async def edit_direction_handler(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    current = data.get("direction", "input")
    new_direction = "output" if current == "input" else "input"
    await state.update_data(direction=new_direction)
    await swap_menu_window(callback.from_user.id, state)
    await callback.answer(f"Direction switched to: {'Amount to receive' if new_direction == 'input' else 'Amount to send'}")


@dp.message(CommandStart())
async def start_command(message: Message, state: FSMContext) -> None:
    connector = await tc.init_connector(message.from_user.id)
    rpc_request_id = (await state.get_data()).get("rpc_request_id")
    if connector.is_transaction_pending(rpc_request_id):
        connector.cancel_pending_transaction(rpc_request_id)

    if not connector.connected:
        await connect_wallet_window(state, message.from_user.id)
    else:
        await wallet_connected_window(message.from_user.id, state)


@dp.message(Command("test"))
async def test_command(message: Message):
    try:
        connector = await tc.init_connector(message.from_user.id)
        if not connector.connected:
            await message.answer("Please connect your wallet first using /start")
            return
        result = await test_swap_transaction(connector)
        await message.answer(f"Test swap initiated. Result: {result}")
    except Exception as e:
        await message.answer(f"Error during test swap: {str(e)}")


@dp.message(SwapStates.setting_max_splits)
async def set_max_splits(message: Message, state: FSMContext):
    data = await state.get_data()
    prompt_id = data.get("prompt_message_id")
    if prompt_id:
        with suppress(Exception):
            await bot.delete_message(chat_id=message.chat.id, message_id=prompt_id)

    try:
        value = int(message.text.strip())
        if value <= 0 | value > 20:
            raise ValueError
        await state.update_data(max_splits=value)
        await message.answer(f"✅ Max Splits set to {value}")
    except ValueError:
        await error_window(
            message.from_user.id,
            "❌ Invalid value. Should be in range from '1' to '20'",
            "Try again",
            "set_max_splits",
        )
        return
    await swap_options_window(message.from_user.id, state)


@dp.message(SwapStates.setting_max_length)
async def set_max_length(message: Message, state: FSMContext):
    data = await state.get_data()
    prompt_id = data.get("prompt_message_id")
    if prompt_id:
        with suppress(Exception):
            await bot.delete_message(chat_id=message.chat.id, message_id=prompt_id)

    try:
        value = int(message.text.strip())
        if value <= 1 | value > 5:
            raise ValueError
        await state.update_data(max_length=value)
        await message.answer(f"✅ Max Length set to {value}")
    except ValueError:
        await error_window(
            message.from_user.id,
            "❌ Invalid value. Should be in range from '2' to '5'",
            "Try again",
            "set_max_length",
        )
        return
    await swap_options_window(message.from_user.id, state)


@dp.message(SwapStates.setting_slippage)
async def set_slippage(message: Message, state: FSMContext):
    data = await state.get_data()
    prompt_id = data.get("prompt_message_id")
    if prompt_id:
        with suppress(Exception):
            await bot.delete_message(chat_id=message.chat.id, message_id=prompt_id)

    try:
        value = float(message.text.strip())
        if value < 0 or value > 1:
            raise ValueError
        await state.update_data(slippage=value)
        await message.answer(f"✅ Slippage set to {value}")
    except ValueError:
        await error_window(
            message.from_user.id,
            "❌ Invalid value. Should be a number from 0 to 1",
            "Try again",
            "set_slippage",
        )
        return
    await swap_options_window(message.from_user.id, state)


@dp.callback_query(F.data == "build_route")
async def build_route_handler(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    data = await state.get_data()
    input_token = TOKENS.get(data.get("token1"))
    output_token = TOKENS.get(data.get("token2"))
    amount = float(data.get("amount"))
    direction = data.get("direction", "input")
    max_splits = data.get("max_splits")
    max_length = data.get("max_length")

    is_input = True if direction == "input" else False
    route = get_swap_route(input_token, output_token, amount, max_splits, max_length, is_input)
    
    if not route.get("paths"):
        await delete_last_message(callback_query.from_user.id, (await dp.fsm.resolve_context(bot, callback_query.from_user.id, callback_query.from_user.id).get_data()).get("last_message_id"))
        msg = await bot.send_message(
            callback_query.from_user.id,
            "<b>Swap route not found.</b>\nPlease check the parameters and try again.",
            reply_markup=_back_only_markup(),
            parse_mode="HTML"
        )
        await delete_last_message(callback_query.from_user.id, msg.message_id) 
        return

    input_symbol = route["input_token"]["metadata"]["symbol"]
    output_symbol = route["output_token"]["metadata"]["symbol"]
    input_amount = route["input_amount"]
    output_amount = route["output_amount"]

    def format_path(path):
        chain = []
        current = path
        while current:
            dex = current.get("dex", "unknown")
            token_in = current["input_token"]["metadata"]["symbol"]
            token_out = current["output_token"]["metadata"]["symbol"]
            out_amount = current.get("swap", {}).get("output_amount", "N/A")
            if isinstance(out_amount, float):
                out_amount = round(out_amount, 6)
            chain.append(f"{dex}: {token_in} → {token_out} (Output Amount: {out_amount})")
            current = current.get("next", [{}])[0] if current.get("next") else None
        return "\n".join(chain)

    route_details = format_path(route["paths"][0])

    response = (
        f"🔁 <b>Swap Route:</b>\n"
        f"<b>From:</b> {input_symbol}\n"
        f"<b>To:</b> {output_symbol}\n"
        f"<b>Input Amount:</b> {input_amount}\n"
        f"<b>Output Amount:</b> {round(output_amount, 6)}\n"
        f"<b>Route Path:</b>\n{route_details}"
    )

    await state.update_data(route=route)

    await bot.send_message(callback_query.from_user.id, response, reply_markup=_confirm_build_route_markup(), parse_mode="HTML")


@dp.callback_query(F.data == "confirm_transaction")
async def confirm_transaction_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    data = await state.get_data()
    connector = await tc.init_connector(callback.from_user.id)
    try:
        transaction = await create_swap_transaction(
            connector=connector,
            sender_address=connector.wallet.account.address.to_str(is_bounceable=False),
            route=data.get("route"),
            slippage=data.get("slippage"))

        if transaction is not None:
            await send_transaction_window(callback.from_user.id)
        else:
            await error_window(callback.from_user.id, "Transaction was not sent.", "Back", "swap_menu")

    except Exception as e:
        logging.error("Transaction error:\n%s", traceback.format_exc())
        await error_window(callback.from_user.id, f"Error: {e}", "Back", "swap_menu")


@dp.callback_query()
async def callback_query_handler(callback_query: CallbackQuery, state: FSMContext) -> None:
    connector = await tc.init_connector(callback_query.from_user.id)
    rpc_request_id = (await state.get_data()).get("rpc_request_id")
    data = callback_query.data
    current_state = await state.get_state()

    if data.startswith("app_wallet:"):
        await state.update_data(selected_wallet=data.split(":")[1])
        await connect_wallet_window(state, callback_query.from_user.id)
    elif data == "main_menu":
        await wallet_connected_window(callback_query.from_user.id, state)
    elif data == "connect_wallet":
        await connect_wallet_window(state, callback_query.from_user.id)
    elif data == "disconnect_wallet":
        connector.add_event_kwargs(Event.DISCONNECT, state=state)
        await connector.disconnect_wallet()
    elif data == "cancel_transaction":
        if connector.is_transaction_pending(rpc_request_id):
            connector.cancel_pending_transaction(rpc_request_id)
        await wallet_connected_window(callback_query.from_user.id, state)
    elif data == "send_transaction":
        rpc_request_id = await connector.send_transfer(
            destination=connector.account.address,
            amount=0.000000001,
            body="Hello from tonutils!",
        )
        await send_transaction_window(callback_query.from_user.id)
        await state.update_data(rpc_request_id=rpc_request_id)
    elif data == "send_batch_transaction":
        messages = [
            TransferMessage(
                destination=connector.account.address,
                amount=0.000000001,
                body="Hello from tonutils!",
            )
            for _ in range(4)
        ]
        rpc_request_id = await connector.send_batch_transfer(messages)
        await send_transaction_window(callback_query.from_user.id)
        await state.update_data(rpc_request_id=rpc_request_id)
    elif callback_query.data == "swap_menu":
        await swap_menu_window(callback_query.from_user.id, state)
    elif data == "cancel":
        with suppress(Exception):
            await bot.delete_message(chat_id=callback_query.from_user.id, message_id=callback_query.message.message_id)
        await wallet_connected_window(callback_query.from_user.id, state)
    elif data == "swap_options":
        await state.update_data(previous_state=current_state)
        await swap_options_window(callback_query.from_user.id, state)
    elif data == "set_max_splits":
        await state.set_state(SwapStates.setting_max_splits)
        msg = await bot.send_message(chat_id=callback_query.from_user.id, text="Enter max splits (integer):")
        await state.update_data(prompt_message_id=msg.message_id)
    elif data == "set_max_length":
        await state.set_state(SwapStates.setting_max_length)
        msg = await bot.send_message(chat_id=callback_query.from_user.id, text="Enter max length (integer):")
        await state.update_data(prompt_message_id=msg.message_id)
    elif data == "set_slippage":
        await state.set_state(SwapStates.setting_slippage)
        msg = await bot.send_message(chat_id=callback_query.from_user.id, text="Enter slippage value (float from 0 to 1):")
        await state.update_data(prompt_message_id=msg.message_id)
    elif data == "back":
        state_data = await state.get_data()
        previous_state = state_data.get("previous_state")
        
        # Если возвращаемся из настроек Max Splits/Length
        if current_state in ["SwapStates:setting_max_splits", "SwapStates:setting_max_length"]:
            await wallet_connected_window(callback_query.from_user.id, state)
            await state.set_state(SwapStates.waiting_for_swap_text)
        
        # Если возвращаемся из меню опций
        elif previous_state == "SwapStates:waiting_for_swap_text":
            await wallet_connected_window(callback_query.from_user.id, state)
            await state.set_state(SwapStates.waiting_for_swap_text)
        
        # Если возвращаемся из меню обмена
        elif state_data.get("back_state") == "swap_menu":
            await swap_menu_window(callback_query.from_user.id, state)
        
        # Дефолтный случай - возврат в главное меню
        else:
            await wallet_connected_window(callback_query.from_user.id, state)
        
        # Открываем начальное меню

    await callback_query.answer()

@dp.message(Command("swap"))
async def start_swap_command(message: Message, state: FSMContext):
    example_text = (
        "Enter the exchange request in the format:\n"
        "🔹 <b>Examples:</b>\n"
        "- Обменять 10 PX на USDT\n"
        "- Обменять 130 DOGS на NOT\n"
        "Now ~300 tokens are available. Attention: Exchanges with TON are temporarily unavailable."
    )
    
    await message.answer(example_text, parse_mode="HTML")
    await state.set_state(SwapStates.waiting_for_swap_text)


@dp.message(SwapStates.waiting_for_swap_text)
async def process_swap_text(message: Message, state: FSMContext):
    try:
        user_text = message.text.strip()
        
        # Сохраняем оригинальный текст для возможного повторного использования
        await state.update_data(swap_text=user_text)
        
        model = "mistral-large-latest"
        with open('prompt.txt', 'r', encoding='utf-8') as file:
            content = file.read().replace('\n', ' ')
        
        # Используем текст пользователя вместо фиксированного
        chat_response = client.chat.complete(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": content + user_text,
                },
            ]
        )
        
        response_text = chat_response.choices[0].message.content
        print(response_text)
        
        first_brace = response_text.find('{')
        last_brace = response_text.rfind('}')
        if first_brace == -1 or last_brace == -1:
            raise ValueError("Exchange parameters could not be recognized")
        
        json_text = response_text[first_brace:last_brace + 1]
        data = json.loads(json_text)
        
        # Проверяем наличие обязательных полей
        required_fields = ["input_token", "output_token", "amount"]
        if not all(field in data for field in required_fields):
            raise ValueError("Not all required parameters are specified")

        # Проверяем существование токенов в TOKENS
        input_token = data["input_token"].upper()
        output_token = data["output_token"].upper()
        if input_token not in TOKENS or output_token not in TOKENS:
            raise ValueError("The entered tokens were not recognized")

        # Обновляем состояние
        await state.update_data(
            token1=input_token,
            token2=output_token,
            amount=str(data["amount"]),
            direction="input"
        )
        
        # Показываем подтверждение пользователю
        confirm_text = (
            f"🔹 <b>Confirm exchange parameters:</b>\n"
            f"• Giving: {data['amount']} {data['input_token']}\n"
            f"• Getting: {data['output_token']}\n\n"
            f"That's right?"
        )
        
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="✅ Yeah, let's go!", callback_data="build_route"),
            InlineKeyboardButton(text="❌ Nah, we're changing", callback_data="cancel"),
            width=2
        )
        
        await message.answer(confirm_text, reply_markup=builder.as_markup(), parse_mode="HTML")
        
    except json.JSONDecodeError:
        await message.answer("❌ Request processing error. Please use the correct format.")
        await start_swap_command(message, state)
    except ValueError as e:
        await message.answer(f"❌ Error: {str(e)}")
        await start_swap_command(message, state)
    except Exception as e:
        await message.answer("❌ An unexpected error has occurred. Please try again.")
        await start_swap_command(message, state)

# ----------------------------
# Main Entry Point
# ----------------------------
async def main():
    _load_tokens()
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
