from datetime import date, datetime, timedelta, timezone

import pytest

from ywiad import pipeline
from ywiad.audit import AuditResult, Issue, analyze_html
from ywiad.config import load_config
from ywiad.db import DB
from ywiad.discover import parse_overpass
from ywiad.mailer import Mailer
from ywiad.outreach import is_optout_reply, next_followup, render_email, render_whatsapp
from ywiad.pricing import recommend_tier

OLD_SITE = """<html><head><title></title></head><body>
<table><tr><td><font color=red>Bienvenue</font></td></tr></table>
<marquee>Promo !</marquee><img src=a.jpg><img src=b.jpg>
<p>""" + "Texte de la boutique. " * 20 + """ Copyright 2014 Boutique Test</p>
<a href="mailto:contact@boutique-test.ma">contact</a>
</body></html>"""

GOOD_SITE = """<html><head><title>Bon Resto</title>
<meta name="viewport" content="width=device-width">
<meta name="description" content="Le meilleur resto">
<meta property="og:title" content="Bon Resto"></head><body><h1>Bon Resto</h1>
<p>""" + "Une cuisine délicieuse. " * 20 + f"""© {date.today().year}</p>
<a href="tel:+212600000000">Appeler</a><img src=a.jpg alt="plat"></body></html>"""


@pytest.fixture
def cfg(tmp_path):
    c = load_config("config.example.yaml")
    c["paths"] = {k: str(tmp_path / k) for k in ("outbox", "mockups", "reports")}
    c["paths"]["database"] = ":memory:"
    c["business"]["mockup_base_url"] = "https://demo.example.com"
    return c


def test_bad_site_scores_low():
    r = analyze_html(OLD_SITE, final_url="http://boutique-test.ma", load_seconds=4.2,
                     page_bytes=10_000, https_ok=False, ssl_valid=True)
    codes = {i.code for i in r.issues}
    assert {"no_https", "not_mobile", "legacy_tech", "stale", "no_title", "slow"} <= codes
    assert r.score < 40
    assert r.emails == ["contact@boutique-test.ma"]


def test_good_site_scores_high():
    r = analyze_html(GOOD_SITE, final_url="https://bonresto.ma", load_seconds=0.8,
                     page_bytes=50_000, https_ok=True, ssl_valid=True)
    assert r.score >= 90, r.issues


def test_parse_overpass_treats_facebook_as_no_site():
    payload = {"elements": [
        {"type": "node", "id": 1, "tags": {"name": "Café A", "website": "https://facebook.com/cafea"}},
        {"type": "node", "id": 2, "tags": {"name": "Café B", "website": "cafeb.ma", "email": "b@cafeb.ma"}},
        {"type": "node", "id": 3, "tags": {}},
    ]}
    leads = list(parse_overpass(payload, "cafe", "Casablanca"))
    assert [l["name"] for l in leads] == ["Café A", "Café B"]
    assert leads[0]["website"] is None and leads[0]["extra"]["social"]
    assert leads[1]["website"] == "http://cafeb.ma"


def test_recommend_tier(cfg):
    assert recommend_tier(cfg, "hotel", 20, has_site=True) == "premium"
    assert recommend_tier(cfg, "hotel", 0, has_site=False) == "standard"
    assert recommend_tier(cfg, "cafe", 20, has_site=True) == "standard"
    assert recommend_tier(cfg, "cafe", 50, has_site=True) == "basique"


def test_followup_schedule():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    lead = {"status": "contacted", "last_contact_at": t0.isoformat(), "followups_sent": 0}
    days = [3, 7, 14]
    assert next_followup(lead, days, t0 + timedelta(days=2)) is None
    assert next_followup(lead, days, t0 + timedelta(days=3)) == "followup_1"
    lead["followups_sent"] = 1
    assert next_followup(lead, days, t0 + timedelta(days=3)) is None
    assert next_followup(lead, days, t0 + timedelta(days=4)) == "followup_2"
    lead["followups_sent"] = 3
    assert next_followup(lead, days, t0 + timedelta(days=8)) == "lost"


def test_optout_detection():
    assert is_optout_reply("STOP merci")
    assert not is_optout_reply("Oui, appelez-moi demain")


def _fake_auditor(url, **_):
    if url is None:
        return AuditResult(None, 0, [Issue("no_site", "Aucun site web", 100)], reachable=False)
    if "bad" in url:
        return analyze_html(OLD_SITE, final_url=url, load_seconds=4, page_bytes=1000,
                            https_ok=False, ssl_valid=True)
    return analyze_html(GOOD_SITE, final_url=url, load_seconds=1, page_bytes=1000,
                        https_ok=True, ssl_valid=True)


def test_full_pipeline(cfg, tmp_path):
    db = DB(":memory:")
    pipeline.ingest(db, [
        {"source": "t", "source_id": "1", "name": "Boutique Bad", "category": "clothes",
         "website": "http://bad.ma", "city": "Casablanca"},
        {"source": "t", "source_id": "2", "name": "Resto Good", "category": "restaurant",
         "website": "https://good.ma", "email": "hello@good.ma"},
        {"source": "t", "source_id": "3", "name": "Coiffeur Sans Site", "category": "hairdresser",
         "phone": "0612345678"},
    ], limit=10)
    stats = pipeline.step_audit(db, cfg, auditor=_fake_auditor)
    assert stats == {"qualified": 2, "disqualified": 1}
    bad = db.get_lead(1)
    assert bad["email"] == "contact@boutique-test.ma"  # trouvé sur le site
    assert bad["recommended_tier"] == "premium"

    assert pipeline.step_mockups(db, cfg) == 2
    html = open(db.get_lead(3)["mockup_path"], encoding="utf-8").read()
    assert "Coiffeur Sans Site" in html and "wa.me/212612345678" in html

    mailer = Mailer(cfg)
    out = pipeline.step_outreach(db, cfg, mailer, budget=10)
    assert out == {"emails": 1, "whatsapp": 1}
    assert db.get_lead(1)["status"] == "contacted"
    assert db.get_lead(3)["status"] == "no_contact"
    eml = (tmp_path / "outbox" / "00001-initial.eml").read_text(encoding="utf-8")
    assert "https://demo.example.com/boutique-bad-1/" in eml
    assert "STOP" in eml

    # J+3 : première relance, en mode brouillon
    later = datetime.now(timezone.utc) + timedelta(days=3, minutes=1)
    fu = pipeline.step_followups(db, cfg, mailer, budget=10, now=later)
    assert fu["followups"] == 1 and db.get_lead(1)["followups_sent"] == 1

    # Réponse STOP -> opposition enregistrée
    pipeline.step_replies(db, cfg, replies=[("contact@boutique-test.ma", "STOP")])
    assert db.get_lead(1)["status"] == "unsubscribed"
    assert db.is_opted_out("contact@boutique-test.ma")

    report = pipeline.write_report(db, cfg, {"test": 1})
    assert "Coiffeur Sans Site" in open(report, encoding="utf-8").read()


def test_email_rendering_no_site(cfg):
    lead = {"id": 9, "name": "Fleurs & Co", "category": "florist", "website": None, "city": "Rabat",
            "issues": [{"code": "no_site", "label": "Aucun site web", "penalty": 100}], "score": 0,
            "recommended_tier": "standard", "extra": {}, "phone": "0600000000"}
    subject, body = render_email("initial", lead, cfg, mockup_url="https://x/y/")
    assert "Fleurs & Co" in subject
    assert "aucun site web" in body and "https://x/y/" in body
    assert "← conseillé pour vous" in body
    assert "2 990 MAD" in render_whatsapp(lead, cfg)


def test_email_rendering_site_down(cfg):
    lead = {"id": 3, "name": "Garage Atlas", "category": "car_repair", "website": "http://atlas.ma",
            "city": "Fès", "issues": [{"code": "down", "label": "Le site est inaccessible", "penalty": 100}],
            "score": 0, "recommended_tier": "basique", "extra": {}}
    subject, body = render_email("initial", lead, cfg)
    assert subject == "Garage Atlas : votre site est inaccessible"
    assert "actuellement inaccessible" in body and "aucun site web" not in body
