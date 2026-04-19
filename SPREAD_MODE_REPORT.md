================================================================
Sharktopus — modo spread: o que foi implementado e como foi testado
================================================================

CONTEXTO
--------
Pergunta original: se o usuário não escolhe priority, hoje a gente baixa tudo
de uma única fonte; não seria mais eficiente dividir o período entre todas
as fontes disponíveis (AWS + GCloud + Azure) e aplicar workers em paralelo
sem risco de throttling?

Decisão: sim, implementar "spread mode". Regras acordadas:
  1. Fila global ordenada por (date, cycle, fxx). WRF roda data-após-data,
     então as datas mais antigas têm prioridade absoluta.
  2. Cada fonte mantém seu próprio pool de workers no DEFAULT_MAX_WORKERS
     publicado (gcloud/aws/azure=4, nomads=2, rda=1). Nenhum worker de
     outra fonte pode estourar o teto da sua.
  3. Round-robin intercalado emergente: como todos puxam da mesma fila
     global, a distribuição vira dia 1→fonte A, dia 2→fonte B, dia 3→C
     sem precisar particionar manualmente.
  4. Falha NÃO é fallback síncrono. O worker devolve o job à fila com a
     fonte que falhou na blacklist — uma outra fonte pega quando sobrar
     workers. Assim B nunca estoura seu ceiling por causa de falhas em A.
  5. Timeout por tentativa: aborta cooperativamente e devolve à fila.
  6. Work-stealing: quando a fila "de A" esvazia, os workers de A pegam
     o próximo job pendente (que também estava na fila de B/C).

TRABALHO JÁ PRONTO E COMMITADO (330d312)
---------------------------------------
Arquivos novos:
  src/sharktopus/_queue.py
      - Step dataclass (key=(date,cycle,fxx), version, blacklist)
      - MultiSourceQueue com heap por fonte + versionamento lazy + set
        de in_progress para evitar dupla reivindicação.
      - O(log N) por push/pop.
  tests/test_queue.py     — 14 testes
  tests/test_batch_spread.py — 10 testes

Arquivos modificados:
  src/sharktopus/batch.py
      - fetch_batch ganha kwargs spread= e attempt_timeout=
      - Default: spread quando priority foi auto-resolvido E tem >1 fonte
      - priority explícito mantém fallback-chain (back-compat)
      - Nova função _run_spread() que orquestra o MultiSourceQueue
  src/sharktopus/sources/{base,_common,aws,azure,gcloud,nomads,nomads_filter,rda}.py
      - kwarg deadline: float | None = None propagado ponta-a-ponta
      - Checado entre retries e entre chunks do download
  CHANGELOG.md, README.md — documentação do modo spread

SOBRE IDX / LISTA MÍNIMA WRF (pergunta do chat)
------------------------------------------------
Sim, continua funcionando:

  * Byte-range via .idx: quando você passa variables= + levels= ao
    fetch_batch, as 4 fontes NCEP-layout (nomads/aws/gcloud/azure)
    baixam só os registros pedidos via HTTP Range. O rda também, via
    idx emprestada de uma mirror irmã; pré-2021 cai em full + wgrib2.

  * Lista WRF mínima em sharktopus.wrf:
      DEFAULT_VARS = 13 campos
        HGT LAND MSLET PRES PRMSL RH SOILL SOILW SPFH TMP TSOIL UGRD VGRD
      DEFAULT_LEVELS = 49 níveis
        coluna isobárica 1000→0.01 mb + 4 camadas de solo
        + 2m/10m/surface/MSL
      Total: 269 registros, ~485 KB após crop geográfico.

  * Aplicação automática: fetch_batch cai nesses defaults APENAS quando
    nomads_filter está na priority list (historicamente a única fonte
    que exigia variables/levels obrigatórios). Para as 4 outras fontes
    você passa VARS + LEVELS explicitamente senão baixa arquivo cheio
    (~500 MB). A CONVECT já usa esse formato em produção.

TESTES OFFLINE
--------------
Suite completa: 214 passed, 1 skipped (o skipped precisa de um GRIB2
real em cache; é esperado em máquinas limpas).

Testes novos com detalhe:

  test_queue.py (14):
    - push/pop mantém ordem por (date, cycle, fxx)
    - pending counter, auto-stop em mark_done
    - blacklist: fonte proibida não recebe o step
    - blacklist total → failure final imediato
    - re-enqueue bumpa version, cópia velha é descartada no pop
    - mark_done invalida cópias em outros heaps
    - concorrência: 50 steps, 5 workers em 3 fontes, cada step entregue
      uma única vez
    - concurrent_pop_single_step_delivered_once  ← ESSE ACHOU O BUG
    - wakeup em push quando worker está em cv.wait()
    - stop() acorda todo mundo

  test_batch_spread.py (10):
    - spread distribui entre fontes quando há jobs suficientes
    - spread default quando priority=None (auto)
    - priority=[...] explícito → fallback-chain por default (back-compat)
    - re-enqueue quando fonte A falha, B pega o step
    - on_step_fail só chamado quando TODAS as fontes falharam
    - ceiling por fonte preservado (não estoura DEFAULT_MAX_WORKERS)
    - attempt_timeout vira deadline nos kwargs do fetch_step
    - attempt_timeout=None → deadline=None
    - ordem global: timestamp mais antigo é servido primeiro

BUG ACHADO PELOS TESTES (e corrigido)
--------------------------------------
Na primeira rodada de test_batch_spread, test_spread_distributes_across_sources
falhou retornando 18 arquivos em vez de 9. Era uma race real:

Quando um Step é empurrado pra fila, ele entra em TODOS os heaps das
fontes elegíveis (não só uma). Se gcloud e aws fazem pop ao mesmo
tempo, ambos viam o Step no topo do seu próprio heap — ambos faziam
heappop e ambos executavam o download do mesmo job.

Fix: set self._in_progress no MultiSourceQueue. No pop, se o key
estiver em _in_progress, é descartado. Só é limpo em mark_done
(sucesso) ou num novo push (re-enqueue após falha). Test de regressão
test_concurrent_pop_single_step_delivered_once adicionado.

SMOKE LIVE — primeira rodada (4 jobs, vars/levels estreitos)
------------------------------------------------------------
Timestamps: 2026041500/06/12/18 — 4 timestamps × 1 step = 4 jobs
Priority:   [gcloud, aws, azure]
Vars/Levs:  [TMP, UGRD, VGRD] / [500 mb, 850 mb] — ~27 KB por arquivo

  fallback_chain:  4.03 s  — gcloud fez todos
  spread:          3.67 s  — gcloud ainda fez todos (!)
  speedup:         1.10×

Por que o spread não "espalhou"?
  - Só 4 jobs contra 12 workers (3 fontes × 4 cada)
  - Como gcloud é o primeiro da priority, seus workers acordaram antes
    nos cv.notify() do push e venceram todas as 4 corridas de pop
  - Não é bug — é o design: quando há menos jobs do que workers, o
    primeiro que chega leva. O spread só "aparece" quando o número de
    jobs é grande o bastante pra que os workers de gcloud não dêem
    conta antes de aws/azure entrarem

SMOKE LIVE — segunda rodada (RESULTADOS)
------------------------------------------------
Config:
  - 4 timestamps × 9 steps = 36 jobs
  - VARS = WRF-canonical (13), LEVELS = WRF-canonical (49) → 269 records
  - Priority = [gcloud, aws, azure]
  - Bbox = (-48,-36,-28,-18) com pad 2°
  - ~1.3 MB por step após crop (byte-range mode)

Resultados:

  === fallback_chain ===
    wall time    : 1148.99 s (19.1 min)
    files        : 36
    total bytes  : 47.06 MB
    per source   : {gcloud: 36}      ← todos caíram em gcloud

  === spread ===
    wall time    :  887.34 s (14.8 min)
    files        : 36
    total bytes  : 47.06 MB
    per source   : {gcloud: 23, aws: 5, azure: 8}  ← spread real

  SPREAD speedup: 1.29× (4.3 min salvos em 19 min)

Análise:
  - Spread funcionou: os 3 mirrors receberam jobs
    (36% do trabalho saiu do gcloud pra aws+azure)
  - Speedup modesto porque o crop local com wgrib2
    (~200 MB de byte-range concat → 1.3 MB cropped) é CPU-bound
    e serializa. Spread acelera a fase de rede mas não a fase de crop
  - Ainda assim, ganho real e zero custo (mesmo código, mesmo ceiling
    por fonte, sem risco de throttling)
  - Para aumentar o speedup seria preciso paralelizar a fase de crop
    também; por ora a limitação é wgrib2 em single-thread

PENDENTE
--------
  - (opcional) tag v0.2.0 com CHANGELOG[Unreleased] → [0.2.0]
  - #15: recipe conda-forge (follow-up)
  - Futuro: considerar crop paralelo por step (já está paralelo entre
    steps, mas não entre pieces do mesmo step)


================================================================
ANÁLISE: concat-depois-crop vs crop-por-variável-depois-concat
================================================================

Pergunta: qual ordem é mais rápida?
  (A) baixa byte-ranges em paralelo → concat em 1 arquivo → 1 crop único
  (B) baixa byte-ranges em paralelo → crop por variável → concat

Resposta curta: (A), por 3 motivos cumulativos. Implementação atual é (A).

--- Motivo 1: overhead de startup do wgrib2 ---

wgrib2 abre, decodifica e reescreve um GRIB2 num passe só. Cada invocação
do subprocess custa ~50-100 ms fixos (fork + exec + mmap + parse do
header + load das tabelas GRIB). Esse custo é independente do conteúdo.

Cenário WRF-canonical (269 records):
  (A) 1 chamada   wgrib2 -small_grib pra 269 records.
      Custo fixo: 1 × 50 ms = 50 ms
  (B) N chamadas  wgrib2 -small_grib, uma por variável.
      Se agrupar por VAR: 13 chamadas × 50 ms = 650 ms
      Se for por record: 269 × 50 ms = 13.4 s

Só de startup, (B) já perde 600 ms a 13 s por step. Multiplicando
por 36 steps do nosso smoke = 22 s a 8 min de overhead puro.

--- Motivo 2: I/O intermediário ---

(A) lê o GRIB uma vez (sequencial, ~200 MB do concat pre-crop) e
escreve uma vez (~1.3 MB pós-crop). 1 read + 1 write.

(B) para cada variável: lê a fatia da variável (pode não ser
sequencial no arquivo concat), escreve a versão cropada em disco,
depois no concat final lê todos os fragmentos cropados e escreve o
arquivo final. N reads + N writes + 1 read final + 1 write final.

Pra 13 variáveis: 13 reads + 13 writes extras, mesmo que cada fatia
seja pequena. Em disco SSD isso é pouco, mas ainda perde pro (A).

--- Motivo 3: HTTP Range consolidation ---

Isso aqui é o mais importante e é outra camada do mesmo raciocínio.
O nosso byte_ranges() junta records adjacentes no GRIB2 em 1 Range
HTTP só. Exemplo: se TMP @ 500 mb está no byte 10_000_000, UGRD @
500 mb no byte 10_280_000, e VGRD @ 500 mb no byte 10_560_000, e
elas são consecutivas no arquivo, vai 1 Range: bytes=10000000-10839999
em vez de 3 requests separados. Menos round-trips, melhor throughput.

Se a gente baixasse por variável (estratégia B), ia ser N requests
por step = dezenas a centenas de RTTs extras. Atualmente a gente
tem tipo 10-30 Ranges consolidados por step em vez de 269.

--- Alternativa teórica: crop server-side ---

Existe um caminho teoricamente ainda mais rápido: o servidor cortar
a região geográfica antes de responder. É exatamente o que o
nomads_filter faz (CGI filter_gfs_0p25.pl). Transferência
mínima possível (~dezenas de KB), zero crop local.

Mas:
  - Só NOMADS oferece esse CGI.
  - Janela de ~10 dias de retenção.
  - Latência do CGI é alta (~100-300 ms por request, serial).
  - Não escala bem pra batch grande (rate-limited).

Por isso sharktopus usa nomads_filter apenas quando é explicitamente
pedido; o default é byte-range em cloud mirror, que é o melhor
equilíbrio para batches de tamanho real (semanas a meses de GFS).

--- Resumo prático ---

Ordem de preferência de estratégia, do mais barato ao mais caro:

  1. nomads_filter (server-side subset)
       transferência: ~dezenas de KB
       crop local: nenhum
       limite: janela ~10 dias; serial; latência CGI

  2. byte-range consolidado + 1 crop local  ← IMPLEMENTAÇÃO ATUAL
       transferência: ~200 MB (pré-crop), cai pra ~1.3 MB
       crop local: 1 chamada wgrib2 por step
       limite: wgrib2 single-thread é CPU-bound

  3. byte-range por variável + N crops + concat
       transferência: igual a (2) no agregado, mas mais RTTs
       crop local: N chamadas wgrib2 por step
       limite: mesma banda, pior latência, pior CPU

  4. full download + crop local
       transferência: ~500 MB por step
       crop local: 1 chamada wgrib2
       limite: WAN fica saturada; só faz sentido em fallback

A ordem (2) é o sweet spot para WRF porque:
  - cobre janela longa (todos os dates desde 2015 via rda, 2021+ em
    aws/gcloud/azure)
  - paralelismo de rede via Range consolidado (HTTP keep-alive)
  - 1 único crop por step (mínimo de overhead wgrib2)
  - integra com o spread mode: cada fonte tem seu pool de rede e
    todos compartilham o pool de CPU do wgrib2 via serialização
    natural do subprocess


================================================================
ADENDO: opt-in OpenMP em wgrib2 + warning de headroom
================================================================

CONTEXTO
--------
No smoke das 36 steps, o spread saiu 1.29× mais rápido (887s vs 1149s).
Boa parte do gargalo restante é a fase de crop do wgrib2, que rodava
single-thread. wgrib2 é compilado com -fopenmp, ou seja, sabe
paralelizar -small_grib e -match — mas por default OMP_NUM_THREADS=1.

Numa máquina tipo megashark (128 núcleos), o pico de concorrência do
spread é ~12 crops simultâneos (4 workers × 3 fontes). Isso deixa
~116 núcleos ociosos durante cada wave de crop. Num 1 arquivo, não faz
diferença. Em 10 anos de reanálise (~29k ciclos × 9 steps = ~260k
crops), ~10% de ganho por arquivo vira horas.

Resultado da discussão: deixar como OPT-IN, nunca ligar por default
(máquinas pequenas ficariam sobrecarregadas), mas emitir um warning
no primeiro fetch_batch(spread=True) quando detectar headroom real e
nenhuma env var setada.

IMPLEMENTAÇÃO (commit pendente)
-------------------------------
Arquivos modificados:

  src/sharktopus/grib.py
    - crop() e filter_vars_levels() ganham kwarg omp_threads: int | None
    - Novo helper suggest_omp_threads(concurrent_crops, cpu_count=None)
      retorna valor seguro: (cpu_count - 2) // concurrent_crops, cap 8.
    - Novo _resolve_omp_threads(): prioridade explicit > env > None.
    - Novo _env_with_omp(): monta dict de env pro subprocess quando N > 0.
    - Quando OMP_NUM_THREADS vem definido, subprocess.run recebe env=;
      senão env=None (herda do processo pai → comportamento atual).
    - Variável de ambiente SHARKTOPUS_OMP_THREADS funciona como default
      process-wide. Rejeita valor não-inteiro ou < 1 com ValueError.

  src/sharktopus/batch.py
    - Novo _maybe_warn_omp_headroom(priority): roda uma vez por processo
      no início de _run_spread. Condições para o warning disparar:
        1. Flag global _OMP_HEADROOM_WARNED ainda não setado.
        2. SHARKTOPUS_OMP_THREADS não setado.
        3. OMP_NUM_THREADS não setado ou == "1".
        4. cpu_count - sum(workers por fonte) >= 8 (headroom real).
        5. suggest_omp_threads(...) > 1.
      Mensagem sugere valor concreto derivado do suggest_omp_threads.

Regra de decisão para OMP_NUM_THREADS:
  1. kwarg explícito omp_threads=N vence tudo.
  2. env var SHARKTOPUS_OMP_THREADS se presente.
  3. Se nenhum dos dois, subprocess herda env do pai (wgrib2 roda
     single-thread por default).

TESTES ADICIONADOS (+14)
-------------------------
tests/test_grib.py:
  - test_suggest_omp_threads_splits_cores_fairly  → 128 cores × 12
    crops × cap 8 = 8. Com max_per_crop=32 retorna 10.
  - test_suggest_omp_threads_small_hosts_return_one  → 8 cores × 12
    crops = (6/12)=0 → clamp 1. 4 cores × 4 crops = (2/4)=0 → 1.
  - test_suggest_omp_threads_zero_concurrent_returns_one
  - test_suggest_omp_threads_uses_os_cpu_count_when_none
  - test_crop_passes_omp_num_threads_when_given  → subprocess.run
    recebe env com OMP_NUM_THREADS=8.
  - test_crop_reads_shark_topus_omp_threads_env  → env var lida.
  - test_crop_no_env_when_omp_not_set  → subprocess.run env=None.
  - test_crop_explicit_beats_env  → omp_threads=16 vence env=2.
  - test_crop_rejects_zero_omp_threads  → ValueError.
  - test_crop_rejects_garbage_env  → SHARKTOPUS_OMP_THREADS="eight"
    levanta ValueError.
  - test_filter_vars_levels_passes_omp_env  → mesma lógica em filter.

tests/test_batch_spread.py:
  - test_omp_warning_fires_when_cores_idle  → 128 cpu, 2×2 workers,
    env limpa → UserWarning com "SHARKTOPUS_OMP_THREADS" na mensagem.
  - test_omp_warning_silenced_when_env_set  → SHARKTOPUS_OMP_THREADS=8
    no ambiente → zero warnings.
  - test_omp_warning_not_fired_on_small_hosts  → 8 cpu, 4×4 workers
    → sem headroom → zero warnings.
  - test_omp_warning_fires_only_once  → duas chamadas consecutivas
    de fetch_batch, apenas 1 warning capturado.
  - reset_omp_warning fixture zera o flag global entre testes para
    que cada teste veja estado limpo.

DOCUMENTAÇÃO
------------
CHANGELOG.md ([Unreleased]):
  - Seção nova "Opt-in wgrib2 OpenMP parallelism" descrevendo crop,
    filter_vars_levels, SHARKTOPUS_OMP_THREADS e suggest_omp_threads.
  - Seção "Headroom warning" explicando quando dispara.

README.md:
  - Bloco novo depois da seção Spread mode: duas formas de ligar
    (env var process-wide, ou kwarg omp_threads por chamada), e menção
    ao warning automático em hosts grandes. Aponta para
    grib.suggest_omp_threads() para quem quiser tunar manual.

SUITE DE TESTES
---------------
229 passed, 1 skipped (o skipped é o mesmo de antes, precisa de GRIB2
real em cache). O warning aparece em test_availability no full-suite
run — é sinal legítimo de que o caminho em produção está funcionando
(primeiro fetch_batch em spread mode disparou). Flag global faz com
que apareça uma vez só.

EXEMPLO DE USO
--------------
Process-wide (menu_gfs, orchestrator, lambdas):

  export SHARKTOPUS_OMP_THREADS=8
  python -m sharktopus.cli ...

Per-call (quem integra como lib):

  sharktopus.grib.crop(src, dst, bbox=..., omp_threads=8)
  sharktopus.fetch_batch(..., spread=True)  # sem ligar OMP

Para pegar valor sugerido programaticamente:

  from sharktopus.grib import suggest_omp_threads
  n = suggest_omp_threads(concurrent_crops=12)  # cpu_count auto
  os.environ["SHARKTOPUS_OMP_THREADS"] = str(n)

GANHO ESPERADO
--------------
Por arquivo: ~10% (wgrib2 OpenMP speedup flatten em 4-8 threads numa
fatia de ~50 MB). Ordem de grandeza:
  - 1 crop de 1 GFS step: ~1s → ~0.9s   (desprezível)
  - 36 crops do smoke:    ~36s → ~32s   (ainda ruído)
  - 1 ano @ 6h:           ~5k crops     (~10 min salvos)
  - 10 anos @ 6h:         ~29k crops    (~50 min salvos)
  - reanálise full era 1960-hoje @ 3h: ~187k crops (~5h salvos)

Por isso é opt-in: vale acender em pipelines de reanálise longa ou em
CI que processa muitos casos, não em simulações pontuais.

PENDENTE
--------
  - Smoke live comparando spread sem OMP vs spread com OMP=8 (opcional,
    confirma o número em produção). Agendar quando a megashark estiver
    desocupada.
  - Agrupar este addendum + implementação do spread num único bump
    de versão v0.2.0 antes do recipe conda-forge.
