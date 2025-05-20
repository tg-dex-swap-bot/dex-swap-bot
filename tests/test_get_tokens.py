from src import SwapCoffeeAPI

def test_get_tokens():
    token_list = SwapCoffeeAPI.get_tokens()

    assert any(token_list)