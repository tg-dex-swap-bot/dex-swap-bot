import base64
from contextlib import suppress
from typing import List
import os

from aiogram import Dispatcher, Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.markdown import hide_link, hcode
from redis.asyncio import Redis

from Storage import TCRedisStorage
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
    setting_slippage = State()
    setting_max_splits = State()
    setting_max_length = State()


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
    VALID_TOKENS = {"TON", "USDT", "USDC", "BTC", "ETH"}
    return token.upper() in VALID_TOKENS


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
            [InlineKeyboardButton(text=f"Cancel", callback_data="cancel_transaction")],
        ]
    )


def _choose_action_markup() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Send transaction", callback_data="send_transaction"))
    builder.row(InlineKeyboardButton(text="Send batch transaction", callback_data="send_batch_transaction"))
    builder.row(InlineKeyboardButton(text="Disconnect wallet", callback_data="disconnect_wallet"))
    return builder.as_markup()


def _go_to_main_menu_markup() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Main menu", callback_data="main_menu"))
    return builder.as_markup()


def _swap_menu_markup() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Build root", callback_data="build_root"))
    builder.row(InlineKeyboardButton(text="Options", callback_data="swap_options"))
    builder.row(InlineKeyboardButton(text="Cancel", callback_data="cancel"))
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
    builder.row(InlineKeyboardButton(text="Swap", callback_data="swap_input"))
    builder.row(InlineKeyboardButton(text="Options", callback_data="swap_options"))
    builder.row(InlineKeyboardButton(text="Disconnect wallet", callback_data="disconnect_wallet"))
    return builder.as_markup()


def _cancel_markup() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Cancel", callback_data="cancel"))
    return builder.as_markup()


# ----------------------------
# Window Rendering Functions
# ----------------------------
async def connect_wallet_window(state: FSMContext, user_id: int) -> None:
    connector = await tc.init_connector(user_id)
    state_data = await state.get_data()
    wallets = await tc.get_wallets()

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


async def wallet_connected_window(user_id: int) -> None:
    connector = await tc.init_connector(user_id)
    wallet_address = connector.wallet.account.address.to_str(is_bounceable=False)

    reply_markup = _main_menu_markup()
    text = f"Connected wallet:\n{hcode(wallet_address)}\n\nChoose an action:"
    
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

    message = await bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup)
    await delete_last_message(user_id, message.message_id)


async def error_window(user_id: int, message_text: str, button_text: str, callback_data: str) -> None:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=button_text, callback_data=callback_data))
    reply_markup = builder.as_markup()

    message = await bot.send_message(chat_id=user_id, text=message_text, reply_markup=reply_markup)
    await delete_last_message(user_id, message.message_id)


async def swap_input_window(user_id: int, state: FSMContext) -> None:
    text = "Enter tokens and amount in the format: <code>TOKEN1 TOKEN2 AMOUNT</code>\n" "Example: <code>USDT TON 10.5</code>"
    reply_markup = _cancel_markup()
    msg = await bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup)
    await delete_last_message(user_id, msg.message_id)
    await state.set_state(SwapStates.waiting_input)


async def swap_menu_window(user_id: int, state: FSMContext) -> None:
    data = await state.get_data()
    token1 = data["token1"]
    token2 = data["token2"]
    amount = data["amount"]

    text = f"<b>Swap:</b> <code>{token1}</code> → <code>{token2}</code>\n" f"<b>Amount:</b> {amount}"

    await state.update_data(back_state="swap_menu")

    reply_markup = _swap_menu_markup()
    msg = await bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup)
    await delete_last_message(user_id, msg.message_id)


async def swap_options_window(user_id: int, state: FSMContext) -> None:
    data = await state.get_data()
    slippage = data.get("slippage", 0.5)
    max_splits = data.get("max_splits", 5)
    max_length = data.get("max_length", 10)

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
    await wallet_connected_window(user_id)


@tc.on_event(EventError.CONNECT)
async def connect_error_event(error: TonConnectError, user_id: int) -> None:
    button_text, callback_data = "Try again", "connect_wallet"
    if isinstance(error, UserRejectsError):
        message_text = f"You rejected the wallet connection."
    elif isinstance(error, RequestTimeoutError):
        message_text = f"Connection request timed out."
    else:
        message_text = f"Connection error. Error: {error.message}"
    await error_window(user_id, message_text, button_text, callback_data)


@tc.on_event(Event.DISCONNECT)
async def disconnect_event(user_id: int) -> None:
    state = dp.fsm.resolve_context(bot, user_id, user_id)
    await connect_wallet_window(state, user_id)


@tc.on_event(EventError.DISCONNECT)
async def disconnect_error_event(error: TonConnectError, user_id: int) -> None:
    button_text, callback_data = "Try again", "connect_wallet"
    if isinstance(error, UserRejectsError):
        message_text = f"You rejected the wallet disconnection."
    elif isinstance(error, RequestTimeoutError):
        message_text = f"Disconnect request timed out."
    else:
        message_text = f"Disconnect error. Error: {error.message}"

    await error_window(user_id, message_text, button_text, callback_data)


@tc.on_event(Event.TRANSACTION)
async def transaction_event(user_id: int, transaction: SendTransactionResponse) -> None:
    await transaction_sent_window(user_id, transaction)


@tc.on_event(EventError.TRANSACTION)
async def transaction_error_event(error: TonConnectError, user_id: int) -> None:
    button_text, callback_data = "Try again", "main_menu"
    if isinstance(error, UserRejectsError):
        message_text = f"You rejected the transaction."
    elif isinstance(error, RequestTimeoutError):
        message_text = f"Transaction request timed out."
    else:
        message_text = f"Transaction error. Error: {error.message}"

    await error_window(user_id, message_text, button_text, callback_data)


# ----------------------------
# Bot Command, Input and Callback Handlers
# ----------------------------
@dp.message(CommandStart())
async def start_command(message: Message, state: FSMContext) -> None:
    connector = await tc.init_connector(message.from_user.id)
    rpc_request_id = (await state.get_data()).get("rpc_request_id")
    if connector.is_transaction_pending(rpc_request_id):
        connector.cancel_pending_transaction(rpc_request_id)

    if not connector.connected:
        await connect_wallet_window(state, message.from_user.id)
    else:
        await wallet_connected_window(message.from_user.id)


@dp.message(SwapStates.setting_slippage)
async def set_slippage(message: Message, state: FSMContext):
    try:
        value = float(message.text.strip())
        if value <= 0:
            raise ValueError
        await state.update_data(slippage=value)
        await message.answer(f"✅ Slippage set to {value}")
    except ValueError:
        await message.answer("❌ Invalid value. Enter a positive number.")
        return
    await swap_options_window(message.from_user.id, state)


@dp.message(SwapStates.setting_max_splits)
async def set_max_splits(message: Message, state: FSMContext):
    try:
        value = int(message.text.strip())
        if value <= 0:
            raise ValueError
        await state.update_data(max_splits=value)
        await message.answer(f"✅ Max Splits set to {value}")
    except ValueError:
        await message.answer("❌ Invalid value. Enter a positive integer.")
        return
    await swap_options_window(message.from_user.id, state)


@dp.message(SwapStates.setting_max_length)
async def set_max_length(message: Message, state: FSMContext):
    try:
        value = int(message.text.strip())
        if value <= 0:
            raise ValueError
        await state.update_data(max_length=value)
        await message.answer(f"✅ Max Length set to {value}")
    except ValueError:
        await message.answer("❌ Invalid value. Enter a positive integer.")
        return
    await swap_options_window(message.from_user.id, state)


@dp.message(SwapStates.waiting_input)
async def handle_swap_input(message: Message, state: FSMContext) -> None:
    parts = message.text.strip().split()

    if len(parts) != 3:
        await error_window(
            user_id=message.from_user.id,
            text="Invalid format.\nUse: <code>TOKEN1 TOKEN2 AMOUNT</code>\nExample: <code>USDT TON 10.5</code>",
            btn_text="Try again",
            cb_data="swap_input",
        )
        return

    token1, token2, amount_str = map(str.upper, parts)

    for token in (token1, token2):
        if not await _is_valid_token(token):
            await error_window(
                user_id=message.from_user.id,
                text=f"Token <code>{token}</code> is not supported.",
                btn_text="Try again",
                cb_data="swap_input",
            )
            return

    try:
        amount = float(amount_str)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await error_window(
            user_id=message.from_user.id,
            text="Amount must be a positive number.\nExample: <code>USDT TON 10.5</code>",
            btn_text="Try again",
            cb_data="swap_input",
        )
        return

    await state.update_data(token1=token1, token2=token2, amount=amount)
    await swap_menu_window(message.from_user.id, state)


@dp.callback_query()
async def callback_query_handler(callback_query: CallbackQuery, state: FSMContext) -> None:
    connector = await tc.init_connector(callback_query.from_user.id)
    rpc_request_id = (await state.get_data()).get("rpc_request_id")
    data = callback_query.data

    if data.startswith("app_wallet:"):
        await state.update_data(selected_wallet=data.split(":")[1])
        await connect_wallet_window(state, callback_query.from_user.id)
    elif data == "main_menu":
        await wallet_connected_window(callback_query.from_user.id)
    elif data == "connect_wallet":
        await connect_wallet_window(state, callback_query.from_user.id)
    elif data == "disconnect_wallet":
        connector.add_event_kwargs(Event.DISCONNECT, state=state)
        await connector.disconnect_wallet()
    elif data == "cancel_transaction":
        if connector.is_transaction_pending(rpc_request_id):
            connector.cancel_pending_transaction(rpc_request_id)
        await wallet_connected_window(callback_query.from_user.id)
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
    elif callback_query.data == "swap_input":
        await swap_input_window(callback_query.from_user.id, state)
    elif data == "cancel":
        with suppress(Exception):
            await bot.delete_message(chat_id=callback_query.from_user.id, message_id=callback_query.message.message_id)
        await wallet_connected_window(callback_query.from_user.id)
    elif data == "swap_options":
        await state.update_data(prev_menu="swap")
        await swap_options_window(callback_query.from_user.id, state)
    elif data == "set_slippage":
        await state.set_state(SwapStates.setting_slippage)
        await bot.send_message(chat_id=callback_query.from_user.id, text="Enter slippage (positive number):")
    elif data == "set_max_splits":
        await state.set_state(SwapStates.setting_max_splits)
        await bot.send_message(chat_id=callback_query.from_user.id, text="Enter max splits (integer):")
    elif data == "set_max_length":
        await state.set_state(SwapStates.setting_max_length)
        await bot.send_message(chat_id=callback_query.from_user.id, text="Enter max length (integer):")
    elif data == "back":
        back_state = (await state.get_data()).get("back_state")
        if back_state == "swap_menu":
            await swap_menu_window(callback_query.from_user.id, state)
        else:
            await wallet_connected_window(callback_query.from_user.id)

    await callback_query.answer()


# ----------------------------
# Main Entry Point
# ----------------------------
async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
