"""Turn the topic HTML form into TopicData, and TopicData back into form values."""

from dataclasses import dataclass, field

from pydantic import ValidationError
from starlette.datastructures import FormData

from app.models import TopicType
from app.schemas.topics import (
    DEFAULT_FOLLOW_UP,
    TopicData,
    default_prompt_text,
)


@dataclass
class SourceRow:
    label: str = ""
    url: str = ""


@dataclass
class PromptRow:
    language: str = "en"
    text: str = ""
    follow_up_text: str = ""


@dataclass
class TopicForm:
    """Raw form values, kept as typed so the form can be re-shown with errors."""

    title: str = ""
    type: str = TopicType.INTERNAL.value
    era: str = ""
    country_1: str = ""
    country_2: str = ""
    context: str = ""
    sources: list[SourceRow] = field(default_factory=list)
    prompts: list[PromptRow] = field(default_factory=lambda: [PromptRow()])
    slug: str = ""

    @classmethod
    def from_formdata(cls, form: FormData) -> "TopicForm":
        def s(name: str) -> str:
            v = form.get(name)
            return v if isinstance(v, str) else ""

        def lst(name: str) -> list[str]:
            return [v if isinstance(v, str) else "" for v in form.getlist(name)]

        sources = [
            SourceRow(label, url)
            for label, url in zip(lst("source_label"), lst("source_url"), strict=False)
        ]
        prompts = [
            PromptRow(lang, text, follow)
            for lang, text, follow in zip(
                lst("prompt_language"), lst("prompt_text"), lst("prompt_follow_up"), strict=False
            )
        ]
        return cls(
            title=s("title"),
            type=s("type") or TopicType.INTERNAL.value,
            era=s("era"),
            country_1=s("country_1"),
            country_2=s("country_2"),
            context=s("context"),
            sources=sources,
            prompts=prompts or [PromptRow()],
        )

    @classmethod
    def from_data(cls, data: TopicData) -> "TopicForm":
        return cls(
            title=data.title,
            type=data.type.value,
            era=data.era.value,
            country_1=data.countries[0] if data.countries else "",
            country_2=data.countries[1] if len(data.countries) > 1 else "",
            context=data.context or "",
            sources=[SourceRow(s.label, s.url) for s in data.sources],
            prompts=[PromptRow(p.language, p.text, p.follow_up_text or "") for p in data.prompts],
            slug=data.slug or "",
        )

    def to_data(self) -> tuple[TopicData | None, list[str]]:
        """Validate. Blank source/prompt rows are ignored. An empty first prompt is filled
        with the default template, and an empty English criss-cross follow-up with the
        study's default."""
        title = " ".join(self.title.split())
        criss = self.type == TopicType.CRISS_CROSS.value
        countries = [self.country_1, self.country_2] if criss else [self.country_1]
        prompts = []
        for i, p in enumerate(self.prompts):
            text, follow, lang = p.text.strip(), p.follow_up_text.strip(), p.language.strip()
            if i > 0 and not text and not follow:
                continue
            if i == 0 and not text and title:
                try:
                    text = default_prompt_text(TopicType(self.type), title)
                except ValueError:
                    pass
            if criss and not follow and (lang or "en").lower() == "en":
                follow = DEFAULT_FOLLOW_UP
            prompts.append({"language": lang or "en", "text": text, "follow_up_text": follow or None})
        raw = {
            "title": title,
            "type": self.type,
            "era": self.era,
            "countries": [c for c in countries if c],
            "context": self.context,
            "sources": [
                {"label": s.label, "url": s.url} for s in self.sources if s.label.strip() or s.url.strip()
            ],
            "prompts": prompts,
        }
        try:
            return TopicData.model_validate(raw), []
        except ValidationError as e:
            return None, friendly_errors(e)


FIELD_NAMES = {
    "title": "Title",
    "type": "Type",
    "era": "Era",
    "countries": "Countries",
    "context": "Context",
    "sources": "Source",
    "prompts": "Prompt",
    "url": "URL",
    "label": "label",
    "language": "language",
    "text": "text",
    "code": "Code",
    "name": "Name",
    "native_language": "Native language",
    "topic_ids": "Topics",
    "model_ids": "Models",
    "languages": "Languages",
    "repetitions": "Repetitions",
    "temperature": "Temperature",
    "concurrency": "Concurrency",
}


def friendly_errors(exc: ValidationError) -> list[str]:
    out = []
    for err in exc.errors():
        msg = err["msg"].removeprefix("Value error, ")
        parts = []
        for x in err["loc"]:
            parts.append(f"#{x + 1}" if isinstance(x, int) else FIELD_NAMES.get(str(x), str(x)))
        where = " ".join(parts)
        out.append(f"{where}: {msg}" if where else msg[:1].upper() + msg[1:])
    return out
