# Atualização — contador de acertos e priorização de questões

## Arquivos modificados

- `migrations/003_question_correct_count.sql` (novo): adiciona o campo persistente `questions.correct_count` e faz backfill de simulados finalizados.
- `app.py`: atualização transacional e idempotente do contador ao finalizar; decremento ao excluir simulado; bloqueio de linha para evitar finalizar/responder simultaneamente; seleção sempre por menos acertos.
- `static/app.js`: exibe o contador no banco de questões, detalhe e tela do simulado; explica seleção por prioridade.
- `tests/test_multi_tenant.py`: adaptador de teste com o campo/SQL novo.
- `tests/test_correct_count.py` (novo): testes de finalização, repetição, isolamento, prioridade, anuladas e exclusão.
- `README.md`: documentação atualizada.

## Deploy (Google Cloud Run + NeonDB)

1. Faça backup do banco NeonDB.
2. Integre as alterações no repositório GitHub; preserve as mudanças que você já fez após a última versão multi-tenant.
3. Execute a migration `003_question_correct_count.sql` usando `python -m scripts.migrate` (o `run.sh` do projeto já executa migrations antes de iniciar o Uvicorn).
4. Publique uma nova revisão no Cloud Run e abra o site. A contagem histórica será incorporada na primeira migração.
5. Crie um simulado com apenas 1 questão e confirme que as questões com contagem zero são escolhidas antes das com contagem positiva.

**Observação:** se fizer deploy do código antes de executar a migration 003, as rotas que leem o contador falharão até o esquema ser atualizado. Use o fluxo automático de migrations ou execute a migration antes de publicar o novo código.

## Regras

- A contagem é exclusiva de cada usuário; nunca usa acertos de outras contas.
- O embaralhamento não interfere na prioridade de seleção: desempata contagens iguais e embaralha a ordem de apresentação.
- As respostas são avaliadas pelo `snapshot_correct` salvo no momento da criação do simulado, não por alterações posteriores do gabarito.
- Questões removidas de uma edição não estão mais disponíveis para seleção; o histórico mantém os snapshots.
- Excluir um simulado concluído reduz o contador daquela tentativa, enquanto existir a questão original.

## Validação

- `PYTHONPATH=. python -m pytest -q`
- `node --check static/app.js`

Os testes automatizados utilizam um banco de teste simulado; **a execução contra seu NeonDB real não foi realizada**.
