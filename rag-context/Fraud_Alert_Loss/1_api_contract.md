# Contratos de Integração de API (Backend Services)

Este documento define os requisitos mínimos obrigatórios para que uma ação passe do estado de coleta de dados para a execução no backend (`next_action` = `EXECUTE_TRANSACTION`). Se qualquer campo obrigatório estiver ausente, a ação deve ser `NEED_MORE_INFO`.

## Módulo de Prevenção a Fraudes (Security)

### API: `blockcard(card_last_4digit, reason)`
* **Descrição:** Bloqueia fisicamente e virtualmente um cartão comprometido.
* **Requisitos Mínimos:**
  * `card_last_4digit`: Os 4 últimos dígitos do cartão afetado. O banco precisa saber *qual* cartão bloquear.
  * `reason`: Motivo do bloqueio (Mapeado do campo `Type`: Lost, Stolen, Cloned).
* **Regra de Transição:** Se o usuário disser apenas "clonaram meu cartão", a API não pode ser chamada pois falta o identificador. O LLM deve pedir os 4 últimos dígitos antes de executar a transação.