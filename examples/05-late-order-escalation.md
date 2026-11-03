# Late order: tracking plus a ticket for the team

**What it shows:** A real-time order lookup. The order is 25 days past its delivery estimate, so the agent apologizes, shares the tracking code and opens a ticket with a deadline counted in business hours.

_Real transcript, not edited. Model `claude-haiku-5-5`, generated on 2026-11-03 with `uv run python -m examples.generate`. Store clock pinned to Wednesday 2026-03-25 10:30, right after the dataset's last order._

---

**Cliente:** Oi, meu pedido 8 ainda não chegou e já passou muito do prazo.

**Lia:** Oi! Sinto muito pelo atraso no seu pedido. Vou verificar a situação para você.

Para consultar o pedido 8, preciso que você me informe o **e-mail ou telefone** usado na compra. Assim que confirmar, eu busco o status e o rastreamento.

<details><summary>Behind this answer: no tools</summary>

- No tool call: answered from the prompt (persona, manual, store context).
- Firewall: no prices, percentages, installments, stock or dates to check.
- 1 model call, 1.8 s.

</details>

**Cliente:** anacarol.ferreira@coldmail.com

**Lia:** Ana, peço muitas desculpas. O pedido 8 já está com 25 dias de atraso em relação à previsão de entrega, que era 28/02/2026. Isso não deveria ter acontecido.

O status atual é **enviado**, e o código de rastreamento é **BRJL5544332BR**. Você pode acompanhar no site dos Correios ou da Jadlog.

Já abri o chamado **EM-260325-001** para a equipe de pedidos. A resposta chega em até 24 horas úteis, até **27/03/2026 às 16:30**. Se preferir falar direto com a loja, o WhatsApp é (67) 3341-4444.

Posso ajudar com mais alguma coisa?

<details><summary>Behind this answer: get_order, escalate_to_human</summary>

- `get_order` `{"order_id": 8, "email": "anacarol.ferreira@coldmail.com"}`
- `escalate_to_human` `{"category": "pedido_atrasado", "contact": "anacarol.ferreira@coldmail.com", "customer_name": "Ana", "order_id": 8, "summary": "Pedido 8 (Kala KA-C Concert Mogno, 1 unidade, R$ 349,90, pago no cartão de débito) está com status \"enviado\" e atrasado. Data de entrega estimada: 28/02/2026, já com 25 dias de atraso. Rastreio: BRJL5544332BR. Cliente pede previsão de entrega e solução."}`
- Firewall: 2 values checked, all grounded.
- 3 model calls, 5.0 s.

</details>
