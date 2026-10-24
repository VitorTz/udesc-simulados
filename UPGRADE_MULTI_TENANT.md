# Atualização para a versão multiusuário

Estas alterações foram feitas sobre o pacote FastAPI + PostgreSQL + R2 anteriormente entregue. Se você modificou o GitHub depois disso, faça o merge das alterações em vez de sobrescrever seu código sem revisão.

## Antes de publicar

1. Faça backup do PostgreSQL (NeonDB) e confira suas credenciais R2.
2. Publique o código e execute as migrations **sem modificar** `001_initial.sql`. A nova migration é `002_multi_tenant_auth.sql`.
3. No Cloud Run, mantenha `DATABASE_URL` e `DATABASE_MIGRATION_URL`, as quatro variáveis R2 e `SESSION_COOKIE_SECURE=true`.
4. Remova as variáveis antigas `APP_USERNAME` e `APP_PASSWORD`, que agora são ignoradas.
5. Após o deploy, abra o site, entre na aba **Criar conta** e registre seu próprio e-mail e senha. O cadastro ativa a conta imediatamente.

## Como recuperar seus dados antigos

O novo código mantém as edições/questões, anotações e simulados anteriores no banco, mas **não atribui automaticamente** esses dados a quem acabou de se registrar. Eles permanecem invisíveis até receberem explicitamente um proprietário.

Em um terminal administrativo com a URL do NeonDB:

```bash
python -m scripts.claim_legacy_data --email voce@exemplo.com
```

O primeiro comando mostra quais registros não têm dono. Após revisar o destino, execute:

```bash
python -m scripts.claim_legacy_data --email voce@exemplo.com --execute
```

As operações são feitas em uma transação PostgreSQL e preservam notas, simulados, gabaritos e referências dos PDFs originais. O comando não assume registros já associados a outras contas.

**Importante:** se já tiver criado uma edição com o mesmo nome na nova conta, a transferência da edição legada poderá produzir um conflito de unicidade. Resolva o conflito de forma explícita antes de executar o comando.

## Importar novos PDFs por um script separado

O importador `import_udesc.py` foi atualizado para exigir o dono da edição:

```bash
python import_udesc.py --owner-email voce@exemplo.com --label 2025/1 \
  --morning matutino.pdf --afternoon vespertino.pdf --key gabarito.pdf --precache-images
```

É necessário que a conta já exista no site. O script continua lendo o `.env` existente. Para apenas validar PDFs, use `--dry-run` (sem credenciais ou dono obrigatórios).

## Autenticação e sessões

- Cadastro livre com e-mail e senha de 12 a 128 caracteres.
- Senha com PBKDF2-HMAC-SHA256, salt aleatório e 600.000 iterações.
- Cookie assinado? **Não:** cookie contém apenas token aleatório criptograficamente seguro; seu hash é armazenado no banco.
- O cookie é `HttpOnly`, `Secure`, `SameSite=Lax` e tem validade de 30 dias.
- Cada conta tem seus próprios PDFs/questões, anotações, simulados e histórico. Os caminhos de novos PDFs no R2 começam com `users/<user_id>/`.
- Sem verificação de e-mail, recuperação automática de senha ou painel de administração.

## Limitações de produção

- Não foram aplicadas políticas Row Level Security (RLS) no NeonDB; a aplicação aplica escopo do proprietário em todas as rotas. Não permita acesso direto de usuários finais à string de conexão do banco.
- Configure proteção a ataques de força bruta e automações abusivas no proxy/WAF ou acrescentando rate limiting persistente.
- O importador de PDFs digitalizados ainda pode exigir processamento pesado; use o script independente ou um worker.
- Valide o deploy real no seu próprio Cloud Run, NeonDB e R2 antes de abrir para outras pessoas.
