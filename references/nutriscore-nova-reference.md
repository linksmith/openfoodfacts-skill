# Nutri-Score, NOVA, and Eco-Score Reference

Read this file when the user asks "how is Nutri-Score calculated?", "why
did this product get a certain grade?", or when you need to explain the
scoring methodology in an analysis.

## Nutri-Score

### What it is

Nutri-Score is a front-of-pack nutrition label used in France, Belgium,
Germany, the Netherlands, Luxembourg, Spain, and Switzerland. It assigns
a letter grade from A (best) to E (worst) based on a product's overall
nutritional quality per 100g.

### How it's calculated

The score is a balance of negative and positive points:

**Negative points (0-10 each, higher is worse):**
- Energy (kJ)
- Sugars (g)
- Saturated fat (g)
- Salt (g)

**Positive points (0-5 each, higher is better):**
- Fibre (g)
- Protein (g)
- Fruits, vegetables, nuts, and legumes (%)

**Final score** = Negative points - Positive points

The final score maps to a letter:

| Score range | Grade | Colour |
|---|---|---|
| -15 to -1 | A | #038141 (dark green) |
| 0 to 2 | B | #85BB2F (light green) |
| 3 to 10 | C | #FECB02 (yellow) |
| 11 to 18 | D | #EE8100 (orange) |
| 19 to 40 | E | #E63E11 (red) |

Note: Thresholds differ for beverages, fats/oils, and cheese. The 2024
revision updated some thresholds.

### Requirements for computation

A product needs all of these to get a Nutri-Score:
- Assigned to a category (so the right thresholds apply)
- Complete nutritional values (energy, fat, saturated fat, sugars, salt,
  fibre, protein)
- Fruits/vegetables/nuts percentage (estimated from ingredients if not
  provided)

Products missing any of these will not have a Nutri-Score on OFF.

### Coverage

Approximately 40-50% of products on OFF have a computed Nutri-Score.
Coverage is highest in France (>60%) and lower in countries where
Nutri-Score is not officially adopted. Products without complete
nutritional data or category assignment will be missing.

## NOVA Classification

### What it is

NOVA is a food classification system based on the degree of industrial
processing. Developed by researchers at the University of Sao Paulo, it
groups all foods into four categories.

### The four groups

**Group 1 — Unprocessed or minimally processed foods**
Foods that have been altered only by processes like washing, peeling,
freezing, or pasteurisation. No added substances.

Examples: fresh fruit, vegetables, eggs, rice, plain yoghurt, fresh
meat, fresh fish, milk, nuts, flour, dried herbs.

**Group 2 — Processed culinary ingredients**
Substances extracted and purified from Group 1 foods, used in cooking
but rarely consumed alone.

Examples: olive oil, butter, sugar, salt, flour, vinegar, honey.

**Group 3 — Processed foods**
Products made by combining Group 1 and Group 2 foods using methods
like canning, bottling, or fermentation. Recognisable as modified
versions of the original food.

Examples: canned vegetables, cheese, cured meats, simple bread (flour,
water, salt, yeast), salted nuts, smoked fish.

**Group 4 — Ultra-processed foods**
Industrial formulations with five or more ingredients, typically including
substances not used in home cooking (high-fructose corn syrup, hydrogenated
oils, protein isolates, emulsifiers, flavour enhancers). Designed for
convenience, hyper-palatability, and long shelf life.

Examples: soft drinks, chips, instant noodles, chicken nuggets, industrial
bread with many additives, reconstituted meat products, sweetened cereals,
ready meals, margarine.

### Why NOVA 4 matters journalistically

Research has linked high consumption of ultra-processed foods (NOVA 4) to
increased risk of obesity, cardiovascular disease, type 2 diabetes, and
some cancers. This is an active area of research and public health debate,
making NOVA data journalistically relevant.

However, NOVA is a classification of processing, not a direct measure of
healthiness. A simple cheese (NOVA 3) with high saturated fat may be
nutritionally worse than a reformulated ultra-processed product (NOVA 4)
with reduced fat and added fibre. Always pair NOVA analysis with
nutritional data for a complete picture.

### How it's assigned on OFF

NOVA classification on OFF is derived from ingredient analysis. The
system looks for ultra-processing markers in the ingredient list
(additives, processing aids, industrial ingredients). Products without
ingredient data cannot be classified.

### Coverage

Approximately 30-40% of products on OFF have a NOVA group. Coverage
depends on having a parsed ingredient list. Missing NOVA data is more
common than missing Nutri-Score.

## Eco-Score

### What it is

Eco-Score rates the environmental impact of food products on a scale from
A (lowest impact) to E (highest impact). It was launched in France in 2021.

### How it's calculated

Based on Life Cycle Analysis (LCA) data from the Agribalyse database,
adjusted for:
- Production system (organic, Label Rouge, etc.)
- Origin of ingredients (transport distance)
- Packaging (recyclability, material)
- Threatened species impact
- National environmental policies

### Coverage

Eco-Score coverage on OFF is lower than Nutri-Score — roughly 20-30% of
products, mostly in France. It requires category assignment and, for full
accuracy, origin and packaging information that many products lack.

For most workshop investigations, Nutri-Score and NOVA are more useful due
to better data coverage. Eco-Score is best used as a supplementary data
point rather than the primary analysis axis.
