from emporio.agent.grounding import build_evidence, check_answer
from emporio.agent.prompt import policies_text
from emporio.core.tools import execute


def evidence_for(*tool_results, user=()):
    return build_evidence(tool_results, policies_text(), user)


def test_values_copied_from_tools_pass():
    ev = evidence_for(execute("get_product", {"product_id": 95}))
    report = check_answer("O Takamine GD20 sai por R$ 2.199,00, ou R$ 2.089,05 no PIX.", ev)
    assert report.ok
    assert report.checked == 2


def test_invented_price_is_caught():
    ev = evidence_for(execute("get_product", {"product_id": 95}))
    report = check_answer("Ele custa R$ 1.999,00 hoje.", ev)
    assert not report.ok
    assert report.unsupported == ["R$ 1.999,00"]


def test_computed_savings_are_caught():
    # 2.399,00 - 1.967,18 = 431,82: true, but computed by the model, not by a tool.
    ev = evidence_for(execute("get_product", {"product_id": 90}))
    report = check_answer("De R$ 2.399,00 por R$ 1.967,18, economia de R$ 431,82!", ev)
    assert report.unsupported == ["R$ 431,82"]


def test_promotion_percent_must_exist():
    ev = evidence_for(execute("get_product", {"product_id": 90}))
    assert check_answer("Está com 18% de desconto.", ev).ok
    assert not check_answer("Está com 40% de desconto.", ev).ok


def test_policy_numbers_are_trusted():
    ev = evidence_for()
    assert check_answer("No PIX você tem 5% de desconto, em até 12x sem juros.", ev).ok
    assert check_answer("Frete grátis acima de R$ 500,00.", ev).ok


def test_installments_and_stock():
    ev = evidence_for(execute("simulate_payment", {"product_id": 107, "installments": 10}))
    assert check_answer("Dá pra fazer em 7x de R$ 114,14.", ev).ok
    assert not check_answer("Dá pra fazer em 8x de R$ 99,88.", ev).ok

    ev = evidence_for(execute("get_product", {"product_id": 95}))
    assert check_answer("Temos 5 unidades.", ev).ok
    assert not check_answer("Temos 9 unidades.", ev).ok


def test_customer_words_count_as_evidence():
    ev = evidence_for(user=["Quero parcelar em 10x"])
    assert check_answer("Em 10x não consigo, infelizmente.", ev).ok


def test_answer_without_numbers_passes():
    assert check_answer("Oi! Como posso te ajudar?", evidence_for()).ok
