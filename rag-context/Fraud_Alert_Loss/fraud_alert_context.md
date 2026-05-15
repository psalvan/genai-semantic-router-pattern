# Procedimentos Operacionais de Risco e Prevenção a Fraude (SOP-001)

## 1. Classificação de Canais de Risco (FraudOn)
Ao registrar uma ocorrência de segurança, o analista ou sistema deve identificar a origem do comprometimento:
* **WebApp / BankApp:** Aplicações digitais acessadas via desktop ou dispositivo móvel (celular).
* **Card:** Cartão físico de crédito ou débito de plástico.
* **Contactless_Accessory / TokenWatchPhone:** Dispositivos vestíveis (smartwatches), carteiras digitais (Apple Pay, Google Pay) ou tags de pagamento.

## 2. Tipologia de Ocorrências (Type)
* **Lost (Perda):** O cliente extraviou o dispositivo ou cartão, sem indícios imediatos de crime.
* **Stolen (Roubo/Furto):** O cliente foi vítima de assalto, furto qualificado ou coação.
* **Cloned (Clonagem):** O dispositivo físico está com o cliente, mas transações não reconhecidas online ou físicas estão ocorrendo.

## 3. Protocolo de Bloqueio Obrigatório (Block Policy)
* **Bloqueio Imediato (Block = true):** É estritamente obrigatório aplicar bloqueio preventivo imediato sempre que o cliente relatar perda (Lost), roubo (Stolen), ou suspeita de clonagem (Cloned). Palavras-chave do cliente como "fui roubado", "perdi", "não reconheço" ou "clonaram" exigem o bloqueio compulsório.
* **Exceção (Block = false):** Apenas se o cliente estiver fazendo uma consulta genérica sobre os procedimentos de segurança, sem relatar um incidente ocorrido.

## 4. Coleta de Evidências (Amount & Date)
* O sistema deve registrar o valor (Amount) da fraude informada pelo cliente, caso exista uma transação não reconhecida específica.
* O horário ou data do incidente (Date) relatado pelo cliente deve ser catalogado para averiguação nas câmeras ou logs de rede.