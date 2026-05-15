# Demo semantic-router (FastAPI on EC2)

Serviço HTTP always-on com **FastAPI** + **uvicorn**, pensado para correr numa **EC2 t4g.small** (Graviton / **ARM64**) com **Ubuntu 24.04** e volume raiz **20 GiB gp3**.

## Deploy com SAM (recomendado)

O `template.yaml` na raiz do repositório inclui a EC2, um **Elastic IP**, um bucket **S3** para artefactos (`{Environment}-nvidia-demo-semantic-router-artifacts-…`), security group, perfil IAM (SSM Session Manager, leitura S3, **leitura do parâmetro** `SMART_ROUTER_KEY` no SSM) e o parâmetro SSM **`/{Environment}/nvidia-demo/SMART_ROUTER_URL`** preenchido automaticamente com `http://<EIP>:8000/check-intent`.

1. No `sam deploy`, passa **obrigatoriamente** `SemanticRouterVpcId` e `SemanticRouterPublicSubnetId` (subnet pública com rota para Internet Gateway). Vê comentários em `samconfig.toml`. O parâmetro **`SemanticRouterEc2Enabled`** (`true` / `false`) controla se o stack cria a EC2 e o EIP no mesmo deploy; ver abaixo.

2. **Primeiro deploy do stack (ou CI que precisa de S3 povoado antes da EC2):** na raiz do repo corre **`make deploy-bootstrap`**. Isto faz: `sam deploy` com `SemanticRouterEc2Enabled=false` (bucket de artefactos + resto da infra **sem** EC2) → `publish-semantic-router` para o S3 → segundo `sam deploy` com `SemanticRouterEc2Enabled=true` (sobe a EC2; o primeiro boot do user-data já vê `main.py` no bucket) → parâmetros String no SSM. Os ambientes `deploy-router-phase1` e `deploy-router-phase2` estão em [`samconfig.toml`](../../samconfig.toml).

3. **Atualizações seguintes** (Lambdas, template, código do router): **`make deploy`** — um único `sam deploy` com `SemanticRouterEc2Enabled=true` (default no `samconfig`) **não** remove a EC2; em seguida publica o router no S3 e aplica parâmetros String.

4. Se só alterares o router sem `make deploy`: `make publish-semantic-router` (ajusta `STACK_NAME` / `AWS_REGION` se necessário). Na **primeira** instância que arrancou com bucket vazio (legado), o user-data não repete com reboot: substitui a instância ou corre `aws s3 sync` + `bootstrap.sh` na máquina (instalação manual).

5. Publica **`SMART_ROUTER_KEY`** para o SSM a partir do `.env` na raiz do repo: `make sync-smart-router-key`. A instância **lê o parâmetro** em cada arranque do serviço (`ExecStartPre` → `demo-router-sync-ssm-env.sh`). Depois de alterares a chave no SSM, na EC2: `sudo systemctl restart demo-router` (ou reboot).

**Não uses em simultâneo** o stack SAM e o Terraform abaixo para o mesmo `environment`: o nome do bucket S3 colide.

## Deploy alternativo com Terraform

A pasta [`terraform/`](terraform/) define a mesma arquitetura (S3, EC2, EIP, SG, IAM) de forma autónoma. Variáveis: `environment`, `vpc_id`, `public_subnet_id`, etc.

- `terraform init` e `terraform apply` a partir de `services/semantic-router/terraform/`.
- O output `smart_router_url_for_ssm` é o valor a colocar em **`/{environment}/nvidia-demo/SMART_ROUTER_URL`** se não estiveres a usar o stack SAM.
- O user-data usa `user-data-terraform.tpl` (sync S3 + `bootstrap.sh`), alinhado com o que o CloudFormation injeta no SAM.

## Layout em produção

| Caminho | Conteúdo |
|--------|----------|
| `/opt/demo-router/main.py` | App FastAPI (`GET /health`, `POST /check-intent`) |
| `/opt/demo-router/requirements.txt` | Dependências pinadas |
| `/opt/demo-router/venv/` | Virtualenv (não commits) |
| `/etc/systemd/system/demo-router.service` | Unit systemd |
| `/etc/demo-router/instance.env` | `DEMO_ENVIRONMENT`, `DEMO_AWS_REGION` (criado no user-data) |
| `/etc/demo-router.env` | Preenchido por `/usr/local/sbin/demo-router-sync-ssm-env.sh` a partir do SSM (`chmod 600`) |

O unit define `HF_HOME` / `TRANSFORMERS_CACHE` em `/opt/demo-router/.cache/huggingface` para cache previsível; monitoriza disco (`sentence-transformers` + **torch** consomem bastante espaço em disco e RAM).

## Parameter Store e chave (SSM)

Com **SAM**, `SMART_ROUTER_URL` é gerido pelo stack. Sem SAM, define manualmente:

```bash
aws ssm put-parameter \
  --name "/dev/nvidia-demo/SMART_ROUTER_URL" \
  --type String \
  --value "http://203.0.113.10:8000/check-intent" \
  --overwrite \
  --region us-east-2
```

**Publicar `SMART_ROUTER_KEY`** a partir do `.env` (só Parameter Store; a EC2 obtém o valor via IAM):

```bash
make sync-smart-router-key
# ou: python3 scripts/sync_smart_router_key.py
```

A instância precisa do agente SSM para **Session Manager** (`AmazonSSMManagedInstanceCore`) e das permissões extra no stack para **ler** `.../SMART_ROUTER_KEY` e **KMS Decrypt**.

## Segurança (Security Group)

- **22/tcp**: restringe com `SemanticRouterSshCidr` (SAM) ou `ssh_ingress_cidr` (Terraform).
- **8000/tcp**: público (`0.0.0.0/0`) para as Lambdas chamarem o endpoint; restringe na app com **Bearer** (`SMART_ROUTER_KEY`).

## RAM e disco

**t4g.small** tem **2 GiB RAM**. Carregar modelos com `sentence-transformers` pode causar **OOM**; se necessário, usa **t4g.medium** ou modelos mais pequenos. **20 GiB** ajuda em `pip install` e cache Hugging Face.

## Instalação manual: `bootstrap.sh`

Na instância (com esta pasta já copiada), como **root**:

```bash
sudo ./bootstrap.sh
```

Ou: `sudo ./bootstrap.sh /caminho/para/semantic-router`

Em lançamentos **sem** o user-data do stack, cria antes `/etc/demo-router/instance.env` com `DEMO_ENVIRONMENT` e `DEMO_AWS_REGION` (mesmo formato que o user-data do SAM), para o `demo-router-sync-ssm-env.sh` resolver o nome do parâmetro.

O script: `apt` → cria `/opt/demo-router` e `.cache` → `python3 -m venv` → `pip install` → instala o unit e o sync SSM → `systemctl enable --now`.

## User data local / S3 manual

O ficheiro [`user-data.sh`](user-data.sh) serve para lançamentos manuais: define `ARTIFACT_BUCKET` e `ARTIFACT_PREFIX` se publicares para S3, ou deixa o bucket vazio e faz **scp** + `bootstrap.sh` (igual ao fluxo descrito acima).

## Verificação

```bash
sudo systemctl status demo-router
curl -sS http://127.0.0.1:8000/health
```

## Dependências Python

Ver `requirements.txt` (pins). O pacote PyPI **`semantic-router`** (Aurelio) é distinto do nome desta pasta no repositório.
