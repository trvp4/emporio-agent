# Price and installments, with a policy rule applied

**What it shows:** A price query (suggested in the brief), then an installment request the minimum-installment rule (manual 3.1) refuses, with the best allowed option instead.

_Real transcript, not edited. Model `claude-haiku-5-5`, generated on 2026-11-02 with `uv run python -m examples.generate`. Store clock pinned to Wednesday 2026-03-25 10:30, right after the dataset's last order._

---

**Cliente:** Quanto custa o Takamine GD20?

**Lia:** Oi! O Takamine GD20 Dreadnought Natural está por **R$ 2.199,00**, ou **R$ 2.089,05** no PIX.

Ele tem tampo de cedro sólido, fundo e laterais em sapele e braço em mogno, com timbre quente e definido, ótimo para fingerstyle. Está disponível, com 5 unidades em estoque.

Quer que eu veja as opções de parcelamento no cartão ou mais alguma coisa para você?

<details><summary>Behind this answer: get_product</summary>

- `get_product` `{"name": "Takamine GD20"}`
- Firewall: 3 values checked, all grounded.
- 2 model calls, 2.1 s.

</details>

**Cliente:** E o Shelby SGD-195E, dá pra fazer em 10x?

**Lia:** Não dá para fazer em 10x. Para esse valor, a parcela mínima de 7x a 12x é R$ 100,00, então o máximo é **7x de R$ 114,14 sem juros**.

O Shelby SGD-195E Elétrico Aço Sunburst está por **R$ 799,00**, ou **R$ 759,05** no PIX. Ele tem captação e afinador integrado, boa opção de custo-benefício para quem precisa de amplificação. Está disponível, com 6 em estoque.

Posso ajudar com mais alguma coisa?

<details><summary>Behind this answer: get_product, simulate_payment</summary>

- `get_product` `{"name": "Shelby SGD-195E"}`
- `simulate_payment` `{"installments": 10, "product_id": 0}`
- `simulate_payment` `{"product_id": 107, "installments": 10}`
- Firewall: 9 values checked, all grounded.
- 3 model calls, 3.1 s.

</details>
