---
name: code-review-skills
description: Senior Staff Engineer code review framework for architecture, Python, async concurrency, security, performance, production readiness, web scraping, and AI/LLM engineering.
---

# Senior Code Review Framework
Core Philosophy
Semantics-driven, not grep-driven.

Senior code reviews understand behavior, architecture, concurrency, maintainability, and business logic. Pattern matching only finds syntax—it cannot understand data flow, intent, or system-level implications.

python

# Grep catches this

query = f"SELECT * FROM users WHERE id = {user_id}"

# Grep cannot evaluate this

query = build_query(user_input)
db.execute(query)  # Is build_query() safe? Only reading the function tells you.
The reviewer must trace data flow, understand context, and evaluate design decisions—not just scan for regex matches.

The 20 Dimensions

1. Correctness
Does the code do what it claims to do?

Edge cases handled?
Off-by-one errors?
Empty input behavior?
Null/None handling?
Boundary conditions?
Return value contracts honored?
Precondition/postcondition violations?
Red flags:

Assumptions about input format without validation
Missing early returns for invalid states
Silent failures
2. Architecture
Is the structure maintainable six months from now?

Check:

text

□ Module responsibilities are clear and singular
□ Dependency direction flows inward (domain → infrastructure, never reverse)
□ Layering is enforced (API → Service → Repository → Model)
□ No circular dependencies
□ Package structure reflects domain boundaries
□ Configuration is externalized, not hardcoded
□ Dependency injection is used for swappable components
□ Extension points exist for anticipated changes
Bad:

text

main.py  (5000 lines)
Good:

text

api/
services/
repositories/
models/
config/
utils/
Key question: If I need to swap the database, change the caching layer, or add a new API endpoint, how many files do I touch?

1. SOLID and Design Patterns
Every production review should evaluate:

Principle
Question
Single Responsibility Does this class/module have one reason to change?
Open/Closed Can I extend behavior without modifying existing code?
Liskov Substitution Can subclasses replace parents without breaking contracts?
Interface Segregation Are clients forced to depend on methods they don't use?
Dependency Inversion Do high-level modules depend on abstractions, not concretions?

Example violation:

python

class AmazonScraper:
    def scrape(self):        # Core responsibility
    def parse_html(self):    # Different responsibility
    def save_json(self):     # Different responsibility
    def retry(self):         # Cross-cutting concern
    def log(self):           # Cross-cutting concern
    def rotate_proxy(self):  # Infrastructure concern
    def run_cli(self):       # Presentation concern
This class has 7 reasons to change. Split it.

1. Python Idioms
Python-specific correctness and style:

text

□ Mutable default arguments avoided
□ Late binding closures understood and handled
□ Context managers used for resource acquisition
□ Generators used for large sequences
□ Dataclasses/NamedTuples/TypedDict for structured data
□ Protocol typing for structural subtyping
□ ABCs for explicit interfaces where needed
□ __slots__ considered for memory-critical classes
□ Weakrefs for caches that shouldn't prevent GC
□ Descriptors understood for property-like behavior
Common traps:

python

# Trap: Mutable default

def process(items=[]):  # Shared across calls!
    items.append(1)
    return items

# Trap: Late binding closure

funcs = [lambda: i for i in range(5)]

# All return 4, not 0,1,2,3,4

# Correct

funcs = [lambda i=i: i for i in range(5)]
5. Async and Concurrency
Critical for FastAPI, Playwright, LLM, and scraping code:

Shared State:

text

□ No unprotected shared mutable state
□ Race conditions identified and mitigated
□ Lock correctness (acquire/release pairs, no deadlocks)
□ Semaphore bounds appropriate
Task Management:

text

□ asyncio.gather() exception handling considered
□ return_exceptions=True where appropriate
□ Task cancellation safety
□ No leaked tasks (all tasks awaited or cancelled)
□ Timeout handling on all external calls
Resource Exhaustion:

text

□ Connection pool limits set and respected
□ Semaphore limits match resource capacity
□ Backpressure mechanisms exist
□ No unbounded task creation
Blocking Prevention:

text

□ No time.sleep() in async code
□ No requests library in async code
□ No CPU-bound work without run_in_executor
□ No synchronous file I/O in hot paths
Example review:

python

await asyncio.gather(*tasks)
Questions to answer:

Can one exception cancel all other tasks?
Should return_exceptions=True be used?
What's the memory footprint if 10,000 tasks are created?
Is there a semaphore limiting concurrency?
6. Performance and Complexity
text

□ Time complexity appropriate for data sizes
□ Space complexity appropriate for data sizes
□ No unnecessary O(n²) when O(n) possible
□ No premature optimization without profiling
□ Hot paths identified and optimized
□ Caching used where appropriate
□ Algorithm choice justified
Tools: cProfile, line_profiler, memray

Complexity scoring:

text

Cyclomatic complexity per function:
  1-10:  Good
  11-20: Warning
  21+:   Refactor
7. Memory Behavior
Critical for scraping and LLM workloads:

text

□ No huge lists accumulating in memory
□ No large dicts held longer than necessary
□ Generators used for streaming data
□ Chunking used for batch processing
□ No unnecessary object copying
□ Large JSON parsed with ijson or streaming
□ String concatenation uses join(), not +
□ Reference cycles avoided or broken
Pattern to watch:

python

# Bad: Loads everything into memory

results = [process(item) for item in huge_list]

# Good: Streams processing

def process_stream(items):
    for item in items:
        yield process(item)
8. Security
Beyond SQL injection—think systematically:

text

□ Input validation at trust boundaries
□ Output encoding for appropriate context
□ Authentication/authorization checks
□ Secrets not in code or logs
□ Dependency vulnerabilities (pip-audit)
□ File path traversal prevention
□ Command injection prevention
□ Deserialization safety
□ Rate limiting on exposed endpoints
□ CORS configuration
For AI/LLM code:

text

□ Prompt injection prevention
□ User input sandboxed from system prompts
□ Output filtering for sensitive data leakage
9. Reliability and Fault Tolerance
text

□ Retries with exponential backoff
□ Circuit breakers for external dependencies
□ Timeouts on all external calls
□ Graceful degradation defined
□ Idempotency for retryable operations
□ Bulkhead isolation between services
□ Failure modes documented
Example:

python

# Fragile

response = await client.post(url, data)

# Resilient

async for attempt in AsyncRetrying(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((TimeoutError, ConnectionError)),
):
    with attempt:
        response = await client.post(url, data, timeout=30)
10. Observability
text

□ Structured logging (JSON format)
□ Correlation IDs for request tracing
□ Appropriate log levels (DEBUG/INFO/WARN/ERROR)
□ No sensitive data in logs
□ Log sampling for high-volume paths
□ Metrics emitted for key operations
□ Distributed tracing spans
□ Health check endpoints
Bad:

python

logger.info(f"Processing user {user.email} with password {user.password}")
Good:

python

logger.info(
    "processing_user",
    extra={
        "user_id": user.id,
        "correlation_id": request.correlation_id,
        "operation": "process_payment",
    }
)
11. Testability
text

□ Pure functions separated from side effects
□ Dependencies injected, not imported directly
□ No global state that prevents isolation
□ Boundaries abstracted for mocking
□ Test cases cover happy path, edge cases, errors
□ Integration tests for external dependencies
□ Property-based tests for data transformations
Question: Can I unit-test this function without hitting a database, network, or filesystem?

1. Scalability
Ask: What happens at each scale?

text

100 users:    ___________
1,000 users:  ___________
10,000 users: ___________
1M users:     ___________
Check:

text

□ No O(n) operations per request that should be O(1)
□ Database queries scale with result set, not total data
□ Caching reduces load proportionally
□ No single points of contention (locks, single queues)
□ Horizontal scaling possible (stateless where needed)
□ Pagination implemented for list endpoints
13. API Design
text

□ Function names describe behavior, not implementation
□ Parameter count ≤ 4 (use dataclasses for more)
□ Return types explicit and consistent
□ Type hints on all public functions
□ Default arguments are safe (no mutable defaults)
□ Exception strategy consistent (custom exceptions?)
□ Error types distinguishable by callers
Bad:

python

def process(x, y, z, a, b, c):
Good:

python

def process_invoice(
    invoice: Invoice,
    currency: Currency,
    retry_count: int = 3,
) -> ProcessingResult:
14. Database Usage
Beyond N+1 queries:

text

□ Transactions used for multi-step operations
□ Indexes exist for query patterns
□ Connection pooling configured
□ Isolation level appropriate for use case
□ Batch inserts instead of loop inserts
□ Prepared statements for repeated queries
□ Pagination (cursor-based preferred for large sets)
□ Connection cleanup guaranteed (context managers)
□ Query plans reviewed for complex queries
□ Read replicas used for read-heavy workloads
15. Web Scraping Concerns
text

□ Retries with exponential backoff
□ Respect for robots.txt
□ Rate limiting per domain
□ Request fingerprinting avoidance
□ TLS fingerprint handling
□ Header rotation
□ Cookie/session management
□ Proxy rotation with health checking
□ Captcha detection and handling strategy
□ Selector fragility (prefer data attributes over class names)
□ Timeout on all requests
□ Response validation before parsing
16. AI/LLM Engineering Concerns
text

□ Prompt injection prevention
□ User input separated from system prompts
□ Hallucination handling (verification, confidence thresholds)
□ Embedding caching strategy
□ Chunk size optimized for use case
□ Token usage tracked and optimized
□ Context window management
□ Vector DB configuration (index type, similarity threshold)
□ Streaming responses for user-facing applications
□ LLM API retry with backoff
□ Cost optimization (model selection, caching)
□ Rate limit handling per provider
□ Fallback strategies for provider outages
□ Output parsing with validation (not raw string manipulation)
17. Refactoring Opportunities
For each suggestion, provide:

Refactor: [Name]
Before:(paste original code)

After:(paste refactored code)

Benefits:

Benefit 1
Benefit 2
Trade-offs:

Trade-off 1
Complexity change: X → Y

Performance impact: None / ±X%

1. Interview Discussion Points
Generate 5-10 questions a senior interviewer would ask about this code:

Interview Questions This Code Might Generate
[Category] Question?
What they're looking for: Expected answer
[Category] Question?
What they're looking for: Expected answer
Categories: Architecture, Concurrency, Performance, Trade-offs, Edge Cases, Scalability

1. Under-the-Hood Explanations
For non-trivial code, explain the mechanics:

How This Actually Works
Code:async with sem: await do_work()

What happens:

sem.aenter() calls sem.acquire()
If counter > 0, decrement and continue
If counter == 0, create a Future, add to waiters queue, yield
When another task calls release(), it pops a waiter and sets Future result
Event loop resumes the waiting task
sem.aexit() calls sem.release(), incrementing counter
Memory diagram:Semaphore: counter: 0 waiters: [Future1, Future2, Future3] ↓ Event Loop: suspends coroutine, will resume when Future resolved

State machine:[acquire] → counter > 0 → [decrement, continue] ↓ counter == 0 → [create Future, enqueue, suspend] ↓ [release called] → [dequeue Future, set result] → [resume]

This is how engineers become senior—understanding what the abstraction hides.

1. Production Readiness Checklist
text

□ Feature flags for new functionality
□ Configuration externalized (env vars, config files)
□ Secrets in vault/secret manager, not code
□ Metrics emitted (latency, errors, throughput)
□ Distributed tracing configured
□ Health check endpoints (/health, /ready)
□ Graceful shutdown handles in-flight requests
□ Retry with backoff for external calls
□ Circuit breakers for downstream services
□ Idempotency keys for critical operations
□ Monitoring dashboards created
□ Alerting rules defined (error rate, latency, saturation)
□ Runbook documentation exists
□ Rollback procedure tested
□ Load tested at expected peak
□ Failure injection tested (chaos engineering)
Review Output Format
Code Review: [PR/File Name]
Summary
[2-3 sentence overview]

Scores
Dimension Score Notes
Correctness X/10 
Architecture X/10 
SOLID X/10 
Python Idioms X/10 
Async/Concurrency X/10 
Performance X/10 
Memory X/10 
Security X/10 
Reliability X/10 
Observability X/10 
Testability X/10 
Scalability X/10 
API Design X/10 
Database X/10 
Domain (Scraping/AI) X/10 
Production Readiness X/10 
Overall: X.X/10

Critical Issues
[Must fix before merge]

Warnings
[Should fix, but not blocking]

Suggestions
[Nice to have improvements]

Refactoring Opportunities
[Detailed before/after with trade-offs]

Under-the-Hood
[Deep explanations of non-trivial mechanisms]

Interview Questions
[5-10 questions this code might generate]

Production Readiness
[Checklist status]

Recommended Toolchain
Replace
text

flake8, pylint, black (separate tools, slower)
With
text

ruff          # Linting + formatting + import sorting (Rust-fast)
pyright       # Type checking (strict mode)
bandit        # Security linting
vulture       # Dead code detection
deptry        # Missing/unused dependency detection
pip-audit     # Vulnerability scanning
radon         # Complexity metrics
xenon         # Complexity enforcement (configurable thresholds)
pytest-cov    # Coverage reporting
memray        # Memory profiling
Why AST over Grep
Grep
AST
Misses multi-line patterns Parses structure correctly
False positives from comments/strings Understands syntax context
Cannot analyze data flow Enables control-flow analysis
Cannot build call graphs Enables dependency analysis
Regex fragility Structural reliability

Anti-Patterns: What Not to Do
Don't grep for this:
bash

grep "def.*,.*,.*,.*,.*," .
Misses:

python

def func(
    a,
    b,
    c,
    d,
):
Don't grep for this:
bash

grep "except:"
Misses:

python

except Exception:  # Often equally problematic
    pass
Don't grep for this:
bash

grep "for.*query"
Produces: False positives and false negatives

Do use AST parsing:
python

import ast

class FunctionParamCounter(ast.NodeVisitor):
    def visit_FunctionDef(self, node):
        if len(node.args.args) > 5:
            print(f"{node.name}:{node.lineno} has {len(node.args.args)} params")

class BareExceptFinder(ast.NodeVisitor):
    def visit_ExceptHandler(self, node):
        if node.type is None:
            print(f"Bare except at line {node.lineno}")
        elif node.type.id == 'Exception' and not node.name:
            print(f"Broad Exception at line {node.lineno}")
Applying This Framework
When reviewing code, work through dimensions in this order:

Correctness first — Does it work?
Security second — Is it safe?
Reliability third — Will it fail gracefully?
Architecture fourth — Is it maintainable?
Performance/Memory fifth — Is it efficient?
Everything else — Polish and production readiness
Stop early for critical issues. Don't polish code that's fundamentally broken.

