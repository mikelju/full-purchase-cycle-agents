# Full Purchase Cycle Agents

Interim README of phase 13 while the showcase is built; the full operating manual is [docs/guide.md](docs/guide.md).

## Evaluation results

<!-- results-table:start -->
| Suite | Cases | Main metric | Value (95% Wilson) | Gate |
|---|---|---|---|---|
| order_line_extraction | 1500 | product_accuracy | 99.5% [99.0, 99.7] | PASS |
| web_form_matching | 468 | line_product_accuracy | 99.6% [98.5, 99.9] | PASS |
| email_order_extraction | 234 | intake_accuracy | 99.1% [96.9, 99.8] | PASS |
| clarification_detection | 150 | recall_ambiguous | 100.0% [93.5, 100.0] | PASS |
| clarification_answers | 156 | resolution_accuracy | 99.5% [97.2, 99.9] | PASS |
| whatsapp_order_extraction | 120 | intake_accuracy | 97.5% [92.9, 99.1] | PASS |
| channel_routing | 47 | route_accuracy | 100.0% [92.4, 100.0] | PASS |
| failure_recovery | 34 | scenario_success | 100.0% [89.8, 100.0] | PASS |
| order_scenarios | 30 | scenario_success | 100.0% [88.6, 100.0] | PASS |
| quote_wait | 24 | scenario_success | 100.0% [86.2, 100.0] | PASS |
| goods_receipt | 40 | scenario_success | 100.0% [91.2, 100.0] | PASS |
| offer_extraction | 90 | offer_accuracy | 100.0% [95.9, 100.0] | PASS |
| quote_decision | 39 | scenario_success | 100.0% [91.0, 100.0] | PASS |
| reconciliation_matching | 58 | case_success | 100.0% [93.8, 100.0] | PASS |
| document_extraction | 90 | kind_accuracy | 100.0% [95.9, 100.0] | PASS |
| claim_approval | 35 | scenario_success | 100.0% [90.1, 100.0] | PASS |
| claim_drafting | 60 | reference_accuracy | 100.0% [94.0, 100.0] | PASS |
| sales_reconciliation | 46 | scenario_success | 100.0% [92.3, 100.0] | PASS |
| customer_response | 40 | scenario_success | 100.0% [91.2, 100.0] | PASS |
| response_drafting | 60 | reference_accuracy | 100.0% [94.0, 100.0] | PASS |
| purchase_cycle | 16 | scenario_success | 100.0% [80.6, 100.0] | PASS |
<!-- results-table:end -->

## License

[MIT](LICENSE)
