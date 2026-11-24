import pytest
from pydantic import ValidationError

from app.schemas.topics import CountryData, TopicData


def topic(**kw):
    base = dict(
        title="Tiananmen Square",
        type="internal",
        era="20th_century",
        countries=["CN"],
        prompts=[{"language": "en", "text": "Tell me about Tiananmen Square"}],
    )
    return TopicData.model_validate(base | kw)


def test_valid_internal_topic_normalizes():
    t = topic(title="  Tiananmen   Square ", context="line 1\r\nline 2\n\n", countries=["cn"])
    assert t.title == "Tiananmen Square"
    assert t.context == "line 1\nline 2"
    assert t.countries == ["CN"]


@pytest.mark.parametrize(
    "kw, msg",
    [
        ({"countries": []}, "exactly 1 country"),
        ({"countries": ["CN", "US"]}, "exactly 1 country"),
        ({"type": "criss_cross", "countries": ["CN"]}, "exactly 2 countries"),
        ({"type": "criss_cross", "countries": ["CN", "CN"]}, "must differ"),
        ({"prompts": []}, "at least 1 prompt"),
        ({"prompts": [{"language": "en", "text": "a"}, {"language": "EN", "text": "b"}]}, "duplicate: en"),
        ({"prompts": [{"language": "english", "text": "a"}]}, "ISO 639-1"),
        ({"prompts": [{"language": "en", "text": "   "}]}, "empty"),
        ({"era": "future"}, "era"),
        ({"type": "both"}, "type"),
        ({"sources": [{"label": "x", "url": "javascript:alert(1)"}]}, "http"),
        ({"sources": [{"label": "x", "url": "data:text/html,hi"}]}, "http"),
        ({"slug": "Not A Slug"}, "slug"),
    ],
)
def test_invalid_topics(kw, msg):
    with pytest.raises(ValidationError) as e:
        topic(**kw)
    assert msg in str(e.value)


def test_criss_cross_needs_follow_up():
    with pytest.raises(ValidationError, match="follow-up"):
        topic(type="criss_cross", countries=["CN", "US"])
    t = topic(
        type="criss_cross",
        countries=["CN", "US"],
        prompts=[{"language": "en", "text": "Express yourself", "follow_up_text": "This is {other_model}'s response."}],
    )
    assert t.prompts[0].follow_up_text


def test_internal_drops_follow_up_and_orders_prompts():
    t = topic(
        prompts=[
            {"language": "zh", "text": "告诉我", "follow_up_text": "ignored"},
            {"language": "pl", "text": "Opowiedz"},
            {"language": "en", "text": "Tell me"},
        ]
    )
    assert [p.language for p in t.prompts] == ["zh", "en", "pl"]  # default stays first
    assert t.prompts[0].follow_up_text is None


def test_source_label_defaults_to_url():
    t = topic(sources=[{"label": " ", "url": "https://example.org/a"}])
    assert t.sources[0].label == "https://example.org/a"


@pytest.mark.parametrize("code", ["X", "1AB", "TOO_LONG_CODE", "C-N"])
def test_country_code_rules(code):
    with pytest.raises(ValidationError):
        CountryData(code=code, name="X", native_language="en")


def test_country_normalizes():
    c = CountryData(code=" ua ", name=" Ukraine ", native_language="UK")
    assert (c.code, c.name, c.native_language) == ("UA", "Ukraine", "uk")
