# Late order: tracking plus a ticket for the team

**What it shows:** A real-time order lookup. The order is 25 days past its delivery estimate, so the agent apologizes, shares the tracking code and opens a ticket with a deadline counted in business hours.

_Real transcript, not edited. Model `claude-haiku-5-5`, generated on 2026-11-02 with `uv run python -m examples.generate`. Store clock pinned to Wednesday 2026-03-25 10:30, right after the dataset's last order._

---

**Cliente:** Oi, meu pedido 8 ainda não chegou e já passou muito do prazo.

**Lia:** Oi! Sinto muito pela demora, sei como é ansioso esperar um instrumento que já deveria ter chegado.

Para eu consultar o pedido 8 com segurança, preciso que você me informe o e-mail ou o telefone usado na compra.

<details><summary>Behind this answer: no tools</summary>

- No tool call: answered from the prompt (persona, manual, store context).
- Firewall: no prices, percentages, installments or stock to check.
- 1 model call, 1.8 s.

</details>

**Cliente:** anacarol.ferreira@coldmail.com

**Lia:** Oi, Ana! Peço muitas desculpas: o pedido 8 (Kala KA-C Concert Mogno, R$ 349,90) já está com 25 dias de atraso em relação à previsão de 28/02/2026. Ele está com status "enviado".

Você pode acompanhar a entrega pelo código de rastreio **BRJL5544332BR**, no site dos Correios ou da Jadlog.

Já abri o chamado **EM-260325-001** para a equipe. O retorno está previsto para até **27/03/2026 às 16:30**. Se preferir falar direto com a loja, o WhatsApp é (67) 3341-4444.

Posso ajudar em mais alguma coisa?

<details><summary>Behind this answer: get_order, escalate_to_human</summary>

- `get_order` `{"order_id": 8, "email": "anacarol.ferreira@coldmail.com"}`
- `escalate_to_human` `{"category": "pedido_atrasado", "contact": "anacarol.ferreira@coldmail.com", "customer_name": "Ana", "order_id": 8, "summary": "Pedido 8 (Kala KA-C Concert Mogno, qtd 1, R$ 349,90, pago no cartão de débito) está com status \"enviado\", previsão de entrega 28/02/2026 e 25 dias de atraso. Rastreio BRJL5544332BR. Cliente pede previsão e solução."}`
- Firewall: 1 value checked, all grounded.
- 3 model calls, 4.3 s.

</details>
