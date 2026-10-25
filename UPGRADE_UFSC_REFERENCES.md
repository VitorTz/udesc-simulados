# Atualização — referências cruzadas UFSC

Esta alteração usa **NeonDB PostgreSQL + Cloudflare R2** e preserva os bancos separados de UDESC e UFSC.

## Mudanças

- `ufsc.py`: localiza textos de apoio que antecedem a primeira questão ou aparecem após uma questão anterior. Relaciona referências pelo número explicitado no enunciado, independentemente da seleção de questões no simulado. Reconhece textos lado a lado.
- `migrations/005_ufsc_shared_references.sql`: acrescenta `reference_clips_json` em `questions`. **Não modifica** as migrations anteriores.
- `app.py`: salva referências na importação UFSC; expõe metadados na biblioteca e no simulado; entrega imagens pelo endpoint autenticado `GET /api/questions/{id}/reference/{ref_index}/{clip_index}`.
- `static/app.js` e `static/styles.css`: exibem o material de apoio antes do enunciado, preservando imagens e tabelas e sem obrigar a incluir a questão anterior.
- `scripts/backfill_ufsc_references.py`: recupera referências de provas UFSC já importadas, usando PDFs armazenados no R2, sem recriar questões nem alterar respostas, notas, tentativas, gabaritos ou pontuações.

## Instalação

1. Faça backup do banco NeonDB.
2. Atualize os arquivos de aplicação e adicione a migration `005`.
3. Execute `python -m scripts.migrate` no ambiente com as variáveis `DATABASE_URL` e, opcionalmente, `DATABASE_MIGRATION_URL` configuradas. Se o `run.sh` já executa migrations na inicialização, o deploy também aplica a nova migration.
4. Atualize o projeto no GitHub e publique a nova revisão no Cloud Run.
5. Execute a recuperação das edições já existentes **fora do fluxo HTTP**:

```bash
python -m scripts.backfill_ufsc_references              # mostra o que mudaria
python -m scripts.backfill_ufsc_references --execute    # persiste no NeonDB
```

Ou somente uma edição: `python -m scripts.backfill_ufsc_references --edition-id 123 --execute`.

O script utiliza o `.env` na raiz do projeto, mantém isolamento por conta e não precisa reimportar os PDFs para o R2.

## Testes com o PDF enviado

UFSC 2025, Prova 1 azul:

- Q01 e Q02 → Texto 1 (resenha na página 1 do PDF)
- Q03 → Texto 2 (canção na página 2)
- Q04 → Texto 3 (canção visual na página 3)
- Q05 → Textos 4 e 5 (charge e depoimento na página 4)

Na prova 2024/azul, os Textos 5 e 6 aparecem em duas colunas, e seus recortes são separados.

## Limitações

Este reconhecimento automático usa títulos explícitos `Texto N`/`Text N` e referências numéricas na pergunta. Textos sem título, relações visuais implícitas ou figuras com legendas não padronizadas podem exigir validação manual. Em qualquer caso, o usuário ainda pode abrir a página original no visualizador do PDF.
