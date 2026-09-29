# Restaurant Format Classification — Prompt & Rule Set

**Purpose:** Classify each restaurant in a given list into exactly one of four foodservice formats — **FSR**, **QSR**, **Cafes & Bars**, or **Cloud Kitchen** — using a consistent, defensible decision process.

---

## PROMPT 

```
You are a foodservice market research analyst. Classify each restaurant in the
provided list into exactly ONE of the following four categories:

1. FSR (Full-Service Restaurant)
2. QSR (Quick Service Restaurant)
3. Cafes & Bars
4. Cloud Kitchen

DEFINITIONS:
- FSR: Customers are seated at a table, place an order with a server, and are
  served the food at the table (table service, no self-carry).
- QSR: Convenience- and speed-focused, lower price point. Customers typically
  order at a counter, self-collect, and carry their own food to a table (or take
  it away).
- Cafes & Bars: Includes bars/pubs licensed to serve alcohol, cafes serving
  refreshments/light food, and specialty tea, coffee, dessert, smoothie, or
  juice bars. Primary draw is beverages/light bites and ambience, not a full meal.
- Cloud Kitchen: Delivery/takeout-only operation, prepared in a commercial
  kitchen, with NO dine-in seating or customer-facing storefront for eating in.

DECISION PROCESS (apply in this exact order; stop at first match):

Step 1 — Check for dine-in seating.
  - No dine-in seating at all, delivery/takeout-only, no listed table/seating →
    CLOUD KITCHEN. Stop.

Step 2 — Check for alcohol license or core beverage focus.
  - Has a bar/pub alcohol license, OR is primarily a coffee/tea/juice/smoothie/
    dessert bar where beverages/light bites are the core offering (not a full
    multi-course meal) → CAFES & BARS. Stop.

Step 3 — Check service model.
  - Customers order at a counter/self-checkout, collect their own food/tray,
    and carry it to the table (or it's takeaway-first with minimal seating) →
    QSR. Stop.
  - Customers are seated, a server takes the order, and food is brought to the
    table; full menu with appetizers/mains/desserts → FSR. Stop.

Step 4 — Tie-break rules (use when signals conflict):
  - A place with table service AND alcohol but alcohol is incidental to a full
    meal menu (e.g., a multi-cuisine FSR that also serves wine) → FSR.
  - A place with counter ordering but seating is meant for eating cake/coffee
    only (no full meal) → Cafes & Bars.
  - A delivery-only brand operating "virtual" from inside another restaurant's
    kitchen, with no separate dine-in identity → CLOUD KITCHEN.
  - A food court counter with self-carry trays → QSR.
  - If average price point is high but format is self-service/no table
    service → still QSR, not FSR (price alone does not override service model).

OUTPUT FORMAT (one row per restaurant):
| Restaurant Name | Assigned Category | Key Evidence Used | Confidence (High/Medium/Low) |

For Medium/Low confidence rows, add a one-line note on what additional
information (e.g., website, menu, Zomato/Swiggy listing) would confirm the
classification.

RESTAURANT LIST TO CLASSIFY:
[PASTE LIST HERE]
```

---

## QUICK-REFERENCE DECISION TREE

```
Does it have any dine-in seating?
 ├─ NO  → Cloud Kitchen
 └─ YES
     ├─ Is alcohol served under license, OR is it primarily a coffee/tea/
     │  juice/smoothie/dessert concept?
     │   ├─ YES → Cafes & Bars
     │   └─ NO
     │       ├─ Do customers order at counter & self-carry food to table?
     │       │   ├─ YES → QSR
     │       │   └─ NO (served at table by staff) → FSR
```

---

## SIGNAL CHECKLIST (for analysts/sources like Zomato, Google Maps, Swiggy)

| Signal | FSR | QSR | Cafes & Bars | Cloud Kitchen |
|---|---|---|---|---|
| Dine-in seating | Yes | Yes (often limited) | Yes | No |
| Table service by staff | Yes | No | Sometimes | N/A |
| Self-order/self-carry | No | Yes | Often (counter) | N/A |
| Alcohol license | Sometimes (incidental) | Rare | Common (bars/pubs) | No |
| Core offering | Full multi-course meal | Fast food, combos | Beverages, light bites, desserts | Any cuisine, delivery-packaged |
| Listed on delivery apps only, no address for dine-in | No | No | No | Yes |
| Price point | Mid–high | Low | Low–mid | Varies |
| Typical examples | Multi-cuisine restaurants, fine dining, casual dining chains | Burger/fried chicken/pizza counters, food court stalls | Coffee chains, juice bars, pubs, dessert lounges | Delivery-only brands, "virtual restaurants," dark kitchens |

---

## NOTES ON USE

- For borderline cases (e.g., a cafe with a small full-meal menu, or a QSR brand that also runs as a cloud kitchen sub-brand), flag for manual review rather than forcing a single label — some real-world outlets legitimately operate across two formats (e.g., a QSR brand also fulfilling delivery-only orders under the same name should still be tagged QSR if it has a dine-in storefront).

