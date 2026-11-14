from decimal import Decimal

from shopping_deal_agent.money import (
    currencies_match,
    discount_pct,
    format_money,
    parse_money,
    price_candidates,
)


def test_symbol_and_code():
    result = parse_money("S$45.00")
    assert result.value == Decimal("45.00")
    assert result.currency == "SGD"
    assert result.ambiguous is False

    assert parse_money("SGD 45").value == Decimal("45")
    assert parse_money("S$1,234.50").value == Decimal("1234.50")
    assert parse_money("45,00", expected_currency="SGD").value == Decimal("45.00")
    assert parse_money("45,00").ambiguous is True


def test_bare_dollar_needs_expected_currency():
    assert parse_money("$45.00").currency is None
    assert parse_money("$45.00").ambiguous is True
    assert parse_money("$45.00", expected_currency="SGD").currency == "SGD"


def test_price_range_is_ambiguous():
    result = parse_money("S$45.00 - S$60.00")
    assert result.value is None
    assert result.ambiguous is True
    assert result.candidates == (Decimal("45.00"), Decimal("60.00"))


def test_multiple_currencies_are_ambiguous():
    result = parse_money("S$45.00 / US$33.00")
    assert result.ambiguous is True
    assert "multiple currencies" in result.reason


def test_candidates_and_discount():
    assert price_candidates("Now S$42.00 was S$65.00") == (
        Decimal("42.00"),
        Decimal("65.00"),
    )
    assert discount_pct(Decimal("42"), Decimal("65")) == Decimal("35.38")
    assert discount_pct(Decimal("70"), Decimal("65")) is None
    assert discount_pct(None, Decimal("65")) is None


def test_format_and_match():
    assert format_money(Decimal("45"), "SGD") == "S$45.00"
    assert format_money(None, "SGD") == "n/a"
    assert currencies_match("SGD", "sgd")
    assert not currencies_match("SGD", "USD")
    assert not currencies_match(None, "SGD")
