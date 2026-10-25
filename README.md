# Vestibulares UDESC + UFSC — Multi-tenant

Aplicação web independente para simulados dos vestibulares **UDESC e UFSC**, com bancos, edições e resultados separados por universidade. A aplicação utiliza **Google Cloud Run** (ou Render) para FastAPI, **NeonDB PostgreSQL** para os dados estruturados e **Cloudflare R2** para os PDFs e imagens. Não possui vínculo oficial com a universidade.

Consulte [UPGRADE_UFSC.md](UPGRADE_UFSC.md) para a implementação UFSC, gabaritos PDF/HTML, fórmula oficial de pontuação objetiva, cor/modelo das provas e importação das edições 2019, 2024 e 2025.

## Funcionalidades

- Cadastro imediato com **e-mail e senha** (sem verificação de e-mail), login e logout.
- Sessão persistente por **30 dias**, armazenada no NeonDB com token aleatório em hash SHA-256 e cookie `HttpOnly`, `Secure` e `SameSite=Lax`.
- Isolamento de edições, gabaritos, questões, notas, simulados e histórico por conta. Usuários distintos podem cadastrar edições com o mesmo nome.
- Importação de PDFs, OCR para provas escaneadas, filtro de disciplinas/edições, correção automática e imagens privadas no R2.
- Interface responsiva; nenhuma funcionalidade de estudo acessível sem autenticação.
- **Contador de acertos por questão e por conta:** só aumenta quando um simulado é finalizado e a resposta coincide com o gabarito registrado para a tentativa. Questões anuladas e sem gabarito não contam.
- **Priorização automática:** ao criar um simulado, o sistema seleciona primeiro as questões com menos acertos, respeitando as edições/matérias selecionadas. A opção de embaralhar altera a apresentação e desempata questões com a mesma contagem, mas não elimina a prioridade.

## Implantação

1. Configure as variáveis do [`.env.example`](.env.example) no Cloud Run / Render. **Não configure mais `APP_USERNAME` e `APP_PASSWORD`: a autenticação agora utiliza contas individuais.**
2. Utilize o `Dockerfile` e mantenha a variável `PORT` fornecida pelo Cloud Run.
3. O `run.sh` executa `python -m scripts.migrate` antes do Uvicorn. Para produção com múltiplas instâncias, prefira migrations separadas como etapa de deploy.
4. Publique com HTTPS; o cookie `SESSION_COOKIE_SECURE` deve ficar como `true`. Não exponha `DATABASE_URL` ou chaves R2 no GitHub.
5. Acesse a URL publicada, escolha **Criar conta**, informe um e-mail e uma senha de **12 a 128 caracteres**. O cadastro já autentica a conta automaticamente.

### Migrations

- `001_initial.sql`: esquema original de questões e simulados.
- `002_multi_tenant_auth.sql`: tabelas `users`, `user_sessions`, colunas `user_id`, novos índices/uniqueness.
- `003_question_correct_count.sql`: coluna `questions.correct_count`, incluindo cálculo inicial com simulados históricos já concluídos.
- `004_ufsc_multi_exam.sql`: suporte simultâneo a UDESC/UFSC, cor da prova, respostas por somatório/abertas, pontuação objetiva e snapshots de correção.
- Execução: `python -m scripts.migrate` com `DATABASE_MIGRATION_URL` (conexão Neon direta) ou `DATABASE_URL`.

**A migração não atribui dados antigos à primeira pessoa que se cadastrar.** As edições e simulados existentes ficam sem proprietário e inacessíveis até serem explicitamente atribuídos por um administrador.

Após criar **a sua conta**, inspecione o que será atribuído:

```bash
python -m scripts.claim_legacy_data --email you@example.com
```

Se estiver correto, execute:

```bash
python -m scripts.claim_legacy_data --email you@example.com --execute
```

Esse comando exige acesso ao NeonDB e deve rodar **somente em ambiente administrativo**, com cuidado para não atribuir dados alheios à conta errada. Ele não substitui nem sobrescreve dados que já pertencem a outro usuário. Faça backup do banco antes.

### Importar via script independente

O arquivo `import_udesc.py` aceita a opção obrigatória `--owner-email` para gravações em NeonDB. A conta deve existir no site. Exemplo:

```bash
python import_udesc.py --owner-email you@example.com --label 2025/1 \
  --morning prova-matutina.pdf --afternoon prova-vespertina.pdf \
  --key gabarito.pdf --precache-images
```

O `--dry-run` funciona sem acessar o NeonDB/R2, e `--owner-email` não é necessário nesse modo.

### Como funciona o contador de acertos

- Cada questão começa com `correct_count = 0`.
- Responder ou salvar uma alternativa **não aumenta** a contagem. Somente o primeiro `POST /api/exams/{id}/finish` realizado com sucesso conta acertos; revisitar o resultado não duplica a contagem.
- Erros, questões em branco, anuladas e sem gabarito não incrementam.
- Repetir corretamente uma questão em outro simulado concluído incrementa mais uma vez.
- Excluir um simulado concluído desconta os acertos que ele havia registrado, sem alterar outros simulados. Excluir um simulado em andamento não altera as contagens.
- O valor pertence à questão daquela conta: dois usuários podem importar a mesma prova e terão contagens distintas.
- Ao criar um novo simulado, o filtro escolhido pelo usuário é respeitado; dentro do conjunto resultante, a seleção favorece sempre os menores valores de `correct_count`. O embaralhamento faz sorteios em empates e reorganiza apenas as questões já escolhidas.
- A migration 003 calcula os acertos retroativos dos simulados existentes, usando o gabarito congelado (`snapshot_correct`) em cada tentativa. Backups são recomendados antes da implantação.

As rotas continuam compatíveis com UDESC e `correct_count` aparece nas questões e nos simulados. As listagens aceitam `institution=UDESC|UFSC` (padrão UDESC). A importação recebe `institution`, `color` e dois gabaritos para UFSC; o simulador recebe `institution` no corpo JSON.

## Segurança e modelo de dados

Cada edição possui `editions.user_id`, cada simulado possui `attempts.user_id`; documentos, questões, notas e respostas são filhos dessas entidades. Todas as rotas de leitura, alteração e exclusão verificam o proprietário antes de retornar dados ou objetos do R2.

As senhas não são armazenadas em texto simples: utilizam **PBKDF2-HMAC-SHA256 com salt aleatório e 600 mil iterações**. Tokens de sessão nunca são armazenados em claro no banco. O frontend inclui proteção CSRF por cabeçalho e o backend valida a origem das escritas. Não existe envio ou verificação de e-mail, recuperação automática de senha nem painel administrativo.

Se desejar uma camada extra de isolamento para clientes com acesso direto ao banco, planeje políticas RLS do PostgreSQL em conjunto com um modelo de conexão que propague o identificador do usuário para cada transação. Esta versão implementa isolamento nas rotas da aplicação, não RLS.

## Considerações

- `SESSION_COOKIE_SECURE=true` pressupõe HTTPS; não desative em produção.
- PDF escaneado pode exigir OCR pesado; prefira o importador independente ou um worker dedicado, em vez de bloquear requisições do servidor.
- Aplicação de autenticação simples: não inclui confirmação de e-mail, redefinição de senha ou rate limiting distribuído. Para acesso público com usuários reais, configure proteção adicional no proxy/WAF (como rate limiting) e monitore tentativas de login.
- Objetos oficiais de provas podem estar sujeitos a direitos de terceiros; não são cobertos automaticamente pela licença do código.
