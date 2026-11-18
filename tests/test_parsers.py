from decimal import Decimal

from helpers import SITE, load_fixture
from shopping_deal_agent.models import RawProduct
from shopping_deal_agent.parse import ProductParser
from shopping_deal_agent.parse.css import selectors_for
from shopping_deal_agent.parse.dom import make_selector
from shopping_deal_agent.parse.embedded import parse_embedded
from shopping_deal_agent.parse.jsonld import parse_jsonld
from shopping_deal_agent.parse.css import parse_css
from shopping_deal_agent.parse.parser import _merge


def test_jsonld_strategy():
    raw = parse_jsonld(make_selector(load_fixture("adidas_verified_deal.html")))
    assert raw is not None
    assert raw.price == Decimal("42.00")
    assert raw.currency == "SGD"
    assert raw.confidence == 0.95
    assert raw.sizes == {"XS": True, "S": True, "M": False}


def test_embedded_next_data_strategy():
    raw = parse_embedded(make_selector(load_fixture("embedded_next_data.html")))
    assert raw is not None
    assert raw.strategy == "embedded"
    assert raw.price == Decimal("39.50")
    assert raw.original_price == Decimal("79.00")
    assert raw.currency == "SGD"
    assert raw.sizes["XS"] is False
    assert raw.sizes["S"] is True


def test_css_fallback_strategy():
    raw = parse_css(make_selector(load_fixture("css_only.html")), SITE)
    assert raw is not None
    assert raw.price == Decimal("25.00")
    assert raw.original_price == Decimal("50.00")
    assert raw.currency == "SGD"
    assert raw.sizes["S"] is False
    assert raw.sizes["XS"] is True


def test_parser_merges_fields_and_finds_promotions():
    raw = ProductParser(SITE).parse(load_fixture("adidas_verified_deal.html"))
    assert raw.strategy == "jsonld"
    assert raw.price == Decimal("42.00")
    assert raw.original_price == Decimal("65.00")
    assert raw.price_candidates == (Decimal("42.00"),)
    assert any(promo.applicable for promo in raw.promotions)
    assert "Black" in raw.colors


def test_parser_flags_conflicting_prices():
    raw = ProductParser(SITE).parse(load_fixture("adidas_ambiguous.html"))
    assert len(raw.price_candidates) == 2
    assert any("conflicting prices" in note for note in raw.notes)


def test_merge_never_blends_original_price_across_strategies():
    primary = RawProduct(
        price=Decimal("42.00"), original_price=None, strategy="jsonld", confidence=0.95
    )
    other = RawProduct(
        price=Decimal("42.00"), original_price=Decimal("999.00"), strategy="css", confidence=0.60
    )
    merged = _merge(primary, [other])
    assert merged.original_price is None


def test_parser_does_not_blend_original_price_from_a_lower_confidence_strategy():
    """JSON-LD is the authoritative, highest-confidence strategy and states no
    list price here; a last-resort CSS match elsewhere on the page must never
    be allowed to silently fabricate a discount JSON-LD never stated."""
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type": "Product", "name": "Test Shoe",
     "offers": {"@type": "Offer", "price": "42.00", "priceCurrency": "SGD"}}
    </script>
    </head><body>
    <h1>Test Shoe</h1>
    <span class="price">S$42.00</span>
    <span class="price--original">S$999.00</span>
    </body></html>
    """
    raw = ProductParser(SITE).parse(html)
    assert raw.strategy == "jsonld"
    assert raw.price == Decimal("42.00")
    assert raw.original_price is None
    assert any("not used" in note for note in raw.notes)


def test_site_adapter_is_selected_by_domain():
    selectors = selectors_for("www.adidas.com.sg")
    assert ".size-selector button" in selectors["sizes"]
    assert "h1" in selectors["title"]
