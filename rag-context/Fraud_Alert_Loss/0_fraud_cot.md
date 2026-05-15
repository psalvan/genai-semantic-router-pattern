CHAIN OF THOUGHT (Reasoning) RULES:
1. CLASSIFICATION & POLICY: Identify the Type (Lost, Stolen, Cloned). If matched, you MUST set 'Block' to true.
2. EXTRACTION: Scan the user's message specifically for card numbers. If they say "final 2345" or "cartão 1234", extract those numbers into the 'card_last_4digit' field.
3. DATA VALIDATION: 
   - Check if the 'card_last_4digit' field has been successfully extracted and filled. 
   - To perform a block, the system strictly requires these 4 digits.
4. EVIDENCE: Identify if Date or Amount were mentioned.
5. NEXT ACTION DECISION:
   - If 'card_last_4digit' is empty ("") -> set `next_action` to "NEED_MORE_INFO".
   - If 'card_last_4digit' has the 4 digits and 'Type' is present -> set `next_action` to "EXECUTE_TRANSACTION".