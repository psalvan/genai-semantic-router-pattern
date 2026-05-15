# SAM: cada função usa Metadata BuildMethod makefile; alvo build-<LogicalId>.
# Região por defeito sa-east-1 (alinhado com samconfig.toml).
_SHARED := $(abspath $(CURDIR)/shared)

.PHONY: build-WhatsappWebhookTrigger build-IntentProcessor build-LlmHandler build-PostLlmConsumer build-DispatchWpp
.PHONY: deploy deploy-bootstrap publish-semantic-router sync-rag-context ssm-secrets ssm-string-params sync-smart-router-key

build-WhatsappWebhookTrigger:
	mkdir -p "$(ARTIFACTS_DIR)"
	cp -R "$(_SHARED)/." "$(ARTIFACTS_DIR)/"
	cp -f "$(CURDIR)/functions/whatsapp-trigger/app.py" "$(ARTIFACTS_DIR)/"

build-IntentProcessor:
	mkdir -p "$(ARTIFACTS_DIR)"
	cp -R "$(_SHARED)/." "$(ARTIFACTS_DIR)/"
	cp -f "$(CURDIR)/functions/intent-processor/app.py" "$(ARTIFACTS_DIR)/"

build-LlmHandler:
	mkdir -p "$(ARTIFACTS_DIR)"
	cp -R "$(_SHARED)/." "$(ARTIFACTS_DIR)/"
	cp -f "$(CURDIR)/functions/llm-handler/app.py" "$(ARTIFACTS_DIR)/"
	cp -f "$(CURDIR)/functions/llm-handler"/*.json "$(ARTIFACTS_DIR)/"

build-PostLlmConsumer:
	mkdir -p "$(ARTIFACTS_DIR)"
	cp -R "$(_SHARED)/." "$(ARTIFACTS_DIR)/"
	cp -f "$(CURDIR)/functions/post-llm/app.py" "$(ARTIFACTS_DIR)/"

build-DispatchWpp:
	mkdir -p "$(ARTIFACTS_DIR)"
	cp -R "$(_SHARED)/." "$(ARTIFACTS_DIR)/"
	cp -f "$(CURDIR)/functions/dispatch-wpp/app.py" "$(ARTIFACTS_DIR)/"

SAM_DEPLOY_FLAGS ?=

# ENVIRONMENT / AWS_* a partir de .env quando existir (alinhado com scripts Python).
# Variáveis já definidas no ambiente ou em `make VAR=...` mantêm-se (comportamento ?=).
ifneq (,$(wildcard $(CURDIR)/.env))
_DOTENV_ENVIRONMENT := $(shell sed -n 's/^ENVIRONMENT=[[:space:]]*//p' "$(CURDIR)/.env" 2>/dev/null | head -n1 | tr -d '\r')
_DOTENV_AWS_REGION := $(shell sed -n 's/^AWS_REGION=[[:space:]]*//p' "$(CURDIR)/.env" 2>/dev/null | head -n1 | tr -d '\r')
_DOTENV_AWS_PROFILE := $(shell sed -n 's/^AWS_PROFILE=[[:space:]]*//p' "$(CURDIR)/.env" 2>/dev/null | head -n1 | tr -d '\r')
endif
ENVIRONMENT ?= $(if $(_DOTENV_ENVIRONMENT),$(_DOTENV_ENVIRONMENT),dev)
STACK_NAME ?= $(ENVIRONMENT)-nvidia-demo
AWS_REGION ?= $(if $(_DOTENV_AWS_REGION),$(_DOTENV_AWS_REGION),sa-east-1)
AWS_PROFILE ?= $(_DOTENV_AWS_PROFILE)

# parameter_overrides do samconfig com Environment= alinhado a $(ENVIRONMENT) (CLI substitui o bloco inteiro no SAM).
SAM_PO_DEFAULT := $(shell python3 "$(CURDIR)/scripts/sam_parameter_overrides.py" default --environment "$(ENVIRONMENT)")
SAM_PO_PHASE1 := $(shell python3 "$(CURDIR)/scripts/sam_parameter_overrides.py" deploy-router-phase1 --environment "$(ENVIRONMENT)")
SAM_PO_PHASE2 := $(shell python3 "$(CURDIR)/scripts/sam_parameter_overrides.py" deploy-router-phase2 --environment "$(ENVIRONMENT)")

export AWS_PROFILE
export ENVIRONMENT
export STACK_NAME
export AWS_REGION

deploy:
	sam build && \
	python3 scripts/sync_smart_router_key.py --skip-if-empty && \
	python3 scripts/assert_ec2_network_params.py "$(SAM_PO_DEFAULT)" && \
	sam deploy --stack-name "$(STACK_NAME)" --parameter-overrides "$(SAM_PO_DEFAULT)" $(SAM_DEPLOY_FLAGS) && \
	bash scripts/publish_semantic_router.sh && \
	python3 scripts/ssm_put_string_params_from_env.py

deploy-bootstrap:
	sam build && \
	python3 scripts/sync_smart_router_key.py --skip-if-empty && \
	sam deploy --config-env deploy-router-phase1 --stack-name "$(STACK_NAME)" --parameter-overrides "$(SAM_PO_PHASE1)" $(SAM_DEPLOY_FLAGS) && \
	bash scripts/publish_semantic_router.sh && \
	python3 scripts/assert_ec2_network_params.py "$(SAM_PO_PHASE2)" && \
	sam deploy --config-env deploy-router-phase2 --stack-name "$(STACK_NAME)" --parameter-overrides "$(SAM_PO_PHASE2)" $(SAM_DEPLOY_FLAGS) && \
	python3 scripts/ssm_put_string_params_from_env.py

publish-semantic-router:
	bash scripts/publish_semantic_router.sh

sync-rag-context:
	@BUCKET="$$(aws cloudformation describe-stacks --stack-name "$(STACK_NAME)" --region "$(AWS_REGION)" \
		--query "Stacks[0].Outputs[?OutputKey=='RagContextBucketName'].OutputValue" --output text)"; \
	if [ -z "$$BUCKET" ] || [ "$$BUCKET" = "None" ]; then echo "Defina STACK_NAME, AWS_REGION e credenciais (ex. AWS_PROFILE)." >&2; exit 1; fi; \
	echo "Sincronizar rag-context/ -> s3://$$BUCKET/context/"; \
	aws s3 sync "$(CURDIR)/rag-context/" "s3://$$BUCKET/context/" --region "$(AWS_REGION)" --delete

ssm-secrets:
	python3 scripts/ssm_put_secrets_from_env.py

ssm-string-params:
	python3 scripts/ssm_put_string_params_from_env.py

sync-smart-router-key:
	python3 scripts/sync_smart_router_key.py
