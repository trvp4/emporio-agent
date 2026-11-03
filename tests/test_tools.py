"""The agent's tools, exercised without any LLM."""

import json

from emporio.core.tools import TOOLS, execute


def run(tool, **args):
    result = execute(tool, args)
    json.dumps(result)  # every result must be JSON-serializable
    return result


# --- registry ---


def test_every_tool_has_a_valid_schema():
    assert len(TOOLS) == 7
    for tool in TOOLS:
        assert tool.input_schema["type"] == "object"
        assert tool.description


def test_unknown_tool_and_bad_arguments_return_errors():
    assert "error" in run("does_not_exist")
    assert "error" in run("get_order", order_id="abc")


# --- search_products ---


def test_violoes_up_to_1000_only_lists_sellable_products():
    r = run("search_products", category="violões", max_price=1000, limit=20)
    ids = {p["product_id"] for p in r["results"]}
    assert r["total_matches"] == 12
    assert 96 not in ids  # out of stock
    assert 113 not in ids  # discontinued
    assert r["unavailable_hidden"] == 2
    prices = [p["price"] for p in r["results"]]
    assert prices == sorted(prices)


def test_search_by_model_name():
    r = run("search_products", query="Takamine GD20")
    assert r["results"][0]["product_id"] == 95
    assert r["results"][0]["price_text"] == "R$ 2.199,00"


def test_search_shows_active_promotion():
    r = run("search_products", query="Crafter HT-100")
    top = r["results"][0]
    assert top["product_id"] == 90
    assert top["promotion"]["discount_percent"] == 18
    assert top["promotion"]["original_price_text"] == "R$ 2.399,00"
    assert top["price_text"] == "R$ 1.967,18"
    assert top["pix_discount_applies"] is False


def test_empty_category_says_so():
    r = run("search_products", category="saxofone")
    assert r["results"] == []
    assert "não temos produtos" in r["message"]


def test_unknown_category():
    r = run("search_products", category="pandeiro")
    assert "Não encontrei a categoria" in r["message"]


# --- get_product ---


def test_get_product_by_name():
    r = run("get_product", name="Takamine GD20")
    assert r["product_id"] == 95
    assert r["pix_price_text"] == "R$ 2.089,05"
    assert r["can_be_sold"] is True


def test_sold_out_product_suggests_alternatives():
    r = run("get_product", product_id=96)
    assert r["can_be_sold"] is False
    assert r["availability"] == "esgotado no momento"
    alts = r["alternatives"]
    assert len(alts) == 3
    assert all(a["availability"].startswith("disponível") for a in alts)
    assert alts[0]["product_id"] == 107  # Shelby SGD-195E, R$ 799,00


def test_expired_promotions_are_reported_not_applied():
    r = run("get_product", product_id=96)
    assert {p["name"] for p in r["expired_promotions"]} == {"Semana do Músico"}
    assert "promotion" not in r


def test_discontinued_product_has_no_price():
    r = run("get_product", product_id=113)
    assert r["availability"] == "descontinuado"
    assert "price_text" not in r


# --- get_order (identity verification) ---


def test_order_requires_contact():
    r = run("get_order", order_id=8)
    assert r["verified"] is False
    assert "e-mail ou o telefone" in r["message"]


def test_wrong_contact_reveals_nothing():
    wrong = run("get_order", order_id=8, email="someone@else.com")
    missing = run("get_order", order_id=999, email="someone@else.com")
    assert wrong == missing  # same answer whether the order exists or not


def test_order_with_email():
    r = run("get_order", order_id=8, email="AnaCarol.Ferreira@coldmail.com")
    assert r["verified"] is True
    assert r["customer_first_name"] == "Ana"
    assert r["status"] == "enviado"
    assert r["tracking_code"] == "BRJL5544332BR"
    assert r["late"] is True
    assert r["days_late"] == 25
    assert r["items"][0]["category"] == "Ukuleles"  # so it isn't called a guitar


def test_order_with_phone_in_any_format():
    r = run("get_order", order_id=8, phone="67 99654 7821")
    assert r["verified"] is True


def test_shared_email_only_opens_own_orders():
    # customers 11 and 43 share an e-mail; order 10 belongs to customer 11
    assert run("get_order", order_id=10, email="bruno.martins@jmail.com")["verified"]


def test_cancelled_order_shows_reason():
    r = run("get_order", order_id=18, email="gustavo.nunes@inlook.com")
    assert r["status"] == "cancelado"
    assert "fora de estoque" in r["notes"]


# --- check_return_eligibility ---


def test_return_after_withdrawal_window():
    r = run(
        "check_return_eligibility", order_id=7, email="leticia.rocha@jmail.com",
        reason="arrependimento",
    )  # fmt: skip
    assert r["verified"] is True
    assert r["options"][0]["eligible"] is False
    assert r["options"][0]["deadline"] == "24/02/2026"


def test_return_requires_verification():
    r = run("check_return_eligibility", order_id=7, email="x@y.com", reason="defeito")
    assert r["verified"] is False


# --- simulate_payment ---


def test_payment_for_product_rejects_10x():
    r = run("simulate_payment", product_id=107, installments=10)
    assert r["requested_installments_allowed"] is False
    assert r["credit_max"] == "7x de R$ 114,14 sem juros"
    assert r["pix_text"] == "R$ 759,05"


def test_payment_with_promotion_has_no_pix_discount():
    r = run("simulate_payment", product_id=121)  # Ohana CK-20, 20% off
    assert r["amount_text"] == "R$ 439,20"
    assert r["pix_text"] == "R$ 439,20"
    assert r["pix_discount_applies"] is False


# --- store_info ---


def test_store_info_open_now():
    r = run("store_info")
    assert r["open_now"] is True  # Wednesday 10:30
    assert r["phone_whatsapp"] == "(67) 3341-4444"


def test_store_info_shipping():
    metro = run("store_info", city="Campo Grande", order_value=429.90)
    assert metro["shipping"]["fee_text"] == "R$ 35,00"
    other = run("store_info", city="Dourados", order_value=429.90)
    assert other["shipping"]["fee_text"] == "calculado pelo CEP"


# --- escalate_to_human ---


def test_escalation_creates_ticket_with_business_hours_deadline():
    r = run(
        "escalate_to_human",
        category="pedido_atrasado",
        summary="Pedido 8 com entrega prevista para 28/02 ainda não chegou.",
        order_id=8,
    )
    assert r["ticket_id"].startswith("EM-260325-")
    # Wed 10:30 + 24 business hours (Wed 7.5h, Thu 9h, Fri 7.5h) -> Fri 16:30
    assert r["response_deadline"] == "27/03/2026 às 16:30"


# --- fixes from the code review ---


def test_name_lookup_needs_a_complete_unambiguous_match():
    assert run("get_product", name="Gibson SG")["found"] is False  # not the Les Paul
    assert run("get_product", name="Fender Jazz Bass")["found"] is False
    ambiguous = run("get_product", name="Yamaha")
    assert ambiguous["found"] is False
    assert len(ambiguous["candidates"]) > 1


def test_brands_match_the_product_name_not_the_description():
    # #139 "Yamaha Kit 1" is described as a Pearl; #140 "Pearl Kit 2" is described as a Tama.
    assert run("get_product", name="Pearl")["product_id"] == 140
    names = [p["name"] for p in run("search_products", query="Fender", limit=20)["results"]]
    assert names and all("Fender" in n for n in names)
    assert "description_warning" in run("get_product", product_id=135)


def test_contrabaixo_means_electric_bass_unless_acoustic():
    assert run("search_products", category="contrabaixo")["category"] == "Baixos"
    assert run("search_products", category="contrabaixo acústico")["category"] == (
        "Cordas Orquestrais"
    )


def test_phone_with_area_code_must_match_it():
    assert run("get_order", order_id=8, phone="(67) 99654-7821")["verified"] is True
    assert run("get_order", order_id=8, phone="+55 67 99654-7821")["verified"] is True
    assert run("get_order", order_id=8, phone="(11) 99654-7821")["verified"] is False


def test_no_payment_plan_for_a_product_that_cannot_be_sold():
    r = run("simulate_payment", product_id=136)  # discontinued
    assert r["can_be_sold"] is False
    assert "credit_options" not in r


def test_city_with_state_suffix_is_campo_grande():
    for city in ("Campo Grande - MS", "Campo Grande/MS", "campo grande, ms"):
        assert run("store_info", city=city, order_value=429.90)["shipping"]["fee_text"] == (
            "R$ 35,00"
        )


def test_large_instruments_get_the_special_shipping_note():
    r = run("store_info", city="Dourados", product_ids=[139])  # acoustic drum kit
    assert any("grande porte" in n for n in r["shipping"]["notes"])


def test_budget_with_no_results_says_what_exists():
    r = run("search_products", category="guitarra", max_price=1000)
    assert r["results"] == []
    assert "R$ 5.400,00" in r["message"]  # the cheapest guitar
    assert r["closest_outside_range"][0]["price_text"] == "R$ 5.400,00"


def test_searching_one_unsellable_product_brings_its_alternatives():
    r = run("search_products", query="Giannini GF-3D")
    assert r["requested_product"]["availability"] == "esgotado no momento"
    assert r["requested_product"]["alternatives"][0]["product_id"] == 107  # Shelby SGD-195E
    assert "preço mais próximo" in r["requested_product"]["alternatives_basis"]
