# Atualização 004 — UFSC + UDESC

Este pacote adapta a **versão multiusuário que já tinha a migration 003**. Faz backup do NeonDB antes de implantar. O importador UDESC de 2019, 2024, 2025 e 2026 continua disponível.

## Novidades

- Seletor **UDESC / UFSC** na barra lateral. Bancos de questões, edições, dashboard, histórico e notas apresentados separadamente, mantendo o isolamento por usuário.
- UFSC organizada por `ano/semestre`, cor/modelo e **Prova 1 / Prova 2**. Possibilidade de manter várias cores do mesmo vestibular na mesma conta, sem misturar os números nem os gabaritos.
- Importação de PDFs de questões e dos respectivos gabaritos **PDF** (2024, 2025) ou **HTML** (2019). A importação rejeita conflitos conhecidos de cor e dia. Exclui a segunda língua da Prova 1 (questões 13–20).
- Resposta UFSC por sete opções de somatório **01, 02, 04, 08, 16, 32 e 64** (o número disponível depende da questão), ou resposta numérica aberta de 00 a 99.
- Correção oficial **das questões objetivas** COPERVE com crédito parcial, apresentação do subtotal em pontos e percentual, histórico de tentativas, contador de acertos exatos após finalização.
- Questões anuladas: 2019 não pontuavam; 2024 e 2025 concediam **1,00 ponto a todos**, conforme os editais específicos.

## 1. Arquivos alterados

- `app.py`: seleção de universidade, dados multiusuário, importação UFSC, correção por tipo de questão, relatório de pontos.
- `ufsc.py`: parser das provas e gabaritos UFSC + fórmula de pontuação de somatórios.
- `ufsc_score.py`: agregação da pontuação e regra anual de questões anuladas.
- `migrations/004_ufsc_multi_exam.sql`: esquema compatível com edições UDESC já cadastradas.
- `static/app.js`, `static/index.html`, `static/styles.css`: seleção de banco, novo importador e campos de respostas UFSC.
- `requirements.txt`: `beautifulsoup4` para ler os gabaritos HTML antigos.
- `parser.py`, `known_scanned_layouts.json`: patch atualizado com os cabeçalhos de UDESC 2019/1, 2024/1 e 2025/1.
- `tests/test_ufsc.py`: testes com as provas anexadas. Para rodar, disponha os PDFs/HTML oficiais em `/mnt/data` ou defina `UFSC_TEST_FIXTURES=/caminho`.

## 2. Publicar no Cloud Run

1. **Faça backup da base NeonDB.** Não altere as migrations 001, 002 ou 003 existentes.
2. Faça merge dos arquivos novos ao seu repositório; se modificou `app.py`/`static/app.js` posteriormente, reconcilie as alterações.
3. Configure as mesmas variáveis do NeonDB e Cloudflare R2, além das variáveis de sessão da implantação anterior; nenhuma credencial nova é exigida.
4. Execute `python -m scripts.migrate` com `DATABASE_MIGRATION_URL` ou deixe a rotina de deploy executar a migration `004_ufsc_multi_exam.sql` uma única vez.
5. Faça deploy do Dockerfile existente no Google Cloud Run.
6. Entre com sua conta, altere o seletor da barra lateral para UFSC e importe os arquivos.

## 3. Exemplos de arquivos para a UFSC

| Edição | Cor | Prova 1 | Gabarito 1 | Prova 2 | Gabarito 2 |
|---|---|---|---|---|---|
| 2019/1 | amarela | `2019-p1-amarela.pdf` | `2019-Gabarito - PROVA 1 - AMARELA.html` | `2019-p2-amarela.pdf` | `2019-Gabarito - PROVA 2 - AMARELA.html` |
| 2024/1 | azul | `ufsc-2024-p1_azul.pdf` | `ufsc-2024-novo_gabarito_p1_azul.pdf` | `ufsc-2024-p2_azul.pdf` | `ufsc-2024-novo_gabarito_p2_azul.pdf` |
| 2025/1 | azul | `ufsc-p1_azul.pdf` | `ufsc-2025-gabarito_p1_azul.pdf` | `ufsc-p2_azul.pdf` | `ufsc-2025-gabarito_p2_azul.pdf` |

Ao importar **ambos os dias**, o site deve cadastrar **72 questões por edição**, pois 8 questões de língua estrangeira são excluídas. São 32 da Prova 1 e 40 da Prova 2.

Para lançar mais de uma cor do mesmo ano, importe novamente informando outra cor; cada variante gera uma edição independente. **Não misture** a prova azul com o gabarito amarelo.

## 4. Fórmula COPERVE aplicada em cada questão de somatório

Quando `NPC > NPI`:

```
P = [NP - (NTPC - (NPC - NPI))] / NP
```

Caso contrário `P = 0,00`.

- `NP`: quantidade de proposições existentes naquela questão.
- `NTPC`: proposições verdadeiras no gabarito.
- `NPC`: proposições verdadeiras assinaladas pelo candidato.
- `NPI`: proposições falsas assinaladas pelo candidato.

A pontuação **é arredondada a duas casas por questão**, conforme o edital; a pontuação total objetiva soma esses pontos. Em questões abertas, o valor é **1,00 para acerto exato ou 0,00**, sem crédito parcial. Aulas e documentos completos ficam no PDF original armazenado no R2.

Exemplo: questão com 5 proposições, gabarito `02+04+08=14`. Se o aluno assinalar apenas `02+04=06`, ganha **0,80**. Se marcar `01+02=03`, ganha **0,00**.

**Limite essencial:** isso é a **pontuação objetiva do simulado**, calculada pela regra oficial da COPERVE. Não é a **Nota Final (NF)** oficial de classificação, porque a plataforma ignora língua estrangeira, não avalia a redação nem as questões discursivas e não aplica os **pesos por curso e edição**. A NF oficial usa `NF = 100 × PT / PMC`, exigindo todas essas parcelas.

## 5. Banco de dados

A migration adiciona, de forma retrocompatível:

- `editions.institution`, `editions.booklet_color`: identificação da banca e do modelo de prova.
- `questions.answer_kind`, `questions.numeric_key`, `questions.proposition_count`: modelo de correção UFSC.
- `attempts.institution`, `attempts.objective_points`, `attempts.objective_max`: origem e pontos do simulado.
- `attempt_items.snapshot_kind`, `snapshot_numeric`, `snapshot_np`, `selected_numeric`: gabarito e resposta numérica congelados para garantir consistência histórica.
- Novos tipos de documentos `key_morning` / `key_afternoon` na tabela `documents`.

As questões UDESC continuam usando `answer_kind='choice'` e os dados anteriores não precisam de reimportação.

## 6. Limitações de importação

- O parser está validado nas **provas e gabaritos específicos enviados para 2019, 2024 e 2025**. Para provas UFSC com diagramação ou quantidade de questões diferente, será necessário expandir o parser.
- Os recortes preservam gráficos e figuras. Quando uma questão referencia texto extenso da página anterior, use **Página anterior / texto de apoio** na interface.
- Atualização de gabarito em edição UFSC exige reimportação da edição para manter prova/gabarito correspondentes. O botão antigo de substituição de gabarito único permanece apenas para UDESC.
- Edições mais antigas da UFSC podem alterar o modelo de aplicação; não presuma que suportam o mesmo formato sem antes revisar seus cadernos e editais.
- O projeto é independente, sem vínculo com a UDESC ou UFSC. Provas e materiais oficiais conservam os direitos de seus titulares.

## 7. Testes

```bash
pip install -r requirements.txt
PYTHONPATH=. pytest -q tests/test_ufsc.py tests/test_correct_count.py tests/test_multi_tenant.py tests/test_parser.py
node --check static/app.js
```

Testes de integração reais com NeonDB, Cloud Run e R2 **não** foram realizados neste ambiente. Os testes HTTP utilizam banco simulado; os testes do parser trabalham com os documentos oficiais anexados.
