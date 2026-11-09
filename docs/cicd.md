# Integração e entrega contínuas

O workflow [cicd.yml](../.github/workflows/cicd.yml) roda em todo push, pull request e execução manual. Não há filtro por pasta: inclusive mudanças em documentação disparam as verificações. Commits da `main` são implantados automaticamente depois que os dois jobs de validação passam; outras branches e PRs executam somente CI.

## Fluxo

1. **Validação:** Python 3.11, lint, formatação, testes locais/AWS simulados, pacote reproduzível, comparação do catálogo gerado com o versionado e validação dos dois projetos Terraform.
2. **Integração:** testes PySpark e compatibilidade do SDK na imagem oficial Glue 5, sem rede dentro do contêiner e sem dados reais. Uma etapa separada executa um smoke com somente três linhas em memória, sem Excel ou Parquet, para detectar erros de expressões, colunas, tipos e SQL em Silver/Gold. A compilação e a importação dos três scripts sem iniciar Glue são verificadas no job Python.
3. **Deploy da main:** autenticação OIDC, conferência da conta, espera por uma janela sem cargas/fila pendentes, plano/aplicação Terraform e atualização das views Athena.
4. **Conferência e rastreabilidade:** versão dos três jobs, Workflow e Lambda e presença das tabelas; arquivo do commit completo em `s3://<artifacts_bucket>/commits/<sha>/source.tar.gz`; resultado do deploy nos artefatos do GitHub Actions.

O processamento de dados continua sendo disparado pelo upload de um Excel no S3. Um commit publica o código e a infraestrutura, sem reenviar automaticamente a base ou gerar uma nova fotografia econômica.

## Identidade e estado

O GitHub assume `github-poc-test-deploy` por até duas horas. A confiança exige audiência `sts.amazonaws.com` e o subject exato `repo:lucasbraz-repos@246191059/poc_test@1450611105:ref:refs/heads/main`. Esse é o formato com IDs imutáveis usado pelo GitHub para este repositório novo. PRs, forks e outras branches não têm autorização para assumir essa role. Não são usados secrets com chaves AWS permanentes. As actions estão fixadas pelo SHA de suas versões oficiais.

As permissões de implantação se limitam aos recursos do projeto e ao arquivo de estado/trava. A role não administra seu próprio trust nem o bucket de estado. O bootstrap desses recursos é separado da infraestrutura aplicada pelo CI, para que um deploy comum não precise alterar sua própria identidade.

O estado da pipeline fica no S3, conforme `infra/terraform/backend.hcl`, com criptografia, versionamento, bloqueio de acesso público e lock nativo S3. A migração preserva um backup local privado em `build/aws/` e confere recursos e outputs após a cópia (a migração pode gerar um novo lineage no backend). O bootstrap continua com estado administrativo local, ignorado pelo Git; preserve esse estado em armazenamento administrativo protegido.

Configuração inicial/reexecução administrativa:

```bash
python3 tools/bootstrap_cicd.py --profile poc-aws --account-id 909492399516 --plan-only
python3 tools/bootstrap_cicd.py --profile poc-aws --account-id 909492399516
```

Os nomes da conta, repositório, branch, região e backend estão explícitos. Para outro ambiente, revisar esses valores, as permissões e o estado; não reutilizar o backend de uma conta diferente.

## Operação

O deploy é serializado no GitHub e protegido pelo lock do Terraform. Se houver cargas ou mensagens pendentes, aguarda até 40 minutos e falha sem trocar a release caso a janela não apareça. Não cancelar um deploy durante `terraform apply`. O teste de ociosidade é uma verificação, não um bloqueio atômico de uploads externos; evite novas entradas durante a implantação. Uma falha no apply pode deixar alterações parciais: corrigir a causa e repetir o workflow, que usa o mesmo estado remoto.

O cancelamento de execuções em andamento está desativado. O GitHub pode substituir uma execução pendente por um push mais recente no mesmo grupo; nesse caso, a versão mais recente da branch é a que será validada/implantada. Não há promessa de implantar cada estado intermediário de uma sequência rápida de pushes.

O resultado contém o commit, o hash da biblioteca, o checksum do arquivo de código e as conferências realizadas. Estado Terraform, planos, credenciais e dados de devedores não são enviados como artefatos do GitHub. O plano permanece no runner, cuja duração é temporária.

Depois de um deploy que muda a release, atualize a configuração local para enviar arquivos com a versão nova:

```bash
aws s3 cp s3://poc-carteira-dev-909492399516-487d65-artifacts/deployments/current.json build/aws/deployment.json --profile poc-aws
python3 tools/enviar_aws.py /caminho/BaseTeste.xlsx --profile poc-aws
```

O código completo do commit fica privado no S3; dados de entrada continuam fora do Git. Para rollback, reverta o commit pela revisão habitual e envie o revert à `main`. Isso publica a configuração anterior pelo mesmo CI/CD; não desfaz cargas já publicadas nem restaura dados apagados por uma alteração destrutiva.

Referências: [OIDC GitHub/AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws), [backend S3 e lock](https://developer.hashicorp.com/terraform/language/backend/s3).
