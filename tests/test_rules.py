"""Policy rules, checked against the numbers in the manual."""

from datetime import date, datetime

import pytest

from emporio.config import STORE_TZ
from emporio.core import rules

TODAY = date(2026, 3, 25)


# --- money ---


def test_brl_format():
    assert rules.brl(1299) == "R$ 1.299,00"
    assert rules.brl(2089.05) == "R$ 2.089,05"
    assert rules.brl(114.142857) == "R$ 114,14"


def test_pix_discount_without_promotion(store):
    q = rules.price_quote(store.products[95], None)  # Takamine GD20
    assert q.price == 2199.00
    assert q.pix_price == 2089.05
    assert q.pix_discount_applies


def test_promotion_does_not_stack_with_pix(store):
    promo = store.active_promotion(90)  # Crafter HT-100, 18% off
    q = rules.price_quote(store.products[90], promo)
    assert q.list_price == 2399.00
    assert q.price == 1967.18
    assert q.pix_price == q.price
    assert not q.pix_discount_applies


# --- installments (3.1) ---


@pytest.mark.parametrize(
    ("amount", "n", "allowed"),
    [
        (159.90, 3, True),  # 53.30 >= 50
        (149.00, 3, False),  # 49.67 < 50
        (149.00, 1, True),  # 1x always allowed
        (480.00, 6, True),  # 80.00 >= 80
        (479.00, 6, False),
        (799.00, 7, True),  # 114.14 >= 100
        (799.00, 8, False),  # 99.88 < 100
        (1200.00, 12, True),
        (5000.00, 13, False),
    ],
)
def test_installment_minimums(amount, n, allowed):
    assert rules.installment_allowed(amount, n) is allowed


def test_max_installments():
    assert rules.max_installments(799.00) == 7
    assert rules.max_installments(699.90) == 6  # 7x would be 99.99
    assert rules.max_installments(120.00) == 2
    assert rules.max_installments(11499.00) == 12


def test_simulation_rejects_10x_for_799():
    sim = rules.simulate_payment(799.00, requested_installments=10)
    assert sim.requested_allowed is False
    assert sim.credit_max_installments == 7
    assert sim.credit_options[-1].value == 114.14
    assert any("10x não é possível" in n for n in sim.notes)


def test_combined_payment_only_above_2000():
    assert not rules.simulate_payment(2000.00).combined_payment_allowed
    assert rules.simulate_payment(2199.00).combined_payment_allowed


# --- shipping (5) ---


def test_metro_shipping():
    assert rules.shipping_quote("Campo Grande", 600).fee == 0
    assert rules.shipping_quote("campo grande", 499.90).fee == 35
    assert rules.shipping_quote("Campo Grande", 500).fee == 35  # "acima de 500" is strict


def test_other_cities_are_quoted_by_cep():
    q = rules.shipping_quote("Dourados", 3000, {3})
    assert q.region == "other"
    assert q.fee is None
    assert any("grande porte" in n for n in q.notes)


# --- opening hours (2) ---


def _at(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=STORE_TZ)


def test_open_on_weekday():
    assert rules.store_status(_at(2026, 3, 25, 10, 30)).is_open  # Wednesday


def test_closed_after_hours_points_to_next_day():
    s = rules.store_status(_at(2026, 3, 25, 19, 0))
    assert not s.is_open
    assert s.next_opening == "quinta (26/03) às 09:00"


def test_saturday_closes_at_13():
    assert rules.store_status(_at(2026, 3, 28, 12, 59)).is_open
    s = rules.store_status(_at(2026, 3, 28, 13, 0))
    assert not s.is_open
    assert s.next_opening == "segunda (30/03) às 09:00"


def test_closed_on_holidays():
    s = rules.store_status(_at(2026, 4, 3, 10, 0))  # Good Friday 2026
    assert not s.is_open
    assert "Sexta-feira Santa" in s.reason
    assert not rules.store_status(_at(2026, 11, 2, 10, 0)).is_open  # Finados


def test_before_opening_today():
    s = rules.store_status(_at(2026, 3, 25, 7, 0))
    assert s.next_opening == "hoje às 09:00"


# --- returns and warranty (4, 8) ---


def test_withdrawal_expired_but_warranty_still_valid(store):
    order = store.orders[7]  # delivered 17/02, bought 03/02
    regret = rules.assess_return(order, "arrependimento", TODAY)
    assert regret.options[0].eligible is False
    assert regret.options[0].deadline == "24/02/2026"

    defect = rules.assess_return(order, "defeito", TODAY)
    store_exchange, warranty = defect.options
    assert store_exchange.eligible is False
    assert store_exchange.deadline == "05/03/2026"
    assert warranty.eligible is True
    assert warranty.deadline == "18/05/2026"


def test_withdrawal_within_seven_days(store):
    order = store.orders[7]
    a = rules.assess_return(order, "arrependimento", date(2026, 2, 20))
    assert a.options[0].eligible
    assert a.options[0].days_left == 4


def test_not_delivered_orders_have_no_return_window(store):
    a = rules.assess_return(store.orders[13], "arrependimento", TODAY)  # pending
    assert a.options == []
    assert "ainda não foi entregue" in a.next_step


def test_cancelled_order(store):
    a = rules.assess_return(store.orders[17], "defeito", TODAY)
    assert a.options == []
    assert "cancelado" in a.next_step


def test_installment_minimum_is_checked_before_rounding():
    assert not rules.installment_allowed(149.99, 3)  # 49,996 per installment
    assert rules.installment_allowed(150.00, 3)


def test_more_than_12_installments_says_the_real_limit():
    sim = rules.simulate_payment(5000, requested_installments=15)
    assert sim.requested_allowed is False
    assert any("vai até 12x" in n for n in sim.notes)


def test_closed_withdrawal_does_not_suggest_another_open_window(store):
    # Order 7: withdrawal closed on 24/02; the defect exchange also closed (05/03). The next
    # step must send the agent to check the defect case, not offer it as still open.
    a = rules.assess_return(store.orders[7], "arrependimento", TODAY)
    assert "reason=defeito" in a.next_step
    assert "30 dias" not in a.next_step


def test_closed_withdrawal_states_its_own_deadline(store):
    a = rules.assess_return(store.orders[7], "arrependimento", TODAY)
    assert "terminou em 24/02/2026" in a.next_step
