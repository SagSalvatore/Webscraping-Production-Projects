#!/usr/bin/env python3
"""
sample_run.py — Validate classifier accuracy on N rows before full run.

Classifies a small sample, tracks REAL token usage from response.usage,
compares new GPT term vs existing col-D term, and prints a full review report.

Outputs → classification/sample_output/
    sample_classified.xlsx    cols A-F (F = match flag vs existing)
    sample_classified.json    full records
    sample_report.txt         statistics + cost breakdown

Usage:
    python talabat/menu/classification/sample_run.py
    python talabat/menu/classification/sample_run.py --n 500
    python talabat/menu/classification/sample_run.py --n 1000 --random
    python talabat/menu/classification/sample_run.py --n 1000 --langsmith

Observability:
    OpenAI Dashboard  → platform.openai.com/logs  (automatic, no setup needed)
    LangSmith         → smith.langchain.com        (pass --langsmith flag)
                        Requires LANGCHAIN_API_KEY in talabat/.env
"""

import argparse
import asyncio
import json
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path

import openpyxl
import polars as pl
import xlsxwriter
from dotenv import load_dotenv
from loguru import logger
from openai import AsyncOpenAI, RateLimitError, APITimeoutError, APIConnectionError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
    RetryError,
)
import logging

# ── Paths ────────────────────────────────────────────────────────────────────
HERE        = Path(__file__).parent
OUT_DIR     = HERE / "sample_output"
OUT_XLSX    = OUT_DIR / "sample_classified.xlsx"
OUT_JSON    = OUT_DIR / "sample_classified.json"
OUT_REPORT  = OUT_DIR / "sample_report.txt"

load_dotenv(HERE.parent.parent / ".env")

# ── Config ───────────────────────────────────────────────────────────────────
OPENAI_MODEL  = "gpt-4o-mini"
BATCH_SIZE    = 50
CONCURRENCY   = 20       # conservative for sample run
RPM           = 4500
DESC_MAXLEN   = 120

# gpt-4o-mini pricing
PRICE_INPUT  = 0.150 / 1_000_000   # $/token
PRICE_OUTPUT = 0.600 / 1_000_000

# ── Loguru setup ─────────────────────────────────────────────────────────────
logger.remove()
logger.add(
    sys.stdout, level="INFO",
    format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
)

# ── Import shared utilities from main script ──────────────────────────────────
sys.path.insert(0, str(HERE))
from menu_classifier import AsyncTokenBucket, build_system_prompt  # noqa: E402


# ── Classify function with usage tracking ─────────────────────────────────────
def make_tracked_classifier(
    client: AsyncOpenAI,
    bucket: AsyncTokenBucket,
    sem: asyncio.Semaphore,
    system_prompt: str,
    valid_terms: set[str],
    usage_totals: dict,
):
    """Returns a classify coroutine that accumulates real token counts."""

    @retry(
        retry=retry_if_exception_type(
            (RateLimitError, APITimeoutError, APIConnectionError)
        ),
        wait=wait_random_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(6),
        before_sleep=lambda rs: logger.warning(
            "Rate-limited — retry {} in {:.1f}s",
            rs.attempt_number,
            rs.next_action.sleep,
        ),
        reraise=True,
    )
    async def _call(batch: list[tuple]) -> dict[int, str]:
        await bucket.acquire()

        lines = []
        for idx, name, cat, desc in batch:
            desc_t = (desc or "")[:DESC_MAXLEN].strip()
            lines.append(f"{idx}|{name or ''}|{cat or ''}|{desc_t}")

        async with sem:
            resp = await client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": "\n".join(lines)},
                ],
                temperature=0,
                max_tokens=600,
            )

        # ── Track REAL usage ──────────────────────────────────────────────
        if resp.usage:
            usage_totals["prompt_tokens"]     += resp.usage.prompt_tokens
            usage_totals["completion_tokens"] += resp.usage.completion_tokens
            usage_totals["calls"]             += 1

        raw    = (resp.choices[0].message.content or "").strip()
        result: dict[int, str] = {}
        for line in raw.splitlines():
            line = line.strip()
            if "|" not in line:
                continue
            idx_str, _, term = line.partition("|")
            try:
                row_idx = int(idx_str.strip())
                term    = term.strip()
                if term in valid_terms:
                    result[row_idx] = term
                elif term.lower() in {t.lower() for t in valid_terms}:
                    matched = next(t for t in valid_terms if t.lower() == term.lower())
                    result[row_idx] = matched
                else:
                    result[row_idx] = "Others"
            except ValueError:
                pass
        return result

    return _call


# ── Load sample rows ──────────────────────────────────────────────────────────
def load_sample(n: int, random_sample: bool) -> list[tuple]:
    logger.info("Loading {} ...", (HERE / "Complete_Menu.xlsx").name)
    wb = openpyxl.load_workbook(
        HERE / "Complete_Menu.xlsx", read_only=True, data_only=True
    )
    ws = wb.active
    all_rows = []
    for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True)):
        name  = str(row[0]).strip() if row[0] is not None else ""
        cat   = str(row[1]).strip() if row[1] is not None else ""
        desc  = str(row[2]).strip() if row[2] is not None else ""
        exist = str(row[3]).strip() if len(row) > 3 and row[3] is not None else ""
        all_rows.append((i, name, cat, desc, exist))
    wb.close()
    logger.info("Total rows: {:,}", len(all_rows))

    if random_sample:
        sample = random.sample(all_rows, min(n, len(all_rows)))
        logger.info("Random sample: {:,} rows", len(sample))
    else:
        sample = all_rows[:n]
        logger.info("First {:,} rows selected", len(sample))

    return sample


# ── Write outputs ─────────────────────────────────────────────────────────────
def write_outputs(rows: list[tuple], results: dict[int, str]) -> None:
    OUT_DIR.mkdir(exist_ok=True)

    # ── Excel with match column ────────────────────────────────────────────
    logger.info("Writing Excel -> {}", OUT_XLSX.name)
    wb  = xlsxwriter.Workbook(str(OUT_XLSX))
    ws  = wb.add_worksheet("Sample")

    hdr_fmt    = wb.add_format({"bold": True, "bg_color": "#4472C4", "font_color": "#FFFFFF"})
    match_fmt  = wb.add_format({"bg_color": "#C6EFCE", "font_color": "#276221"})  # green
    differ_fmt = wb.add_format({"bg_color": "#FFEB9C", "font_color": "#9C5700"})  # amber
    others_fmt = wb.add_format({"bg_color": "#FFC7CE", "font_color": "#9C0006"})  # red

    headers = ["Item Name", "Category", "Description",
               "existing_std_term", "new_std_term", "match_flag"]
    for col, h in enumerate(headers):
        ws.write(0, col, h, hdr_fmt)

    ws.set_column(0, 0, 35)
    ws.set_column(1, 1, 25)
    ws.set_column(2, 2, 45)
    ws.set_column(3, 3, 25)
    ws.set_column(4, 4, 25)
    ws.set_column(5, 5, 15)

    for r, (row_idx, name, cat, desc, exist) in enumerate(rows):
        new_term = results.get(row_idx, "")
        match    = "MATCH" if new_term == exist else "DIFFERS"
        fmt      = (others_fmt if new_term == "Others"
                    else match_fmt if match == "MATCH"
                    else differ_fmt)
        ws.write(r + 1, 0, name)
        ws.write(r + 1, 1, cat)
        ws.write(r + 1, 2, desc)
        ws.write(r + 1, 3, exist)
        ws.write(r + 1, 4, new_term, fmt)
        ws.write(r + 1, 5, match, fmt)

    wb.close()
    logger.success("Excel saved -> {}", OUT_XLSX.name)

    # ── JSON ──────────────────────────────────────────────────────────────
    out = []
    for row_idx, name, cat, desc, exist in rows:
        new_term = results.get(row_idx, "")
        out.append({
            "item_name":         name,
            "category":          cat,
            "description":       desc,
            "existing_std_term": exist,
            "new_std_term":      new_term,
            "match":             new_term == exist,
        })
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    logger.success("JSON  saved -> {}", OUT_JSON.name)


# ── Print + save report ───────────────────────────────────────────────────────
def print_report(
    rows: list[tuple],
    results: dict[int, str],
    usage_totals: dict,
    elapsed: float,
) -> None:
    total     = len(rows)
    terms     = [results.get(r[0], "") for r in rows]
    others_ct = terms.count("Others")
    matched   = sum(1 for r in rows if results.get(r[0], "") == r[4])  # D == E
    differs   = total - matched

    # Term distribution
    dist = Counter(terms).most_common()

    # Actual cost
    real_cost_in  = usage_totals["prompt_tokens"]     * PRICE_INPUT
    real_cost_out = usage_totals["completion_tokens"] * PRICE_OUTPUT
    real_cost     = real_cost_in + real_cost_out

    # Extrapolated full-run cost
    cost_per_item  = real_cost / max(total, 1)
    full_run_cost  = cost_per_item * 355_727

    lines = [
        "=" * 60,
        f"SAMPLE RUN REPORT - {total:,} rows",
        "=" * 60,
        "",
        "-- TIMING ------------------------------------------",
        f"  Wall clock     : {elapsed:.1f}s",
        f"  API calls      : {usage_totals['calls']}",
        f"  Rows/second    : {total / elapsed:.1f}",
        "",
        "-- REAL TOKEN USAGE (from response.usage) ----------",
        f"  Prompt tokens  : {usage_totals['prompt_tokens']:,}",
        f"  Output tokens  : {usage_totals['completion_tokens']:,}",
        f"  Total tokens   : {usage_totals['prompt_tokens'] + usage_totals['completion_tokens']:,}",
        "",
        "-- REAL COST ---------------------------------------",
        f"  This sample    : ${real_cost:.4f}",
        f"  Per item       : ${cost_per_item:.6f}",
        f"  Extrapolated -> full 355,727 rows : ${full_run_cost:.2f}",
        f"  Your budget    : $8.00  (buffer: ${8.00 - full_run_cost:.2f})",
        "",
        "-- CLASSIFICATION RESULTS --------------------------",
        f"  Total rows     : {total:,}",
        f"  Others         : {others_ct:,}  ({100*others_ct/total:.1f}%)",
        f"  Classified     : {total - others_ct:,}  ({100*(total-others_ct)/total:.1f}%)",
        "",
        "-- COMPARISON vs EXISTING col D --------------------",
        f"  MATCH          : {matched:,}  ({100*matched/total:.1f}%)",
        f"  DIFFERS        : {differs:,}  ({100*differs/total:.1f}%)",
        "  NOTE: DIFFERS expected - col D uses old 583-term taxonomy,",
        "        col E uses new 102-term taxonomy (different systems).",
        "",
        "-- TERM DISTRIBUTION (top 30) ----------------------",
    ]

    for term, cnt in dist[:30]:
        bar = "#" * min(int(cnt / max(total, 1) * 50), 50)
        lines.append(f"  {term:<35s} {cnt:5d}  {bar}")

    # Items that went to Others
    others_items = [(r[1], r[2], r[4]) for r in rows if results.get(r[0]) == "Others"]
    if others_items:
        lines += ["", "-- ITEMS CLASSIFIED AS 'Others' (spot-check) -------"]
        for name, cat, exist in others_items[:30]:
            lines.append(f"  [{cat[:20]}] {name[:45]:<45s}  was: {exist}")
        if len(others_items) > 30:
            lines.append(f"  ... and {len(others_items)-30} more (see sample_classified.xlsx)")

    # Items where new != existing
    diffs = [
        (r[1], r[2], r[4], results.get(r[0], ""))
        for r in rows
        if results.get(r[0], "") != r[4] and results.get(r[0], "") != "Others"
    ]
    if diffs:
        lines += ["", "-- CLASSIFICATION CHANGES (new != existing) --------"]
        for name, cat, old, new in diffs[:25]:
            lines.append(f"  {name[:40]:<40s}  {old:<25s} -> {new}")
        if len(diffs) > 25:
            lines.append(f"  ... and {len(diffs)-25} more (see sample_classified.xlsx col F=DIFFERS)")

    lines += ["", "=" * 60, f"Output files -> {OUT_DIR}", "=" * 60]

    report_text = "\n".join(lines)
    print(report_text)

    OUT_DIR.mkdir(exist_ok=True)
    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        f.write(report_text)
    logger.success("Report saved -> {}", OUT_REPORT.name)


# ── Main ──────────────────────────────────────────────────────────────────────
async def main(n: int, random_sample: bool, use_langsmith: bool) -> None:
    logger.info("=" * 60)
    logger.info("UAE Menu Classifier — SAMPLE RUN ({:,} rows)", n)
    logger.info("=" * 60)

    # ── Standard terms ────────────────────────────────────────────────────
    df        = pl.read_csv(HERE / "standard_term_table.csv", infer_schema_length=0)
    std_terms = [t.strip() for t in df["Standard Term"].to_list()]
    valid_set = set(std_terms)
    valid_set.add("Others")
    logger.info("Standard terms: {}", len(std_terms))

    system_prompt = build_system_prompt(std_terms)

    # ── OpenAI client ─────────────────────────────────────────────────────
    api_key = os.environ.get("OPEN_AI_API") or os.environ.get("OPENAI_API_KEY", "")
    client  = AsyncOpenAI(api_key=api_key)

    # ── LangSmith (optional) ──────────────────────────────────────────────
    # Uses OpenAI Agents SDK tracing processor + wrap_openai for raw calls.
    # Enable with: --langsmith flag
    # Required in talabat/.env:
    #   LANGSMITH_API_KEY=lsv2_pt_...
    #   LANGSMITH_PROJECT=RAG_LEARNING
    #   LANGSMITH_TRACING=true
    #   LANGSMITH_ENDPOINT=https://api.smith.langchain.com
    if use_langsmith:
        ls_key = os.environ.get("LANGSMITH_API_KEY", "")
        if not ls_key:
            logger.warning(
                "LANGSMITH_API_KEY not found in talabat/.env — LangSmith disabled.\n"
                "  Get your key at: smith.langchain.com/settings"
            )
            use_langsmith = False
        else:
            try:
                from agents import set_trace_processors
                from langsmith.integrations.openai_agents_sdk import OpenAIAgentsTracingProcessor
                from langsmith.wrappers import wrap_openai

                # 1. Hook the Agents SDK tracing layer into LangSmith
                set_trace_processors([OpenAIAgentsTracingProcessor()])

                # 2. Wrap raw AsyncOpenAI client so every chat.completions.create
                #    call also appears as a run in LangSmith (our script doesn't
                #    use Agent/Runner, so wrap_openai covers the raw calls)
                client = wrap_openai(client)

                ls_project = os.environ.get("LANGSMITH_PROJECT", "RAG_LEARNING")
                logger.success(
                    "LangSmith tracing enabled\n"
                    "  Project  : {}\n"
                    "  Dashboard: smith.langchain.com",
                    ls_project,
                )
            except ImportError as e:
                logger.warning(
                    "LangSmith import failed: {}\n"
                    "  Run: python -m pip install -U 'langsmith[openai-agents]'",
                    e,
                )
                use_langsmith = False

    if not use_langsmith:
        logger.info(
            "OpenAI Dashboard active: platform.openai.com/logs  (automatic, no setup needed)"
        )

    # ── Load sample ───────────────────────────────────────────────────────
    rows = load_sample(n, random_sample)

    # ── Run classification ────────────────────────────────────────────────
    batches = [rows[i : i + BATCH_SIZE] for i in range(0, len(rows), BATCH_SIZE)]
    logger.info("Batches: {} × {} items", len(batches), BATCH_SIZE)

    bucket       = AsyncTokenBucket(rpm=RPM)
    sem          = asyncio.Semaphore(CONCURRENCY)
    usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    classify_fn  = make_tracked_classifier(
        client, bucket, sem, system_prompt, valid_set, usage_totals
    )

    results: dict[int, str] = {}
    t_start  = time.time()
    done     = 0
    failed   = 0

    async def process(batch_idx: int, batch: list) -> None:
        nonlocal done, failed
        items = [(r[0], r[1], r[2], r[3]) for r in batch]
        try:
            result = await classify_fn(items)
        except (RetryError, Exception) as e:
            logger.error("Batch {} failed: {}", batch_idx, e)
            result = {r[0]: "Others" for r in batch}
            failed += len(batch)

        for row_idx, *_ in batch:
            if row_idx not in result:
                result[row_idx] = "Others"

        results.update(result)
        done += 1
        pct   = 100 * done / len(batches)
        items_done = done * BATCH_SIZE
        logger.info(
            "Batch {:>3}/{} done ({:.0f}%)  — {:,}/{:,} items",
            done, len(batches), pct, min(items_done, len(rows)), len(rows),
        )

    tasks = [asyncio.create_task(process(i, b)) for i, b in enumerate(batches)]
    await asyncio.gather(*tasks)

    elapsed = time.time() - t_start
    logger.success("Classification done in {:.1f}s", elapsed)

    if failed:
        logger.warning("{} rows fell back to 'Others' due to API errors", failed)

    # ── Write outputs + report ─────────────────────────────────────────────
    write_outputs(rows, results)
    print_report(rows, results, usage_totals, elapsed)


# ── CLI entry point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sample run — classify N menu items")
    parser.add_argument(
        "--n", type=int, default=1000,
        help="Number of rows to classify (default: 1000)",
    )
    parser.add_argument(
        "--random", action="store_true",
        help="Random sample instead of first N rows",
    )
    parser.add_argument(
        "--langsmith", action="store_true",
        help="Enable LangSmith tracing (requires LANGCHAIN_API_KEY in .env)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.n, args.random, args.langsmith))
