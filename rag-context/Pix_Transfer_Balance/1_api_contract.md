# Contratos de Integração de API (Backend Services)

Este documento define os requisitos mínimos obrigatórios para que uma ação passe do estado de coleta de dados para a execução no backend (`next_action` = `EXECUTE_TRANSACTION`). Se qualquer campo obrigatório estiver ausente, a ação deve ser `NEED_MORE_INFO`.

## Módulo PIX (Transferências e Recebimentos)

### API: `transfer_pix_now(amount, destination)`
* **Descrição:** Executa uma transferência PIX imediata (Operação: Pay).
* **Requisitos Mínimos:**
  * `Amount`: Valor validado e convertido para minor units (inteiro > 0).
  * `PIX`: Chave PIX de destino válida (`destination`).
* **Regra de Transição:** Se faltar o Valor ou a Chave, o fluxo NÃO PODE chamar a API. O LLM deve solicitar o dado faltante.

### API: `receive_pix_qrcode(amount)`
* **Descrição:** Gera um QR Code ou link de cobrança PIX (Operação: Receive).
* **Requisitos Mínimos:**
  * `Amount`: Valor validado e convertido para minor units. (Opcionalmente, pode ser gerado sem valor, mas o padrão do banco exige o valor para fechar a cobrança).