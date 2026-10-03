# Interview and quality bar

The interview turns tables into shared meaning. Ask in rounds, as the skill's rules say, and offer the answer the profile suggests, so the user confirms or corrects instead of starting from nothing. Write the answers down as you go; they become descriptions, metrics and instructions.

## Round 1: the questions the model must answer

Start here; everything else follows from these.

- "Which questions should people or an AI assistant be able to answer from these tables? Give 5 to 10, in your own words."
- Make sure the list has at least one trend over time ("how is X doing lately"), one comparison ("which A has the most B") and one ratio or percentage.
- For each question: "What would a good answer look like: a number, a top list, a daily series? Over which period by default?"
- "Which questions do people get wrong today, or which numbers disagree between reports?" These become metrics with exact definitions and warnings in the instructions.

Vague words in the questions ("doing well", "active", "recent", "customer", "revenue") are the terms to define in the next rounds.

## Round 2: grain, keys and joins

- "What is one row in <table>?" (one order, one order line, one event, one daily snapshot)
- "Which column or columns identify a row?" Suggest a candidate key from the profile. For an event log without a key, agree how to count events and distinct entities.
- "Which entity do people mean by <term>?" When the same thing has several identifiers (account, user, device, IP address), decide which one counts and how reliable it is.
- For several tables: "How do these tables join, and can a join multiply rows?" (one-to-many, several rows per key, history tables). For each join, confirm which side is "one" and that its key is unique: the relationship points from the many side to that key.
- "What does this join mean?" Its answer names the relationship. When one table is reached in two ways (departure and arrival airport, buyer and seller), each way gets its own relationship and name.

## Round 3: time

- "Which time zone are the timestamps in, and what does each date or time column mean?" (created, shipped, scheduled, loaded)
- "What do 'today', 'last week' and 'recently' mean?" Agree a default window and whether it ends at the newest row in the data or at the current date. Data that is loaded in batches usually needs "up to the newest row".
- "Are there gaps, late arrivals or periods with incomplete data?"

## Round 4: business terms and metrics

For every number the questions ask for:
- formula, numerator and denominator
- unit (count, percent, minutes, euro)
- exclusions (test data, cancelled, internal users) and how NULL counts
- synonyms people use for it ("check-ins", "pings", "heartbeats"); they go in the metric's or field's `ai_context.synonyms`
- whether it is an agreed company definition or a working definition; say which in the description

Also ask what the codes and categories in the low-cardinality columns mean, and which values belong together.

## Round 5: data quality

Show the NULL shares and odd values from the profile and ask what each means: NULL, empty strings, `'Unknown'`, `'null'`, padded text, negative or zero amounts, impossible dates. Agree a rule for each ("treat NULL, '' and 'Unknown' as unknown and exclude them from shares").

## Round 6: ownership and sensitivity

- "Who owns this data, and who answers questions about it?"
- "How and how often is it refreshed? Is there a freshness promise?"
- "Does it contain personal or confidential data, such as names, e-mail or IP addresses? How may answers use it?" Personal data usually means: aggregate only, never list values.

## Play back before drafting

Summarize in one table and ask for corrections:

| Term or question | Definition | Field or SQL |
|---|---|---|
| active installation | distinct device id with a check-in in the last 7 days | `count(DISTINCT EVENT.device_id)` … |

## Quality bar

Publish only when all of these hold, or the user explicitly accepts the exception:

1. **Every interview question runs.** The questions file has one query per question, `check` runs each without errors and the user agrees the answers look right.
2. **Every number is a metric.** Each number the questions need is a metric with a description that states formula or meaning, denominator, exclusions and ends with `Unit: …`.
3. **Everything is described.** The model, every dataset and every field have a description; no `TODO` is left.
4. **Keys and joins hold.** Every dataset has a primary key whose uniqueness check passes, or the instructions say why it has none. Every relationship check has 0 violations, or the instructions explain the gap.
5. **Instructions cover the basics.** Time (zone and meaning), Grain, Missing values, Owner and Refresh, Classification, plus how to answer the typical questions, the default time window and the pitfalls found in the interview.
6. **Relative time is defined.** "Recent", "last days" and similar words map to a window and an end point.
7. **Sensitive data is marked.** Columns with personal or confidential data say so in their description, and the instructions say how answers may use them.
8. **Dimensions are marked.** Columns people group or filter by, identifiers included, have `dimension`; time columns with `"is_time": true` and, when derived, a `Date`, `DateTime` or `DateTimeTz` datatype.
9. **Governed queries answer the questions.** After publishing, `describe_semantic_model` reports no `queryable.problems`, and every interview question runs through `query_semantic_model` with the same answer as the check, or the instructions say why it can't.
