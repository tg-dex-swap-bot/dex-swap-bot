from typing import Optional
from decimal import Decimal
import time

from SwapCoffeeAPI import get_swap_route, get_prepared_transaction
from tonutils.tonconnect.models import SendTransactionResponse, Transaction, Message


class TransactionException(Exception):
    """Base exception for transaction-related errors"""
    pass


async def create_swap_transaction(
    connector,
    sender_address: str,
    route: dict,
    slippage: float = 0.05,
    mev_protection: bool = True
) -> Optional[SendTransactionResponse]:
    """
    Создает и отправляет транзакцию свопа.

    Args:
        connector: Объект соединения с TON-кошельком.
        sender_address (str): Адрес отправителя.
        route (dict): Ответ от CoffeeAPI о маршруте транзакции.
        slippage (float): Допустимое проскальзывание.
        mev_protection (bool): Флаг защиты от MEV-атак.

    Returns:
        Optional[SendTransactionResponse]: Ответ от кошелька.

    Raises:
        TransactionException: Если произошла ошибка при создании транзакции
    """
    try:
        prepared_transaction = get_prepared_transaction(
            sender_address,
            slippage,
            route["paths"],
            mev_protection
        )

        tx_data = prepared_transaction["transactions"][0]
        message = Message(
            address=tx_data["address"],
            amount=str(tx_data["value"]),
            payload=str(tx_data["cell"])
        )

        tx = Transaction(
            valid_until=int(time.time()) + 600,
            messages=[message]
        )

        response = await connector.send_transaction(tx)
        return response

    except Exception as e:
        raise TransactionException(f"Ошибка при создании транзакции: {str(e)}")


async def test_swap_transaction(connector) -> Optional[SendTransactionResponse]:
    """
    Тестовая функция для выполнения свопа с заданными тестовыми параметрами.

    Returns:
        Optional[SendTransactionResponse]: Ответ от кошелька или None.

    Raises:
        TransactionException: Если произошла ошибка при выполнении теста
    """
    try:
        print("Запуск тестовой транзакции...")

        sender_address = "UQCf_BAw_HyPyF1YMNT_jQLHYbPGaKAQcaAyoMjx2qdAy2wb"
        input_token = "EQCvxJy4eG8hyHBFsZ7eePxrRsUQSFE_jpptRAYBmcG_DOGS"
        output_token = "EQB420yQsZobGcy0VYDfSKHpG2QQlw-j1f_tPu1J488I__PX"
        amount = Decimal("1.0")

        route = get_swap_route(
            input_token,
            output_token,
            float(amount),
            is_input=True
        )

        result = await create_swap_transaction(
            connector=connector,
            sender_address=sender_address,
            route=route
        )

        if result:
            print(f"Тест успешен! Результат: {result}")
        else:
            print("Тест завершился безуспешно (None).")

        return result

    except Exception as e:
        raise TransactionException(f"Ошибка при выполнении теста: {str(e)}")