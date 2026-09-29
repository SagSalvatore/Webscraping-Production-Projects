# Talabat → `s3://rii-data-dump`

Move the Talabat data off `C:` into S3, keep the pipeline runnable, and be able
to bring any file back. Measured on 2026-09-22: **15.2 GB in 3,439 files**.
**15.0 GB is uploaded**, **14.3 GB then leaves the disk**, and **788 MB in 33
files stays** — the current deliverable, the paid caches and the vocabularies
the pipelines read every month.

Nothing is deleted until it has been uploaded **and** verified. Every script
here is a dry run unless you pass `--execute`.

## The layers

Named after what the data *is*, not the folder it sits in.

| Layer | What it holds | Size | Kept locally? |
|---|---|---:|---|
| `bronze/` | exactly what was captured: zone crawls, raw menus, raw Google/Serper/Apify/LLM responses, logo URL lists | 3.00 GB | no, except the paid caches |
| `silver/` | what our rules made from bronze: sanitized menus, std_term + ingredient mapping, kNN and manual review decisions, classification, area and address repair, validation reports | 5.41 GB | no |
| `gold/` | what was handed over: the unified deliverable, the monthly Tech exports, the logo packages and manifests | 6.62 GB | current month only |
| `reference/` | the controlled vocabularies every month maps against (`area_list.csv`, the ingredient taxonomy, the std_term taxonomy) | 0.5 MB | yes, always |
| `ops/` | logs and checkpoints, so a past run can still be explained | 9 MB | no |

Bronze is the layer worth keeping longest: re-fetching it costs Serper credits,
Oxylabs bandwidth or is simply impossible once Talabat changes a page.

## The key shape

```
talabat/<layer>/<month>/<stage>/<original path under talabat/>
talabat/reference/<topic>/<file>            # not month-partitioned
```

```
talabat/gold/2026-09/unified/unified/data/talabat_unified_202609.jsonl
talabat/bronze/2026-09/03_menus_raw/menu_refresh/data/refresh_202609/scrape/menu_items.jsonl
talabat/silver/2026-09/03_std_terms/review/menu_refresh/data/refresh_202609/stdterm/finalised.xlsx
talabat/reference/areas/area_list.csv
```

The original path is kept inside the key, so a file's origin is never lost and
`restore_from_s3.py` can put it back exactly where the scripts expect it.

**The month is the month the DATA is about**, read from the file's own name or
folder (`202609`, `refresh_202609`, `August_menu`), never from its timestamp —
a file copied last week can hold July's data. Files that genuinely span months
(taxonomies, the brand caches, the listing universe) go to `_cross_month/`.

Measured spread: 2026-08 5.41 GB · 2026-07 3.61 GB · 2026-06 3.14 GB ·
2026-09 2.44 GB · `_cross_month` 0.44 GB.

## The stages, in pipeline order

The number prefix is the order the work happens in, so the bucket reads like
the process.

| Stage | Layer | What lands there | Comes from |
|---|---|---|---|
| `01_zones` | bronze | zone crawl snapshots, area probes, zone deltas | `listing_scraper/`, `september/data/zone_*` |
| `02_listings` | bronze | collected restaurant URLs, identification input | `data/urls/`, `Restaurant Identifier/`, `url_collector/` |
| `03_menus_raw` | bronze | raw menu scrapes, scrape status, June snapshots | `August_menu/data/menu_items.jsonl`, `menu_refresh/.../scrape/`, `menu/data/snapshots/` |
| `04_enrichment_raw` | bronze | Serper/Google Maps/Apify/Tavily/LLM/translation caches | `August_classification/data*/`, `address_audit/data/serper_*`, `apify/`, `Google_Place/` |
| `05_images_raw` | bronze | logo URL lists, scrape checkpoints | `images/data/*images_confirmed*`, `*logo_targets*` |
| `01_listing_compare` | silver | new-brand vs relisting decisions | `listing_comparison/` |
| `02_sanitize` | silver | cleaned menus, promo dedup, Arabic translation | `*menu_items_clean/final/shipped*`, `sanitization/` |
| `03_std_terms` | silver | mapper output, kNN results, **manual review workbooks** | `menu_refresh/.../stdterm/`, `menu/classification/` |
| `04_ingredients` | silver | ingredient conformance to the 820-term vocabulary | `ingredients/`, `August_menu/*FINALIZED*` |
| `05_classification` | silver | chain / MNC / independent, chain_ids, entity resolution | `August_classification/`, `september/data/sept_chain_ids*`, `map_version2/` |
| `06_areas` | silver | area resolution maps, city fixes, manual findings | `area_classification/`, `unified/data/unified_*_area*` |
| `07_addresses` | silver | smear detection, per-branch verified addresses | `address_audit/` |
| `08_validation` | silver | validation gates, audit CSVs, build reports | `unified/data/unified_*`, `unified/review/` |
| `09_delta` | silver | month-to-month menu deltas and baselines | `menu_refresh/data/`, `menu/data/reports/` |
| `unified`, `exports`, `logos` | gold | the delivered files and their manifests | `unified/data/`, `*_export.json`, `images/data/*logos*` |

## The bucket

`arn:aws:s3:::rii-data-dump` in **us-west-2 (Oregon)**, owned by Tech. Set in
`layout.py` as `BUCKET` and `REGION`.

## Credentials

`boto3` reads them from `talabat/.env` (same file as the Oxylabs and Serper
keys), so add:

```
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-west-2
```

**Where the key comes from:** AWS console → **IAM** → *Users* → your user
(`sagar.singh@mordorintelligence.com`) → **Security credentials** tab → *Access
keys* → **Create access key** → "Application running outside AWS". The secret
is shown **once** — copy it straight into `.env`, and never into a file that
gets committed. If that button gives an AccessDenied, your IAM user cannot
create its own keys and Tech has to issue one.

Two console errors that look related but are not: `iam:GetLoginProfile` is the
console-password widget, and `iam:ListServiceSpecificCredentials` is the *API
keys* tab (CodeCommit and Keyspaces). Neither one governs access keys.

### If self-service keys are denied

Any of these works; `boto3` reads all of them from `talabat/.env`.

1. **Tech issues a long-lived access key** for the IAM user — simplest, and
   what the policy below is written for.
2. **Tech sends temporary credentials** (`aws sts get-session-token`, or
   assume-role output). Three values instead of two: set `AWS_SESSION_TOKEN`
   as well. They expire — up to 36 hours for a session token, usually 1–12 for
   a role — so upload in one sitting, or ask again when they lapse.
3. **IAM Identity Center (SSO)**, if the org runs it: Tech assigns a permission
   set and the AWS CLI (2.32+, not installed here) fetches short-term keys with
   `aws login` / `aws sso login`. Cleanest, but needs the CLI and the
   `SignInLocalDevelopmentAccess` policy.
4. **No API credentials at all** — fall back to browser upload:
   `stage_for_console_upload.py` builds a folder tree whose layout *is* the S3
   key layout (hardlinks, so it costs no extra disk), and you drag that one
   folder into the S3 console. Checksums cannot be verified that way, so
   `prune_local.py` will refuse to delete anything uploaded by hand until a
   later `verify_s3.py` run with read access confirms it.

Ignore the `s3files:ListFileSystems` error in the console: it comes from the
S3 console's *File systems* tab, which is a different service. Uploading
objects does not need it.

### What to ask Tech for

The archive makes five calls. This is the least-privilege policy that covers
them, scoped to this bucket and the `talabat/` prefix:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "ListTheBucketUnderTalabat",
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:ListBucketMultipartUploads"],
      "Resource": "arn:aws:s3:::rii-data-dump",
      "Condition": { "StringLike": { "s3:prefix": ["talabat/*", "talabat"] } } },
    { "Sid": "ReadWriteTalabatObjects",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:GetObjectAttributes",
                 "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"],
      "Resource": "arn:aws:s3:::rii-data-dump/talabat/*" }
  ]
}
```

`s3:DeleteObject` is deliberately **not** requested: nothing here deletes from
S3, only from the local disk. Bucket-level settings (versioning, lifecycle,
encryption) need the bucket owner, so if `setup_bucket.py` reports DENIED, send
Tech `bucket_lifecycle.json` and ask them to enable versioning — that is the
undo for an accidental overwrite.

## Run order

```bash
python -m pip install boto3 python-dotenv     # done
python inventory.py                 # 1. plan: what goes where (no AWS needed)
python check_access.py              # 2. do the credentials allow the 5 calls?
python setup_bucket.py --execute    # 3. versioning, encryption, lifecycle (may be Tech's)
python upload_to_s3.py --execute --max-gb 2   # 4. a first batch, then drop --max-gb
python verify_s3.py --deep 25       # 5. size + sha256, and 25 full downloads
python prune_local.py               # 6. read the plan
python prune_local.py --execute     #    then free the disk
```

Bring anything back:

```bash
python restore_from_s3.py --path unified/data/talabat_unified_202608.jsonl --execute
python restore_from_s3.py --prefix menu_refresh/data/refresh_202609/ --execute
```

## What is never deleted

`prune_local.py` deletes a file only when **all** of these hold:

1. the inventory marked it `archive` — never `archive_keep`, never `skip`;
2. `verify_s3.py` listed it as ok, in a run newer than the upload;
3. its size and SHA-256 on disk still match what was uploaded, re-checked at
   the moment of deletion.

`archive_keep` is the list the pipelines need every month, so it is uploaded
**and** kept: the vocabularies, the Serper/translation caches (credits and
Oxylabs bandwidth to rebuild), the current unified deliverable, the monthly
exports and logo manifests. Code, tests and documentation are `skip` — this
archive never touches them.

Deletions are recorded in `data/deleted_index.csv` (path, size, sha256, the
`s3://` URI), so a deleted file is still findable by name.

## Safety in the bucket

Set by `setup_bucket.py`: Block Public Access on all four switches, SSE-S3
default encryption, **versioning on** (an accidental overwrite stays
recoverable), and `bucket_lifecycle.json`, which also aborts incomplete
multipart uploads after 7 days so failed transfers stop costing money.

Lifecycle: bronze → Glacier Instant Retrieval at 30 days; silver → Standard-IA
at 30, Glacier IR at 180; ops → Standard-IA at 30 and expires at 730 days; gold
and reference stay Standard. Transitions apply only to objects over 128 KB,
because smaller ones are billed at 128 KB in those classes.

**Cost:** about **$0.35/month** on day one, falling to **~$0.23/month** once
the lifecycle rules apply — plus a one-off ~$0.01 in PUT requests. Egress is
free until you download, so restores are the only thing worth thinking about.

## Current state (Sept 23 2026)

All 1,647 objects uploaded through the browser and verified by
`verify_console.py`; 2,660 local files / 14.3 GB pruned. Two things differ from
the plan and are deliberate:

- **bronze sits at `talabat/bronze/bronze/`** — the console appended the
  selected folder's name inside a prefix of the same name. `remap_keys.py`
  repointed the manifest at the real keys, so verify and restore both work.
  Fix it server-side with a copy (no re-upload) once credentials exist.
- **Leftover duplicates** from the Move attempts: `_tmp_move/` (~330 objects)
  and a partial `talabat/bronze/_cross_month/`. Safe to delete in the console.

## Files here

| File | Role |
|---|---|
| `layout.py` | the ONE definition of layer, stage, month, action and S3 key |
| `inventory.py` | scans `talabat/`, writes `data/inventory.csv` — the plan |
| `upload_to_s3.py` | uploads with SHA-256, writes `data/upload_manifest.jsonl` |
| `verify_s3.py` | proves the copy, writes `data/verify_report.json` |
| `prune_local.py` | deletes only what is verified, writes `data/deleted_index.csv` |
| `restore_from_s3.py` | brings files back to their original paths |
| `setup_bucket.py`, `bucket_lifecycle.json` | bucket settings and lifecycle |

## Every month after this

1. run the month's pipeline as usual;
2. `python inventory.py` — the new files classify themselves from their paths;
3. `upload_to_s3.py --month 2026-10 --execute`, `verify_s3.py`, `prune_local.py --execute`.

A new folder that no rule matches shows up as `review` in the inventory and is
never uploaded or deleted until a rule is added for it in `layout.py`.
