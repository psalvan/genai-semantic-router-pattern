# Diretrizes do Sistema de Pagamentos Instantâneos (PIX)

## 1. Conversão Numérica Padrão (Minor Units / Centavos)
* Todos os sistemas de backend do MyZelo Bank processam valores financeiros estritamente em **unidades menores (centavos)** para evitar erros de ponto flutuante.
* **Regra de Conversão:** O valor extraído da intenção do usuário deve ser multiplicado por 100.
* *Exemplo Prático:* Se o cliente solicitar uma transferência de "100 reais", "R$ 100", ou usar gírias como "100 conto" ou "100 pila", o valor (Amount) enviado no JSON deve ser o número inteiro `10000`. Um envio de "R$ 50,50" será `5050`.

## 2. Agendamento de Transações (TargetDate)
* **Transação Imediata (mode: "now"):** Qualquer solicitação de PIX ou pagamento que não mencione explicitamente uma data futura, ou que use termos como "agora", "já", "hoje", deve ser processada como imediata. O campo `scheduled_at` deve ficar vazio.
* **Transação Agendada (mode: "scheduled"):** Se o cliente usar termos de tempo futuro (ex: "amanhã", "dia 15", "semana que vem"), a operação deve ser marcada como agendada, convertendo o texto do cliente para o formato ISO 8601 no campo `scheduled_at` sempre que possível.

## 3. Direção da Operação (Operation)
* **Pay (Pagar):** Ocorre quando o cliente é o emissor do dinheiro. Ele quer "fazer um PIX", "enviar", "transferir", "pagar". O campo `PaymentForTo` representa quem vai receber o dinheiro.
* **Receive (Receber/Cobrar):** Ocorre quando o cliente gera um QR Code ou link para cobrar alguém. Termos como "gerar cobrança", "receber de", "manda um link de pagamento". O campo `PaymentForTo` representa quem vai pagar.

## 4. Chaves Aceitas (PIX Key)
* O sistema de roteamento deve extrair a chave PIX, que pode ser o CPF/CNPJ (apenas números), e-mail, telefone celular (idealmente com DDI +55) ou chave aleatória (EVP).