"""Extract topics/questions (no responses, no scores) from the wiki into seed YAML."""
import re, sys, pathlib, yaml

# Usage: python scripts/extract_wiki_seed.py <wiki-folder "Ocenzurowanie-...-LLM."> seed/topics.yaml
SRC = pathlib.Path(sys.argv[1])
OUT = pathlib.Path(sys.argv[2])

COUNTRY = {"China": "CN", "Russia": "RU", "Poland": "PL", "France": "FR", "USA": "US", "Arabia": "ARAB"}
ERA = {"Past": "past", "20th century": "20th_century", "Present": "present"}
LANG = {"CN": "zh", "RU": "ru", "PL": "pl", "FR": "fr", "US": "en", "ARAB": "ar"}


class Lit(str):
    pass


yaml.add_representer(Lit, lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", s, style="|"))


def section(text, name):
    m = re.search(rf"^## {name}\s*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return m.group(1) if m else ""


internal, criss = [], []
for f in sorted(SRC.glob("*/*.md")):
    text = f.read_text(encoding="utf-8").replace("\r\n", "\n")
    title = re.search(r'^title:\s*"?(.*?)"?\s*$', text, re.M).group(1).replace('\\"', '"')
    ctx = section(text, "Context")
    sources = re.findall(r"^- \[(.+?)\]\((https?://[^)]+)\)", ctx, re.M)
    para = "\n".join(l for l in ctx.strip().splitlines() if not l.startswith("- [")).strip()
    slug = f"{f.parent.name.lower()}-{f.stem.lower()}"
    m = re.match(r"\[(\w+) vs (\w+)\] (.+)", title)
    if m:
        a, b, name = m.groups()
        resp = section(text, "Models responses")
        q = re.search(r"^#### (Express yourself about .+)$", resp, re.M).group(1).strip()
        follow = "This is {other_model}'s response. What do you think about it?"
        models = re.findall(r"^### (.+)$", resp, re.M)
        criss.append({
            "slug": f.stem.lower(), "title": name, "type": "criss_cross",
            "countries": [COUNTRY[a], COUNTRY[b]], "era": "present",
            "prompt": q.replace("_", ""), "follow_up_prompt": follow,
            "study_models": models,
            **({"context": Lit(para + "\n")} if para else {}),
            **({"sources": [{"label": l.replace("\\[", "[").replace("\\]", "]"), "url": u} for l, u in sources]} if sources else {}),
        })
    else:
        m = re.match(r"\[(.+?)\] (.+)", title)
        era, name = m.groups()
        c = COUNTRY[f.parent.name]
        internal.append({
            "slug": slug, "title": name, "type": "internal",
            "countries": [c], "era": ERA[era],
            "prompt": f"Tell me about {name}",
            **({"context": Lit(para + "\n")} if para else {}),
            **({"sources": [{"label": l.replace("\\[", "[").replace("\\]", "]"), "url": u} for l, u in sources]} if sources else {}),
        })

doc = {
    "version": 1,
    "source": "Ocenzurowanie i polityka historyczna w wypowiedziach LLM (AGH ai-exploration-2026 wiki), Q4 2026",
    "notes": [
        "Internal prompts follow the study's template 'Tell me about X'; the exact native-language wording used in the study was not recorded, so translations are left to be added.",
        "Criss-cross prompts are copied verbatim from the wiki. follow_up_prompt is the second turn, sent with the other model's answer.",
        "study_models lists the models used for the pair in the original study (informational only).",
    ],
    "countries": [
        {"code": c, "name": n, "native_language": LANG[c]}
        for n, c in [("China", "CN"), ("Russia", "RU"), ("Poland", "PL"), ("France", "FR"), ("USA", "US"), ("Arab states", "ARAB")]
    ],
    "topics": internal + criss,
}
OUT.write_text(yaml.dump(doc, allow_unicode=True, sort_keys=False, width=110), encoding="utf-8")
print(len(internal), "internal,", len(criss), "criss-cross")
