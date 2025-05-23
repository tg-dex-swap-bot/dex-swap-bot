import pytest
from src import SwapCoffeeAPI


@pytest.fixture(scope="session")
def tokens():
    token_list = SwapCoffeeAPI.get_tokens()

    token_in = next((token for token in token_list if token["metadata"]["symbol"] == "tsTON"), None)
    token_out = next((token for token in token_list if token["metadata"]["symbol"] == "USDT"), None)

    return token_in, token_out


def test_get_swap_route_for_input(tokens):
    amount = 0.001

    token_in, token_out = tokens

    route = SwapCoffeeAPI.get_swap_route(
        token_in["address"]["address"],
        token_out["address"]["address"],
        amount,
        2,
        3,
        True
    )

    assert route["input_amount"] == amount
    assert route["input_token"] == token_in
    assert route["output_token"] == token_out


def test_get_swap_route_for_output(tokens):
    amount = 0.00000001

    token_in, token_out = tokens

    route = SwapCoffeeAPI.get_swap_route(
        token_in["address"]["address"],
        token_out["address"]["address"],
        amount,
        2,
        3,
        False
    )

    assert route["output_amount"] == amount
    assert route["input_token"] == token_in
    assert route["output_token"] == token_out


@pytest.mark.xfail(strict=True)
def test_get_swap_route_unknown_token(tokens):
    amount = 0.00000001

    token_in = tokens[0]

    route = SwapCoffeeAPI.get_swap_route(
        token_in["address"]["address"],
        "abc",
        amount,
        2, 
        3,
        True
    )


