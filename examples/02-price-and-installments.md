# Price and installments, with a policy rule applied

**What it shows:** A price query (suggested in the brief), then an installment request the minimum-installment rule (manual 3.1) refuses, with the best allowed option instead.

_Real transcript, not edited. Model `claude-haiku-5-5`, generated on 2026-11-03 with `uv run python -m examples.generate`. Store clock pinned to Wednesday 2026-03-25 10:30, right after the dataset's last order._

---

**Cliente:** Quanto custa o Takamine GD20?

**Lia:** Oi! O Takamine GD20 Dreadnought Natural está por **R$ 2.199,00**, e no PIX sai por **R$ 2.089,05**.

Ele tem tampo sólido de cedro, fundo e laterais em sapele, braço em mogno e escala de 650mm. É um violão ótimo para fingerstyle, com timbre quente e definido. Temos 5 unidades em estoque.

Quer que eu simule o parcelamento no cartão ou tem mais alguma dúvida?

<details><summary>Behind this answer: get_product</summary>

- `get_product` `{"name": "Takamine GD20"}`
- Firewall: 3 values checked, all grounded.
- 2 model calls, 2.5 s.

</details>

**Cliente:** E o Shelby SGD-195E, dá pra fazer em 10x?

**Lia:** O Shelby SGD-195E Elétrico Aço Sunburst custa **R$ 799,00**, e no PIX sai por **R$ 759,05**. Ele tem captação e afinador integrados, e temos 6 unidades em estoque.

Em 10x não dá: a parcela mínima nessa faixa é R$ 100,00, então o máximo é **7x de R$ 114,14 sem juros** no cartão.

Quer que eu veja outra opção de parcelamento ou tem mais alguma dúvida?

<details><summary>Behind this answer: get_product, simulate_payment</summary>

- `get_product` `{"name": "Shelby SGD-195E"}`
- `simulate_payment` `{"product_id": 107, "installments": 10}`
- Firewall: 7 values checked, all grounded.
- 3 model calls, 3.0 s.

</details>
