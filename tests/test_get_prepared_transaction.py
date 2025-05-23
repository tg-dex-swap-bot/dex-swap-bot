import random

from src import SwapCoffeeAPI
from tests.test_get_swap_route import tokens


def test_get_prepared_transaction(tokens):
    amount = 0.00000001

    token_in, token_out = tokens

    route = SwapCoffeeAPI.get_swap_route(
        token_in["address"]["address"],
        token_out["address"]["address"],
        amount,
        2,
        3,
        True
    )

    SwapCoffeeAPI.get_prepared_transaction(
        "UQCNTO0Nh0Z7QNyRW1BLWfk08f2dAOw4izrx9sO6OUPg4DoV",
        0.05,
        route["paths"]
    )


