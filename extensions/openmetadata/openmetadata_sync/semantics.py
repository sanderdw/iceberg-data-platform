"""What the listed semantic models say about their data, for OpenMetadata.

A semantic model describes the business meaning of the tables it reads: the model itself, its
datasets (business entities, each reading one table), their fields (often one column each) and
metrics. This module turns that into:

- glossary terms: a term per model, with its datasets and described fields as child terms, in one
  glossary per installation, named `<prefix>__<database>__<namespace…>__<model>[__<dataset>[__<field>]]`
  with the readable name as display name;
- a classification of field labels (Ossie's "label for categorization"), a tag per label;
- notes, term tags and label tags for the tables and columns that datasets and fields read;
- a section per model for the data product's description.

Everything here is a pure function of a listing.
"""

import re

from . import mapping as m

COLUMN = re.compile(r'^(?:[`"]?[A-Za-z_][\w$]*[`"]?\.)?[`"]?([A-Za-z_][\w$]*)[`"]?$')


def glossary_request(domain):
    return {"name": m.clean(domain), "displayName": domain,
            "description": "Business terms from the semantic models of data products that teams of the Iceberg "
                           "Data Platform list in this catalog. Published by the platform's OpenMetadata extension."}


def classification_name(domain):
    return m.clean(f"{domain} labels")


def classification_request(domain):
    return {"name": classification_name(domain), "displayName": f"{domain} labels", "mutuallyExclusive": False,
            "description": "Labels that the semantic models of listed data products give their fields, such as the "
                           "business concept a field holds. Published by the platform's OpenMetadata extension."}


def label_fqn(domain, label):
    return f"{m.quote(classification_name(domain))}.{m.quote(m.clean(label))}"


def tag_request(domain, label):
    return {"classification": classification_name(domain), "name": m.clean(label), "displayName": label,
            "description": f"Fields that a listed semantic model labels {label}."}


def tag(fqn, source="Glossary"):
    return {"tagFQN": fqn, "source": source, "labelType": "Automated", "state": "Confirmed"}


def context(rows):
    """AI context rows as sentences, such as `Examples: A, B.`"""
    return [f"{r['label']}: {r['value']}" + ("" if r["value"].endswith(".") else ".") for r in rows]


def root_name(prefix, listing, obj, model, several):
    """The term of one model: unique per installation, database, namespace and model."""
    parts = [prefix, listing["database"]["id"], *obj["namespace"], obj["name"], *([model["name"]] if several else [])]
    return m.clean("__".join(parts))


def field_column(field, columns):
    """The column a field reads when its expression is just that column, such as `carrier_code` or `t.code`."""
    names = {c.lower(): c for c in columns}
    for candidate in (m.expression(field), field["name"]):
        match = COLUMN.match((candidate or "").strip())
        if match and match[1].lower() in names:
            return names[match[1].lower()]
    return None


def origin(listing, model):
    database = listing["database"]
    return f"From semantic model {model['name']} in database {database['name']} ({database['environment']})."


def enrich(prefix, listing, glossary):
    """(terms, tables, labels) for every semantic model of a listing.

    `terms` is [(fqn, create request)], parents before children. `tables` maps a listed table's key
    to {"notes": [text], "tags": [tag label], "columns": {column: {"notes": [text], "tags": [tag label]}}}.
    `labels` are the field labels, each a tag of the label classification. `glossary` is the
    configured domain: it names the glossary and the classification.
    """
    listed = {m.key(o["namespace"], o["name"]): [c["name"] for c in o["columns"]]
              for o in listing["objects"] if o["kind"] in ("table", "view")}
    root, terms, tables, labels = m.quote(m.clean(glossary)), [], {}, []
    for obj in (o for o in listing["objects"] if o["kind"] == "semantic-model"):
        several = len(obj["models"]) > 1
        for model in obj["models"]:
            name = root_name(prefix, listing, obj, model, several)
            model_fqn = f"{root}.{m.quote(name)}"
            lines = [model["description"] or f"Semantic model {model['name']}.", origin(listing, model)]
            terms.append((model_fqn, {"glossary": m.clean(glossary), "name": name, "displayName": model["name"],
                                      "description": "\n\n".join(lines)}))
            for dataset in model["datasets"]:
                # OpenMetadata wants term names unique in the whole glossary, and fields such as
                # `name` or `code` recur: child terms carry their parents' names.
                dataset_name = m.clean(f"{name}__{dataset['name']}")
                dataset_fqn = f"{model_fqn}.{m.quote(dataset_name)}"
                notes = context(dataset["aiContext"])
                terms.append((dataset_fqn, {
                    "glossary": m.clean(glossary), "parent": model_fqn, "name": dataset_name,
                    "displayName": dataset["name"],
                    "description": "\n\n".join([dataset["description"] or f"Dataset {dataset['name']} of semantic "
                                                                          f"model {model['name']}.", *notes])}))
                k = m.key(dataset["table"]["namespace"], dataset["table"]["name"]) if dataset.get("table") else None
                table = tables.setdefault(k, {"notes": [], "tags": [], "columns": {}}) if k in listed else None
                if table is not None:
                    table["tags"].append(tag(dataset_fqn))
                    if dataset["description"] or notes:
                        table["notes"] += [f"In semantic model {model['name']}, {dataset['name']}"
                                           + (f": {dataset['description']}" if dataset["description"] else "."),
                                           *notes]
                for field in dataset["fields"]:
                    label, notes = field["label"], context(field["aiContext"])
                    if label and label not in labels:
                        labels.append(label)
                    if not (field["description"] or field["synonyms"] or label or notes):
                        continue
                    field_name = m.clean(f"{dataset_name}__{field['name']}")
                    field_fqn = f"{dataset_fqn}.{m.quote(field_name)}"
                    if field["synonyms"]:
                        notes.insert(0, "Also called: " + ", ".join(field["synonyms"]) + ".")
                    terms.append((field_fqn, {
                        "glossary": m.clean(glossary), "parent": dataset_fqn, "name": field_name,
                        "displayName": field["name"], "synonyms": [m.clean(s) for s in field["synonyms"]],
                        "description": "\n\n".join([field["description"] or f"Field {field['name']} of "
                                                                              f"{dataset['name']}.", *notes]),
                        **({"tags": [tag(label_fqn(glossary, label), "Classification")]} if label else {})}))
                    column = field_column(field, listed[k]) if table is not None else None
                    if column:
                        entry = table["columns"].setdefault(column, {"notes": [], "tags": []})
                        entry["notes"].append(" ".join(filter(None, [field["description"], *notes])))
                        entry["tags"].append(tag(field_fqn))
                        if label:
                            entry["tags"].append(tag(label_fqn(glossary, label), "Classification"))
    unique = {}
    for fqn, body in terms:
        unique.setdefault(fqn, body)
    for table in tables.values():
        table["tags"] = list({t["tagFQN"]: t for t in table["tags"]}.values())
        for entry in table["columns"].values():
            entry["tags"] = list({t["tagFQN"]: t for t in entry["tags"]}.values())
    return list(unique.items()), tables, labels


def sections(listing):
    """A Markdown section per semantic model for the data product's description."""
    result = []
    for obj in (o for o in listing["objects"] if o["kind"] == "semantic-model"):
        for model in obj["models"]:
            lines = [f"### Semantic model {model['name']}"]
            if model["description"]:
                lines.append(model["description"])
            for row in model["aiContext"]:
                lines.append(f"**{row['label']}:** {row['value']}")
            datasets = [f"- **{d['name']}**"
                        + (f" ({'.'.join([*d['table']['namespace'], d['table']['name']])})" if d.get("table") else "")
                        + (": " + " ".join(filter(None, [d["description"], *context(d["aiContext"])]))
                           if d["description"] or d["aiContext"] else "")
                        for d in model["datasets"]]
            if datasets:
                lines.append("**Datasets**\n\n" + "\n".join(datasets))
            metrics = [f"- **{x['name']}**" + (f": {x['description']}" if x["description"] else "")
                       for x in model["metrics"]]
            if metrics:
                lines.append("**Metrics**\n\n" + "\n".join(metrics))
            result.append("\n\n".join(lines))
    return "\n\n".join(result)


def wanted_terms(prefix, listings, glossary):
    return {fqn for listing in listings for fqn, _ in enrich(prefix, listing, glossary)[0]}


def wanted_labels(prefix, listings, glossary):
    return {label_fqn(glossary, label) for listing in listings for label in enrich(prefix, listing, glossary)[2]}
