# Plano: Biblioteca Python `sharktopus`

Documento vivo de acompanhamento da conversão do sub-sistema Sharktopus (fetcher
GFS multinuvem do CONVECT) em uma biblioteca Python instalável via `pip`.

**Mora dentro do próprio repo** (`docs/PLANO.md`), ao lado de `ROADMAP.md`
(visão curta do mapa de camadas) e `ORIGIN.md` (mapeamento função→CONVECT).
O CONVECT (`~/CONVECT`) continua sendo a fonte de cada port.

> **Como usar este doc**: cada sessão atualiza a seção **"Status atual"** com o que
> foi feito, move itens de **"Próximo passo"** para **"Feito"** conforme avançamos,
> e anota decisões de design + blockers no **"Log de sessões"** ao final.

---

## Contexto

**O que é Sharktopus.** Sub-sistema cloud-native dentro do CONVECT que:

1. Faz deploy automático (1 comando) de funções serverless nas 3 nuvens (AWS
   Lambda, Google Cloud Run, Azure Functions) que recortam GFS no próprio provedor.
2. Recorte em nuvem reduz o download de ~500 MB → ~2 MB por timestep.
3. Distribui requisições entre nuvens em round-robin para manter cada uma no free
   tier (≈400k GB·s/mês cada).

**Mas também (descoberto na sessão 2026-04-17):** já existem **5 fontes locais
plenamente funcionais** — NOMADS direto, NOMADS filter (grib_filter.pl), AWS S3
byte-range, GCloud Storage byte-range, Azure Blob byte-range. Essas baixam
direto do bucket público e cortam com wgrib2 local. **Nenhuma delas depende
do deploy serverless.** Isso muda a estratégia: começamos a biblioteca pela
parte local (sem credenciais, sem deploy), e a parte serverless entra como
camada opcional.

**Inspiração de organização: Herbie** (herbie.readthedocs.io). Copiamos:
- Construtor único com `date/model/product/fxx`.
- Lista de prioridade de fontes com fallback automático (`priority=[...]`).
- Config em `~/.config/sharktopus/config.toml` + env vars.
- CLI que espelha a API Python.

Descartamos: plotting, HRRR/ECMWF/etc, inventários via "search strings". Foco
restrito a **GFS download + recorte** (local e opcionalmente em nuvem).

---

## Estratégia de construção em camadas

Constrói-se **de baixo pra cima**. Cada camada é testada isoladamente antes
da próxima. Camada N+1 depende de N; as camadas cloud (3, 4) são extras
opcionais — uma pessoa que só quer baixar GFS localmente usa camadas 0–2.

```
┌──────────────────────────────────────────────────────────────┐
│ 5. CLI & Menu interativo    (sharktopus.cli)                │
├──────────────────────────────────────────────────────────────┤
│ 4. Deploy serverless [extra] (sharktopus.deploy)            │ — setup("aws"|...)
├──────────────────────────────────────────────────────────────┤
│ 3. Invoke serverless [extra] (sharktopus.cloud)             │ — invoke()
├──────────────────────────────────────────────────────────────┤
│ 2. Orquestrador fetch        (sharktopus.fetch)             │ — prioridade, cache, paralelismo
├──────────────────────────────────────────────────────────────┤
│ 1. Fontes locais             (sharktopus.sources.*)         │ — 5 fontes
├──────────────────────────────────────────────────────────────┤
│ 0. Utilidades wgrib2 + idx   (sharktopus.grib)              │ — crop, filter, verify, parse_idx
└──────────────────────────────────────────────────────────────┘
```

### Camada 0 — utilidades wgrib2 + .idx (puro, sem rede)

Funções miúdas que hoje estão **duplicadas** entre os 5 scripts de download.
Ficam num único módulo e todas as camadas acima consomem daqui.

Candidatas a unificar (extraídas do levantamento dos scripts):

| Função unificada | Origens atuais |
|---|---|
| `sharktopus.grib.crop(src, dst, bbox)` | `crop_grib()` em `download_aws_gfs_0p25_full4.py`, `download_gcloud_gfs_0p25.py`, `download_azure_gfs_0p25.py`; `_crop_region()` em `download_rda_gfs.py` |
| `sharktopus.grib.filter(src, dst, vars, levels)` | `filter_grib_by_vars_levels()` em NOMADS e AWS |
| `sharktopus.grib.verify(path) → int` (# registros) | `run_verification()` em 3 scripts, `_verify_grib()` no RDA |
| `sharktopus.grib.parse_idx(text) → list[Record]` | `read_idx()` em AWS, GCloud, Azure |
| `sharktopus.grib.byte_ranges(records, wanted)` | `compute_ranges()` em AWS, GCloud, Azure |
| `sharktopus.grib.rename_link_abs/rel(path)` | `create_link_abs/rel()` e `rename_grib()` em NOMADS e AWS |

### Camada 1 — fontes locais (`sharktopus.sources.*`)

Cinco módulos, **uma interface comum**:

```python
def fetch_step(date, cycle, fxx, *, dest, bbox=None, vars_levels=None,
               timeout=300) -> Path:
    """Baixa 1 timestep. Retorna o Path do .grib2 final.
    Levanta SourceUnavailable se o step não existe ainda nesse mirror."""
```

Mapeamento 1:1 com o que existe hoje:

| Módulo alvo | Arquivo atual | Observações |
|---|---|---|
| `sharktopus.sources.nomads` | `download_nomades_gfs_0p25.py` (444+ linhas) | full-file; útil quando `fxx` é novo e ainda não está na AWS |
| `sharktopus.sources.nomads_filter` | `download_nomads_filter.py` (52 linhas) | usa `grib_filter.pl` com `subregion=1&...` (crop server-side, sem wgrib2) |
| `sharktopus.sources.aws_s3` | `download_aws_gfs_0p25_full4.py` (401+ linhas) | byte-range via `.idx`, mais rápido; precisa `boto3` ou `s5cmd` |
| `sharktopus.sources.gcloud_storage` | `download_gcloud_gfs_0p25.py` (306+ linhas) | byte-range via HTTPS público; sem auth |
| `sharktopus.sources.azure_blob` | `download_azure_gfs_0p25.py` (204+ linhas) | byte-range via HTTPS público; sem auth |
| `sharktopus.sources.rda` | `download_rda_gfs.py` (323+ linhas) + `download_rda_gfs_1deg.py` | requer login RDA; resolução 0.25° + 1° |

### Camada 2 — orquestrador (`sharktopus.fetch`)

A função "Herbie-like" que o usuário chama:

```python
import sharktopus as st

path = st.fetch(
    "2024-01-21 00Z",
    fxx=range(0, 13, 3),             # ou int único
    product="pgrb2.0p25",            # default
    bbox=(-45, -40, -25, -20),       # (lon_w, lon_e, lat_s, lat_n) — padrão wgrib2
    vars=["TMP","UGRD","VGRD"],      # opcional
    levels=["500 mb","850 mb"],      # opcional
    priority=["aws_s3","gcloud_storage","nomads_filter","nomads","azure_blob"],
    dest="~/data/gfs",               # default do config.toml
    parallel=4,
)
```

Responsabilidades:
- Itera `fxx`, para cada step tenta as fontes na ordem de `priority`.
- Fallback automático em `SourceUnavailable`.
- Cache local (pula se arquivo já existe e passa `verify`).
- Paralelismo com `ThreadPoolExecutor` (já existe nos scripts).

### Camada 3 — invocação serverless (`sharktopus.cloud`) — extra

Só faz sentido depois que `setup()` rodou. Invoca a Lambda/CloudRun/Function
deployada, que corta no provedor e devolve o GRIB2 recortado (ou URL S3/GCS).

```python
path = st.cloud.fetch(
    "2024-01-21 00Z", fxx=6, bbox=(...),
    provider="aws",   # ou "gcloud", "azure", "auto" (round-robin c/ quota tracking)
)
```

O código de invocação já existe, espalhado no `menu_gfs.py` (linhas ~29–50 e
funções `invoke_lambda`, `invoke_cloudrun`, `invoke_azurefunc`). **Blocker B2**:
essas funções leem URLs de constantes hardcoded — migração precisa trocar por
`sharktopus.config.load()`.

### Camada 4 — deploy (`sharktopus.deploy`) — extra

Já existe e funciona (AWS + GCloud testados). Basicamente mover
`orchestration/deploy/*.py` → `src/sharktopus/deploy/*.py` e re-exportar o
`setup(provider)`.

### Camada 5 — CLI + menu interativo (`sharktopus.cli`)

Port do `menu_gfs.py` (2712 linhas) como módulo `sharktopus.cli.menu`, com
entry point `sharktopus` no `pyproject.toml`. **Só depois** das camadas 0–4
estáveis, porque o menu é o consumidor final de tudo isso.

---

## API pública alvo (imports explícitos)

Três estilos coexistem, como no Herbie:

### (a) Top-level one-shot — 80% dos casos
```python
import sharktopus as st
path = st.fetch("2024-01-21 00Z", fxx=6, bbox=(-45,-40,-25,-20))
ds = st.open_xarray(path)    # opcional: wrapper de cfgrib
```

### (b) Fonte específica — controle fino ou debug
```python
from sharktopus.sources import aws_s3, nomads_filter
p = aws_s3.fetch_step("20240121", "00", 6, dest="~/data",
                       bbox=(-45,-40,-25,-20))
p = nomads_filter.fetch_step("20240121", "00", 6, dest="~/data",
                              bbox=(-45,-40,-25,-20),
                              vars=["TMP","UGRD","VGRD"],
                              levels=["500 mb","850 mb"])
```

### (c) Utilidades wgrib2 isoladas — para quem já tem o GRIB2
```python
from sharktopus.grib import crop, filter, verify, parse_idx
crop("in.grib2", "out.grib2", bbox=(-45,-40,-25,-20))
n = verify("out.grib2")                  # nº de registros
records = parse_idx(open("in.grib2.idx").read())
```

### (d) Cloud (extra) — só se `setup()` já rodou
```python
from sharktopus.deploy import setup
setup("aws")                              # 1x por conta

from sharktopus.cloud import invoke
path = invoke("aws", "20240121", "00", 6, bbox=(-45,-40,-25,-20))
```

Conversão vs Herbie:

| Herbie | Sharktopus | Motivo |
|---|---|---|
| `H = Herbie(date, model, product, fxx)` | `st.fetch(date, fxx, product=...)` direto | Não precisamos de objeto com estado; o arquivo é o artefato |
| `H.search(":TMP:500 mb")` | `vars=[...], levels=[...]` explícitos | Mais simples, evita DSL; o usuário vem do mundo WRF/namelist |
| `H.xarray(":TMP")` | `st.open_xarray(path)` | Separa download de leitura |
| `priority=['aws','nomads',...]` | Mesma coisa | Idêntico |
| `~/.config/herbie/config.toml` | `~/.config/sharktopus/config.toml` **+** `~/.sharktopus/config.json` (deploy) | Dois arquivos: um do usuário (TOML, editável), um de estado de deploy (JSON, gerado) |

---

## Plano de validação por camada

Antes de mover/refatorar, **roda cada função no ambiente atual** com um caso
conhecido e registra o resultado. Caso teste canônico: **2024-01-21 00Z, fxx=6,
bbox=(-45,-40,-25,-20) (sudeste do Brasil)** — é o mesmo caso já validado
no pipeline de radar DA (ver `project_radar_da_outputs_inventory`).

### Validação Camada 0 (utilidades)
- Extrair `crop_grib`, `filter_grib_by_vars_levels`, `run_verification`, `read_idx`
  das 3 fontes em AWS/GCloud/Azure para um `grib_utils.py` de teste.
- Smoke: pegar um GRIB2 já baixado em `/gfsdata/...`, rodar `crop()` → comparar
  com saída do wgrib2 manual; rodar `verify()` → comparar nº de registros.
- Critério: todas as 6 utilidades executam sem erro e retornam o mesmo resultado
  que as versões atuais.

### Validação Camada 1 (fontes locais, uma por vez)

Para cada fonte, **isolar um pequeno script** que importa só o `download_gfs`
daquele arquivo e roda o caso canônico:

| Fonte | Script de validação | Tempo esperado |
|---|---|---|
| NOMADS direto | `python -c "from download_nomades_gfs_0p25 import download_gfs; download_gfs(date='20240121', ref='00', ext='6h', intr='6h', lat_s=-25, lat_n=-20, lon_w=-45, lon_e=-40)"` | ~30s/step |
| NOMADS filter | idem `download_nomads_filter` | ~5s/step (crop server-side) |
| AWS S3 | idem `download_aws_gfs_0p25_full4` | ~10s/step (byte-range) |
| GCloud Storage | idem `download_gcloud_gfs_0p25` | ~10s/step |
| Azure Blob | idem `download_azure_gfs_0p25` | ~10s/step |

Blocker esperado: paths hardcoded `/gfsdata/fcst/...` e `/experiments/...`.
Solução durante validação: `cd /tmp && mkdir -p gfsdata/fcst && ln -s /tmp/gfsdata /gfsdata`
(hack temporário, não commitado).

Critério de pronto: os 5 scripts produzem o mesmo GRIB2 recortado (diff binário
não precisa bater, mas `verify()` tem que dar mesmo `n_records`).

### Validação Camada 2 (orquestrador)
- Montar `fetch()` minimal chamando as 5 fontes da camada 1 na ordem `priority`.
- Simular falha injetando 503 no mirror primário → deve cair pro próximo.
- Critério: baixa 3 steps do caso canônico com `priority=['aws_s3','nomads']`
  e quando matamos rede pra S3, cai pro NOMADS sem intervenção.

### Validação Camadas 3–4 (cloud)
- Já validadas parcialmente (AWS ✓, GCloud ✓, Azure ✗ bloqueado no wgrib2).
- Ver `docs/cloud_setup_report.md` para o histórico completo.
- Nada a revalidar agora; só migrar imports quando chegar nessa fase.

---

## Arquitetura alvo (diretórios)

```
sharktopus/
  pyproject.toml
  README.md
  LICENSE
  src/sharktopus/
    __init__.py              # expõe fetch, open_xarray, Config
    config.py                # load/save ~/.config/sharktopus/config.toml
    grib.py                  # camada 0: crop, filter, verify, parse_idx, byte_ranges
    fetch.py                 # camada 2: orquestrador
    sources/                 # camada 1
      __init__.py
      base.py                # Protocol fetch_step(...) + SourceUnavailable
      nomads.py
      nomads_filter.py
      aws_s3.py
      gcloud_storage.py
      azure_blob.py
      rda.py
    cloud/                   # camada 3 [extra]
      __init__.py            # invoke(provider, ...)
      aws.py
      gcloud.py
      azure.py
    deploy/                  # camada 4 [extra] — move de orchestration/deploy
      __init__.py            # setup(provider)
      aws.py
      gcloud.py
      azure.py
      common.py
    cli/                     # camada 5
      __init__.py            # entry point sharktopus
      menu.py
    wgrib2/
      wgrib2-linux_x86_64   # binário estático (blocker B1)
  tests/
    test_grib.py             # camada 0
    test_sources_nomads.py   # camada 1 (live, skippable)
    test_sources_aws.py
    ...
    test_fetch.py            # camada 2 (mocks)
```

### Decisões de design (marcar resolvidas à medida que confirmadas)

- [ ] **Nome PyPI**: `sharktopus` (provável) — checar em pypi.org antes do primeiro push.
- [x] **Build backend**: hatchling (confirmado 2026-04-17 (e); build hook custom em
  `hatch_build.py` para platform-tag do wheel quando `_bin/wgrib2` existe).
- [x] **Python mínimo**: 3.10 (declarado em `pyproject.toml`).
- [ ] **Extras**:
  - base: `requests`, `tomli`/`tomllib` — fontes HTTP puras (NOMADS, nomads_filter, GCloud, Azure) funcionam
  - `[aws]`: `boto3` (para `sources.aws_s3` + `deploy.aws` + `cloud.aws`)
  - `[gcloud]`: `google-auth`, `google-cloud-storage` (só se user quiser auth; público não precisa)
  - `[azure]`: `azure-identity`, `azure-mgmt-web` (só para deploy)
  - `[xarray]`: `cfgrib`, `xarray` (para `open_xarray`)
  - `[cli]`: `rich`, `prompt-toolkit`
  - `[web]`: `fastapi`, `uvicorn`
  - `[all]`: tudo
- [x] **wgrib2 no wheel**: compilado em CI (`quay.io/pypa/manylinux_2_28_x86_64`)
  a partir da fonte upstream com features opcionais desativadas (AEC, OpenJPEG,
  NetCDF), empacotado em `src/sharktopus/_bin/wgrib2`, wheel tagueado
  `py3-none-linux_x86_64` (auditwheel promove para manylinux_2_28 na CI).
  Fallback via `$SHARKTOPUS_WGRIB2` ou `$PATH` para instalações source-only.
  aarch64/macOS ainda são TODO no workflow (2026-04-17 (e)).
- [x] **Bbox convention**: `(lon_w, lon_e, lat_s, lat_n)` (wgrib2 / Herbie) — adotada
  em toda a API pública.
- [ ] **Path destino default**: `~/.cache/sharktopus/gfs/{date}{cycle}/{bbox_tag}/`
  vs `/gfsdata/...` (atual). Primeiro é portável.

---

## Próximo passo imediato

**Camadas 0–5 funcionais (2026-04-20).** AWS Lambda + GCloud Cloud Run
em produção, CLI com `--setup` para bootstrap one-shot, docs de deploy
+ billing + auth prontos. Frentes de trabalho pendentes, em ordem de
prioridade:

1. **Azure Functions crop source** (task #52). Terceira nuvem;
   recipe já desenhado em `deploy/aws` / `deploy/gcloud` — replicar.
   Destrava round-robin entre 3 nuvens para user no free tier.
2. **Release público v0.1.0 no PyPI.** Tag existe (task #38) e wheel
   CI gera artefato; falta `twine upload` + conda-forge recipe PR
   (task #15). JOSS paper skeleton + Zenodo DOI depois.
3. **Rewrite `deploy/gcloud/provision.py` em Python puro** —
   `google-cloud-run` + `google-cloud-storage` + `google-cloud-artifact-registry`
   substituem shell-out ao `gcloud` CLI. Faz o `--setup gcloud` não
   exigir install do gcloud no host do deployer.
4. **Live test `sharktopus --setup` num host limpo.** Megashark já tem
   gcloud instalado; validar o fluxo de install opt-in exige VM
   scratch (ou container leve).
5. **Observabilidade.** Logging estruturado em `fetch_batch` (tempo
   por step, bytes, cache-hit, fonte vencedora) — útil quando users
   externos começarem a reportar regressões.

---

## Blockers conhecidos

| # | Blocker | Impacto | Ideia |
|---|---|---|---|
| ~~B1~~ | ~~wgrib2 estático não reprodutível~~ | ~~Azure deploy e wheel distribuível só funcionam com binário manualmente compilado~~ | **Resolvido 2026-04-17 (e)** — `scripts/build_wgrib2.sh` compila a partir do upstream NOAA com features opcionais off; `.github/workflows/build-wheels.yml` roda em manylinux_2_28 e gera o wheel; `scripts/bundle_wgrib2.sh` + `hatch_build.py` montam o artefato. Resolver em `sharktopus._wgrib2` escolhe entre binário bundled / `$SHARKTOPUS_WGRIB2` / `$PATH` |
| B2 | `menu_gfs.py` tem URLs hardcoded (linhas 29–50) | Quem fizer `setup()` em conta nova não usa seus próprios endpoints | Substituir constantes por lookup em `sharktopus.config.load()`. Vira Camada 3 |
| B3 | Scripts atuais assumem paths do container (`/gfsdata`, `/experiments`) | Não portáveis fora do container fetcher | Na migração para `sharktopus.sources.*`, parametrizar `dest=` e remover `/experiments` (usar argparse externo) |
| B4 | Free-tier tracking usa `/gfsdata_store/.lambda_invocations` | Não portável | Mover para `~/.cache/sharktopus/quota.json` |
| ~~B5~~ | ~~6 utilidades wgrib2 duplicadas entre 3–4 scripts~~ | ~~Cada bugfix precisa ser aplicado em N lugares~~ | **Resolvido 2026-04-17 (c)** — estão em `sharktopus.grib` |

---

## Log de sessões

### 2026-04-20 — `sharktopus --setup {gcloud,aws}` + docs de auth/billing
- Bootstrap subcommand novo: `sharktopus --setup gcloud` ou `--setup aws`
  detecta o CLI da nuvem, oferece install user-space opt-in
  (`~/google-cloud-sdk` / `~/.local/aws-cli`), guia o browser-OAuth
  (imprime o comando, espera ENTER — sem stdin-forwarding frágil) e
  chama o `provision.py` correspondente. ~4 prompts end-to-end,
  nada silencioso. `pip install` nunca dispara.
- `deploy/aws/provision.py` ganhou `_hint_credentials()` que traduz
  erros boto3 em ações concretas: SSO expirado → `aws sso login`;
  ProfileNotFound → `aws configure sso`; NoCredentials → escolha de
  método.
- `docs/DEPLOY_AWS.md` (novo) e `docs/DEPLOY_GCLOUD.md` (seção Auth
  expandida): IAM Identity Center documentada como caminho recomendado,
  chaves estáticas como fallback. `gcloud auth login --no-launch-browser`
  e sua gotcha no Claude `!` prefix (sem forward de stdin) documentadas.
- `docs/IMAGE_STORAGE_AND_BILLING.md` (novo): modelo "pull once" da
  imagem container — primeiro cold start puxa do GHCR, AR/ECR cacheia,
  próximas invocações servem do cache. Headroom confortável no free
  tier (AR ~66 MB vs 500 MB; ECR ~90 MB vs 500 MB).
- Bug de auth em produção resolvido: `google.oauth2.id_token.fetch_id_token`
  não aceita ADC tipo user (só SA / metadata server). `gcloud_crop`
  agora faz fallback para `gcloud auth print-identity-token`
  (commit `a31d341`). Smoke real contra Cloud Run: 16103 bytes GRIB2,
  6 records, 1.0-1.3 s warm.
- Commits: `a31d341`, `cc69166`, `b2c8592`, `7334261`.

### 2026-04-19 — GCloud Cloud Run deploy + GHCR→AR proxy
- Camada 3 segunda nuvem: `sharktopus.sources.gcloud_crop` análogo
  ao `aws_crop`, tira partido do free tier de Cloud Run (2M req/mês,
  180k vCPU-s, 360k GiB-s). Dois modes de delivery: `inline`
  (base64, cap 20 MB) e `gcs` (signed URL 1 h, objeto auto-apagado;
  retido com `SHARKTOPUS_RETAIN_GCS=true`).
- `deploy/gcloud/provision.py`: cria AR remote repo `ghcr-proxy`
  apontando para `ghcr.io` — Cloud Run recusa `ghcr.io/*` diretamente,
  só aceita `gcr.io`, `*-docker.pkg.dev`, `docker.io`. AR proxy é
  o análogo GCloud do ECR Pull-Through Cache do AWS.
- Fix libgfortran ABI mismatch no Dockerfile Cloud Run (runtime base
  tinha libgfortran5, binário wgrib2 buildado contra libgfortran4).
- Matrix CI build: `.github/workflows/build-image.yml` publica duas
  variantes (`lambda`, `cloudrun`) em GHCR a cada push em `main`,
  cache buildx separado por scope.
- `DEFAULT_PRIORITY` agora
  `("aws_crop", "gcloud_crop", "gcloud", "aws", "azure", "rda", "nomads")`.
  `supports(date)` de cada cloud source requer credencial viável —
  hosts sem GCloud/AWS configurado silenciosamente dropam a fonte.
- CLI: `--quota {aws,gcloud}` despacha para o tracker correspondente.
- 29 testes novos. Commits: `3e375d0`, `eddfb03`, `593a2e4`.

### 2026-04-18 (b) — AWS Lambda deploy + CI GHCR publish
- Camada 3 primeira nuvem: `sharktopus.sources.aws_crop` invoca Lambda
  deployada em `deploy/aws/provision.py` (container image + ECR
  Pull-Through Cache do GHCR + S3 bucket com lifecycle 7d + IAM role).
  Free-tier tracker (`sharktopus.cloud.aws_quota`): 1M req/mo +
  400k GB-s/mo antes de spend; gates `SHARKTOPUS_ACCEPT_CHARGES` /
  `SHARKTOPUS_MAX_SPEND_USD`.
- Handler Lambda devolve GRIB2 em dois modos: `inline` (base64 ≤ 20 MB,
  default para crops pequenos) e `s3` (presigned URL 1 h). Client
  baixa-deleta; `SHARKTOPUS_RETAIN_S3` retém.
- CI `.github/workflows/build-image.yml` publica
  `ghcr.io/sharktopus-project/sharktopus:lambda-latest`; provision
  rewrite usa PTC para evitar GitHub auth no Lambda cold start.
- Refactor: `src/sharktopus/` split em subpacotes — `sources/`,
  `cloud/`, `batch/` (era monolito). Imports externos quebrados
  atualizados.
- Commits: `1579660`, `7ca755d`, `de65173`, `1504084`.

### 2026-04-18 — Camada 1 completa: AWS + GCloud + Azure + RDA
- Quatro fontes novas portadas seguindo o mesmo contrato de `nomads.py`:
  - `sharktopus.sources.aws` — `noaa-gfs-bdp-pds.s3.amazonaws.com`
  - `sharktopus.sources.gcloud` — `storage.googleapis.com/global-forecast-system`
  - `sharktopus.sources.azure` — `noaagfs.blob.core.windows.net/gfs`
  - `sharktopus.sources.rda` — `data.rda.ucar.edu/d084001` (ds084.1)
- **Estratégia**: todas usam **download do GRIB completo + recorte local
  com wgrib2** (não byte-range). Mais simples, uma conexão HTTP por step,
  mesmo fluxo `bbox → crop` em todas as fontes. Byte-range continua
  possível via `grib.byte_ranges` / `parse_idx` para quem precisa.
- **Helper novo**: `sources._common.download_and_crop(url, final, ...)`
  consolida stream_download + crop opcional + verify. `nomads.py`
  refatorado pra usá-lo também — zero mudança de comportamento, só
  deduplicação.
- **Anti-throttle workers**: cada módulo publica `DEFAULT_MAX_WORKERS`
  calibrado abaixo do limiar de throttling observado (NOMADS/filter 2,
  cloud 4, RDA 1). `fetch_batch` paraleliza steps via
  `ThreadPoolExecutor` dimensionado a `min()` desses defaults ao longo
  da priority list. CLI ganhou `--max-workers`; config INI aceita
  `max_workers`.
- **Registry**: `register_source(name, fn, *, max_workers=1)` — default
  conservador (serial) para fontes custom não caracterizadas.
- **RDA específico**: filenames no formato validity-time
  (`gfs.0p25.{YYYYMMDDHH}.f{FFF}.grib2`), mas o arquivo final é salvo
  com o nome canônico NOMADS/AWS (`gfs.t{HH}z.pgrb2.0p25.f{FFF}`)
  para o resto do pipeline não precisar saber qual fonte ganhou.
  `$SHARKTOPUS_RDA_COOKIE` serve requests autenticados.
  Guard `EARLIEST = 2015-01-15` levanta `SourceUnavailable` cedo.
- **Tests**: 34 novos (`test_sources_mirrors.py` com parametrize sobre
  as 4 fontes, `test_batch_parallel.py` com threading real). Suite
  total: **143 passam, 1 skip**.
- `docs/ORIGIN.md` atualizado com tabelas por fonte + diferenças
  intencionais (full download vs byte-range, worker defaults).
  `CHANGELOG.md` com entrada Unreleased. `README.md` com tabela
  comparativa das 6 fontes + tabela de workers.

### 2026-04-17 (e) — Empacotamento wgrib2 + CI de wheel
- **Resolver** (`sharktopus._wgrib2`): ordem explicit → `$SHARKTOPUS_WGRIB2` →
  bundled em `_bin/` → `$PATH`. `WgribNotFoundError` com mensagem que aponta
  pros três caminhos de instalação. Todas as funções `grib.*` passaram a
  usar `wgrib2: str | None = None` e resolver internamente.
- **Hatch hook** (`hatch_build.py`): detecta binário em `src/sharktopus/_bin/`
  em build time e troca o wheel de `py3-none-any` para `py3-none-<platform>`.
  Sdist segue puro e exclui o binário.
- **Scripts**:
  - `scripts/build_wgrib2.sh` — compila upstream NOAA com
    `USE_AEC=0 USE_OPENJPEG=0 USE_NETCDF3=0 USE_NETCDF4=0` (adaptado do
    `~/CONVECT/images/azure_gfs/build_wgrib2.sh`); resultado depende só
    de libs base-system.
  - `scripts/bundle_wgrib2.sh` — dev local: materializa binário em
    `_bin/` (via `$SHARKTOPUS_WGRIB2_SRC`, `$SHARKTOPUS_WGRIB2_URL`, ou
    fallback CONVECT), valida portabilidade via `ldd` com whitelist,
    roda `python -m build --wheel` e tenta `auditwheel repair`.
- **CI** (`.github/workflows/build-wheels.yml`): Linux x86_64 dentro de
  `quay.io/pypa/manylinux_2_28_x86_64`, instala gfortran, compila wgrib2,
  chama o bundle script, sobe `sharktopus-linux-x86_64/*.whl` como artifact.
  Não publica no PyPI ainda — será manual depois da primeira inspeção.
  Trigger: push de tag `v*` ou workflow_dispatch. aarch64/macOS stubados.
- **Comportamento novo em `grib.verify`**: passou a levantar `GribError`
  quando o arquivo é não-vazio mas wgrib2 parseia zero registros (wgrib2
  v3.1.3 não sinaliza erro em input corrompido; antes isso virava `0`
  silenciosamente).
- **Smoke end-to-end local**: `bundle_wgrib2.sh` usando fallback CONVECT
  produziu `sharktopus-0.1.0-py3-none-linux_x86_64.whl` com binário
  dentro; `pip install` em venv scratch resolveu o binário bundled,
  `grib.verify` + `grib.crop` rodaram num GFS 0.25° real
  (`/data/comum/datasets/gfsdata/fcst/2023011318/.../gfs.0p25.2023011318.f006.grib2`).
- 10 novos testes no `test_wgrib2_resolver.py`; suite agora em **61 passam,
  1 skip**.
- **Blocker B1 resolvido.** Commits `29c5c42`, `ae3eb55`.

### 2026-04-17 (d) — Camada 1 iniciada: `nomads` + `nomads_filter`
- Sub-pacote `sharktopus.sources` criado com três módulos:
  - `base.py` — exceção `SourceUnavailable`, `canonical_filename`,
    validadores `validate_cycle`/`validate_date`, `check_retention` e
    `stream_download` em stdlib puro (`urllib.request`) com retries,
    atomic rename via `.part` e mapeamento 404→`SourceUnavailable`.
  - `nomads.py` — download full-file de `nomads.ncep.noaa.gov`, com
    crop local opcional (usa `grib.crop` quando `bbox=` é passado).
    Aplica a janela de retenção (~10 dias) antes de tocar a rede.
  - `nomads_filter.py` — subset server-side via `filter_gfs_0p25.pl`
    (e `..._1hr.pl` com `hourly=True`). Aceita nomes de níveis estilo
    wgrib2 (`"500 mb"`, `"2 m above ground"`) e converte para
    `lev_*` via `level_to_param`.
- Escolha de design: **sem variáveis/níveis hardcoded** (todo script
  CONVECT carregava cópias privadas do conjunto WRF-input de 13 vars/
  48 níveis). `nomads_filter.fetch_step` exige `variables=` e `levels=`
  do caller — a biblioteca fica útil para workflows fora do WRF.
- 25 testes novos (URL, retenção, retry, 404, conversão de nível,
  fluxo download+crop com `urlopen` monkeypatched). Total: **41 passam,
  2 skip** (wgrib2 fora do PATH no venv). Versão bumpada para 0.1.0.
- Diferenças vs CONVECT documentadas em `docs/ORIGIN.md` seção Layer 1:
  exceções tipadas, stdlib-only, sem I/O escondido em `parse_idx`,
  parametrização explícita de variáveis/níveis.
- Commit `618b21e` em `~/projetos/sharktopus/`.

### 2026-04-17 (a) — Documento de acompanhamento criado
- Contexto coletado de `STATUS.md`, `docs/cloud_setup_report.md`,
  `orchestration/deploy/*.py`, `containers/fetcher/scripts/menu_gfs.py`.
- Versão inicial do plano escrita.

### 2026-04-17 (c) — Camada 0 implementada em `~/projetos/sharktopus/`
- Pacote criado em `~/projetos/sharktopus/` com layout src, hatchling,
  Python ≥ 3.10, `pip install -e .[test]` funcional.
- Módulo `sharktopus.grib` com as 6 utilidades consolidadas:
  `verify, crop, filter_vars_levels, parse_idx, byte_ranges, rename_by_validity`.
  Mais `have_wgrib2()` como auxiliar e `GribError`/`IdxRecord` como tipos.
- Diferenças intencionais vs CONVECT documentadas em `docs/ORIGIN.md`:
  bbox é tupla `(lon_w, lon_e, lat_s, lat_n)`, falhas viram `GribError`
  (não `-1`/`None`), `parse_idx` é função pura sem HTTP.
- Testes: 14 passam (parse_idx, byte_ranges, validações de bbox/inputs,
  erros quando wgrib2 ausente); 2 skip quando wgrib2 fora do PATH.
- Git inicializado com commit inicial `dd93ac7`.
- **Blocker B5 resolvido**: as 6 utilidades agora têm um único home; as 5
  fontes da Camada 1 vão consumir daqui em vez de duplicar.

### 2026-04-17 (b) — Estratégia revisada: construção em camadas, local primeiro
- **Descoberta**: as 5 fontes de download não dependem do deploy serverless.
  Cada uma lê direto do bucket/endpoint público e corta com wgrib2 local.
  Isso permite uma primeira versão "zero-cloud" da biblioteca.
- **Arquitetura em 6 camadas** definida (grib utils → sources locais →
  orquestrador → cloud invoke → deploy → CLI). Camadas 0–2 não exigem
  credenciais nem deploy.
- **API alvo inspirada no Herbie** formalizada: top-level `st.fetch(...)`,
  submódulos de fonte para uso fino, utilidades wgrib2 standalone.
- **Caso canônico de validação** escolhido: 2024-01-21 00Z, fxx=6,
  bbox=(-45,-40,-25,-20) — mesmo caso do pipeline de radar DA, já temos
  GRIB2 de referência no disco.
- **Levantamento de código duplicado**: identificadas 6 utilidades wgrib2/idx
  repetidas em 3–4 scripts (B5) — unificação vira primeiro PR da biblioteca.
- **Próxima sessão**: Camada 0 (`src/sharktopus/grib.py` + `pyproject.toml` mínimo
  + `tests/test_grib.py`). Critério: `pytest` passa no caso canônico.
