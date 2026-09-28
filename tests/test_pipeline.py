from datetime import date, datetime, timedelta, timezone

import pytest

from ywiad import pipeline
from ywiad.audit import AuditResult, Issue, analyze_html, extract_emails
from ywiad.config import load_config
from ywiad.db import DB
from ywiad.discover import all_combos, next_combos, parse_overpass
from ywiad.mailer import Mailer
from ywiad.mockup import whatsapp_number
from ywiad.outreach import is_optout_reply, next_followup, render_email, render_whatsapp
from ywiad.pricing import can_email_market, format_price, in_send_window, market, recommend_tier

OLD_SITE = """<html><head><title></title></head><body>
<table><tr><td><font color=red>Bienvenue</font></td></tr></table>
<marquee>Promo !</marquee><img src=a.jpg><img src=b.jpg>
<p>""" + "Texte de la boutique. " * 20 + """ Copyright 2014 Boutique Test</p>
<a href="mailto:contact@boutique-test.fr">contact</a>
</body></html>"""

GOOD_SITE = """<html><head><title>Bon Resto</title>
<meta name="viewport" content="width=device-width">
<meta name="description" content="Le meilleur resto">
<meta property="og:title" content="Bon Resto"></head><body><h1>Bon Resto</h1>
<p>""" + "Une cuisine délicieuse. " * 20 + f"""© {date.today().year}</p>
<a href="tel:+212600000000">Appeler</a><img src=a.jpg alt="plat"></body></html>"""

# Mardi 10 h à Paris, 4 h du matin à New York
PARIS_MORNING = datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_dns(monkeypatch):
    """Pas de requête DNS réelle pendant les tests."""
    monkeypatch.setattr("ywiad.audit.has_mx", lambda domain: True)
    monkeypatch.setattr("ywiad.audit.domain_exists", lambda domain: True)


@pytest.fixture
def cfg(tmp_path):
    c = load_config("config.example.yaml")
    c["paths"] = {k: str(tmp_path / k) for k in ("outbox", "mockups", "reports", "site")}
    c["paths"]["database"] = ":memory:"
    return c


def test_bad_site_scores_low():
    r = analyze_html(OLD_SITE, final_url="http://boutique-test.fr", load_seconds=6.2,
                     page_bytes=10_000, https_ok=False, ssl_valid=True)
    codes = {i.code for i in r.issues}
    assert {"no_https", "not_mobile", "legacy_tech", "stale", "no_title", "slow"} <= codes
    assert r.score < 40
    assert r.emails == ["contact@boutique-test.fr"]


def test_good_site_scores_high():
    r = analyze_html(GOOD_SITE, final_url="https://bonresto.ma", load_seconds=0.8,
                     page_bytes=50_000, https_ok=True, ssl_valid=True)
    assert r.score >= 90, r.issues


def test_extract_emails_filters_placeholders():
    html = 'Écrivez à info[at]salon-lumiere.fr ou votre-email@example.com — <img src="logo@2x.png">'
    assert extract_emails(html) == ["info@salon-lumiere.fr"]


def test_parse_overpass_skips_chains_and_social():
    payload = {"elements": [
        {"type": "node", "id": 1, "tags": {"name": "Café A", "website": "https://facebook.com/cafea"}},
        {"type": "node", "id": 2, "tags": {"name": "Café B", "website": "cafeb.fr", "email": "B@cafeb.fr"}},
        {"type": "node", "id": 3, "tags": {"name": "Starbucks", "brand": "Starbucks", "website": "starbucks.fr"}},
        {"type": "node", "id": 4, "tags": {}},
    ]}
    leads = list(parse_overpass(payload, "cafe", "Lyon", "FR"))
    assert [l["name"] for l in leads] == ["Café A", "Café B"]
    assert leads[0]["website"] is None and leads[0]["extra"]["social"]
    assert leads[1]["website"] == "http://cafeb.fr" and leads[1]["email"] == "b@cafeb.fr"
    assert leads[1]["market"] == "FR"


def test_rotation_covers_every_combo(cfg):
    combos = all_combos(cfg)
    assert combos and all(cfg["markets"][c[0]]["enabled"] for c in combos)
    assert not any(c[0] == "GB" for c in combos)  # marché désactivé
    seen, cursor = set(), 0
    for _ in range(len(combos) // 6 + 1):
        picked, cursor = next_combos(cfg, cursor, 6)
        seen.update(picked)
    assert seen == set(combos)


def test_localized_prices(cfg):
    assert format_price(1290, market(cfg, "FR")) == "1 290 €"
    assert format_price(1490, market(cfg, "US")) == "$1,490"
    assert format_price(2990, market(cfg, "MA")) == "2 990 MAD"


def test_recommend_tier(cfg):
    assert recommend_tier(cfg, "hotel", 20, has_site=True) == "premium"
    assert recommend_tier(cfg, "hotel", 0, has_site=False) == "standard"
    assert recommend_tier(cfg, "cafe", 20, has_site=True) == "standard"
    assert recommend_tier(cfg, "cafe", 50, has_site=True) == "basique"


def test_send_window_and_postal_address(cfg):
    assert in_send_window(cfg, "FR", PARIS_MORNING)
    assert not in_send_window(cfg, "US", PARIS_MORNING)
    assert not in_send_window(cfg, "FR", datetime(2026, 9, 27, 9, 0, tzinfo=timezone.utc))  # dimanche
    assert in_send_window(cfg, "FR", datetime(2026, 9, 26, 9, 0, tzinfo=timezone.utc))  # samedi : commerces ouverts
    assert can_email_market(cfg, "US") == (False, "marché désactivé")
    cfg["markets"]["US"]["enabled"] = True
    assert can_email_market(cfg, "US") == (False, "adresse postale de l'expéditeur requise (business.postal_address)")
    cfg["business"]["postal_address"] = "1 rue X, Casablanca"
    assert can_email_market(cfg, "US")[0]
    assert not can_email_market(cfg, "GB")[0]


def test_whatsapp_number():
    assert whatsapp_number("0612345678", "MA") == "212612345678"
    assert whatsapp_number("+33 6 12 34 56 78", "FR") == "33612345678"
    assert whatsapp_number("06 12 34 56 78", "FR") == "33612345678"


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
    assert is_optout_reply("Not interested, thanks")
    assert not is_optout_reply("Oui, appelez-moi demain")


def _fake_auditor(url, **_):
    if url is None:
        return AuditResult(None, 0, [Issue("no_site", 100)], reachable=False)
    if "bad-diner" in url:
        return analyze_html(OLD_SITE.replace("contact@boutique-test.fr", "hi@bad-diner.com"), final_url=url,
                            load_seconds=4, page_bytes=1000, https_ok=False, ssl_valid=True)
    if "bad" in url:
        return analyze_html(OLD_SITE, final_url=url, load_seconds=4, page_bytes=1000,
                            https_ok=False, ssl_valid=True)
    if "nomail" in url:
        r = analyze_html(OLD_SITE.replace("contact@boutique-test.fr", ""), final_url=url, load_seconds=4,
                         page_bytes=1000, https_ok=False, ssl_valid=True)
        r.emails = []
        return r
    return analyze_html(GOOD_SITE, final_url=url, load_seconds=1, page_bytes=1000,
                        https_ok=True, ssl_valid=True)


def test_full_pipeline_queue_mode(cfg):
    db = DB(":memory:")
    created = pipeline.ingest(db, [
        {"source": "t", "source_id": "1", "name": "Boutique Bad", "category": "clothes", "market": "FR",
         "website": "http://bad.fr", "city": "Lyon"},
        {"source": "t", "source_id": "2", "name": "Resto Good", "category": "restaurant", "market": "FR",
         "website": "https://good.fr", "email": "hello@good.fr"},
        {"source": "t", "source_id": "3", "name": "Coiffeur Sans Site", "category": "hairdresser", "market": "MA",
         "phone": "0612345678", "city": "Rabat"},
        {"source": "t", "source_id": "4", "name": "Diner Bad", "category": "restaurant", "market": "US",
         "website": "http://bad-diner.com", "city": "Miami"},
        {"source": "t", "source_id": "5", "name": "Injoignable", "category": "cafe", "market": "FR"},
        {"source": "t", "source_id": "6", "name": "Garage Sans Email", "category": "car_repair", "market": "FR",
         "website": "http://nomail.fr"},
    ], limit=10)
    assert created == 5  # le lead sans site ni email hors Maroc est ignoré

    stats = pipeline.step_audit(db, cfg, auditor=_fake_auditor)
    assert stats == {"qualified": 3, "disqualified": 1, "no_contact": 1}
    bad = db.get_lead(1)
    assert bad["email"] == "contact@boutique-test.fr" and bad["recommended_tier"] == "premium"
    assert db.leads("qualified")[0]["priority"] >= db.leads("qualified")[-1]["priority"]

    assert pipeline.step_mockups(db, cfg) == 3
    html = open(db.get_lead(3)["mockup_path"], encoding="utf-8").read()
    assert "Coiffeur Sans Site" in html and "wa.me/212612345678" in html
    us_html = open(db.get_lead(4)["mockup_path"], encoding="utf-8").read()
    assert 'lang="en"' in us_html and "Book a table" in us_html

    mailer = Mailer(cfg)
    out = pipeline.step_outreach(db, cfg, mailer, budget=10, now=PARIS_MORNING)
    assert out == {"emails": 1, "whatsapp": 1}
    assert db.get_lead(4)["status"] == "blocked"  # US sans adresse postale
    queued = db.messages("queued")
    assert len(queued) == 1 and queued[0]["to_addr"] == "contact@boutique-test.fr"
    assert "https://yourwebsiteinaday.netlify.app/demo/boutique-bad-1/" in queued[0]["body"]
    assert "1 290 €" in queued[0]["body"] and "L34-5" in queued[0]["body"]
    assert db.get_lead(1)["status"] == "qualified"  # pas encore envoyé

    # Pas de doublon tant que le message est en file
    assert pipeline.step_outreach(db, cfg, mailer, budget=10, now=PARIS_MORNING)["emails"] == 0

    pipeline.confirm(db, queued[0]["id"])
    assert db.get_lead(1)["status"] == "contacted"

    later = PARIS_MORNING + timedelta(days=3)
    db.update_lead(1, last_contact_at=PARIS_MORNING.isoformat())
    fu = pipeline.step_followups(db, cfg, mailer, budget=10, now=later)
    assert fu["followups"] == 1
    msg = db.messages("queued")[0]
    assert msg["subject"].startswith("Re: Boutique Bad")
    pipeline.confirm(db, msg["id"])
    assert db.get_lead(1)["followups_sent"] == 1

    assert pipeline.inbound(db, "CONTACT@boutique-test.fr", "STOP") == "unsubscribed"
    assert db.is_opted_out("contact@boutique-test.fr")

    report = open(pipeline.write_report(db, cfg, {"test": 1}), encoding="utf-8").read()
    assert "**FR**" in report and "Bloqués (1)" in report


def test_bounce_marks_lead_lost(cfg):
    db = DB(":memory:")
    lead_id, _ = db.upsert_lead({"source": "t", "source_id": "1", "name": "X", "market": "FR", "email": "x@x.fr"})
    msg_id = db.log_message(lead_id, "initial", "email", "s", "b", "queued", "x@x.fr")
    pipeline.fail(db, msg_id, "550 user unknown", bounce=True)
    assert db.get_lead(lead_id)["status"] == "lost"
    assert db.message(msg_id)["status"] == "failed"


def test_email_rendering_english_no_site(cfg):
    lead = {"id": 9, "name": "Bloom & Co", "category": "florist", "market": "AU", "website": None,
            "city": "Sydney", "issues": [{"code": "no_site", "penalty": 100, "params": {}}], "score": 0,
            "recommended_tier": "standard", "extra": {}, "phone": "0400000000"}
    subject, body = render_email("initial", lead, cfg, mockup_url="https://x/y/")
    assert subject == "Bloom & Co: your customers are looking for you online"
    assert "no website" in body and "https://x/y/" in body and "recommended for you" in body
    assert "No website: customers" not in body  # pas de puce redondante
    assert "1,990 AUD" in body and "Reply \"STOP\"" in body
    assert "790 AUD" in render_whatsapp(lead, cfg)


def test_email_rendering_site_down_fr(cfg):
    lead = {"id": 3, "name": "Garage Atlas", "category": "car_repair", "market": "MA", "website": "http://atlas.ma",
            "city": "Fès", "issues": [{"code": "down", "penalty": 100, "params": {"status": 503}}],
            "score": 0, "recommended_tier": "basique", "extra": {}}
    subject, body = render_email("initial", lead, cfg)
    assert subject == "Garage Atlas : votre site est inaccessible"
    assert "ne s'affiche plus" in body and "aucun site web" not in body
    assert "erreur 503" in body
    assert "2 990 MAD" in body and "09-08" in body


def test_placeholder_and_garbage_emails_rejected():
    from ywiad.audit import plausible_email
    for bad in ("abc@xyz.com", "votre@email.fr", "7p46ot4avp90@1280x853.jpeg", "adresse@mail.com", "logo@2x.png"):
        assert not plausible_email(bad), bad
    assert plausible_email("contact@salon-lumiere.fr")


def test_network_pages_are_excluded():
    from ywiad.discover import is_network_page
    assert is_network_page("https://www.orpi.com/bischheimcentre")
    assert not is_network_page("https://salon.fr/fr/")
    assert not pipeline.keep_lead({"website": None, "email": "a@b.fr", "extra": {"network": "https://orpi.com/x"}})


def test_js_rendered_site_is_not_judged():
    html = "<html><head><title>App</title><script src='app.js'></script></head><body><div id=root></div></body></html>"
    r = analyze_html(html, final_url="https://spa.fr", load_seconds=1, page_bytes=1000, https_ok=True, ssl_valid=True)
    assert not r.verified


def test_free_subdomain_flagged():
    r = analyze_html(GOOD_SITE, final_url="https://didier.wixsite.com/bonsai", load_seconds=1, page_bytes=1000,
                     https_ok=True, ssl_valid=True)
    assert "no_domain" in {i.code for i in r.issues}


def test_followups_reuse_gmail_thread_and_confirm_is_idempotent():
    db = DB(":memory:")
    lead_id, _ = db.upsert_lead({"source": "t", "source_id": "1", "name": "X", "market": "FR", "email": "x@x.fr"})
    m1 = db.log_message(lead_id, "initial", "email", "s", "b", "queued", "x@x.fr")
    pipeline.confirm(db, m1, "thread-abc")
    assert db.lead_thread(lead_id) == "thread-abc"
    m2 = db.log_message(lead_id, "followup_1", "email", "Re: s", "b", "queued", "x@x.fr")
    pipeline.confirm(db, m2)
    pipeline.confirm(db, m2)
    assert db.get_lead(lead_id)["followups_sent"] == 1


def test_html_email_initial(cfg):
    from ywiad.outreach import render_email_html
    lead = {"id": 32, "name": "Shine Spa", "category": "beauty", "market": "FR", "website": "https://www.shinespa.fr",
            "city": "Lyon", "score": 49, "recommended_tier": "standard", "extra": {},
            "issues": [{"code": "bad_ssl", "penalty": 20, "params": {}}, {"code": "not_mobile", "penalty": 20, "params": {}},
                       {"code": "no_cta", "penalty": 5, "params": {}}]}
    html = render_email_html("initial", lead, cfg, mockup_url="https://x/demo/shine-spa-32/",
                             preview_url="https://x/demo/shine-spa-32/preview.jpg")
    assert html.startswith("<!doctype html>")
    assert ">49</span>" in html and "À refaire" in html and "shinespa.fr" in html
    assert "<img" not in html  # maquette dessinée en HTML : rien à bloquer
    from ywiad.mockup import mockup_spec
    assert mockup_spec(lead, cfg)["tagline"] in html  # même accroche que la maquette web
    assert "INSTITUT" not in html  # majuscules via CSS
    assert "background:" not in html and 'bgcolor="#1a1614"' in html
    assert "Conseillé pour vous" in html and "1 290 €" in html and "L34-5" in html
    assert len(html.encode()) < 60_000  # Gmail tronque au-delà de ~102 Ko


def test_html_email_followup_keeps_text_and_links(cfg):
    from ywiad.outreach import render_email_html
    lead = {"id": 1, "name": "Café & Co", "category": "cafe", "market": "FR", "website": "http://cafe.fr",
            "city": "Nice", "score": 30, "recommended_tier": "basique", "extra": {},
            "issues": [{"code": "not_mobile", "penalty": 20, "params": {}}]}
    html = render_email_html("followup_1", lead, cfg, mockup_url="https://x/demo/cafe-1/")
    assert "Café &amp; Co" in html and '<a href="https://x/demo/cafe-1/"' in html
    assert "Votre maquette" in html and "<script" not in html


def test_french_elision_and_custom_domain_site():
    from ywiad.outreach import de_name
    assert de_name("Institut Audrey Ebeyer") == "d'Institut Audrey Ebeyer"
    assert de_name("Hotel Espagne21") == "d'Hotel Espagne21"
    assert de_name("Shine Spa") == "de Shine Spa"
    assert pipeline.website_from_email({"website": None, "email": "contact@laurabinstitut.fr"}) == "https://laurabinstitut.fr"
    assert pipeline.website_from_email({"website": None, "email": "salon@gmail.com"}) is None


def test_gone_and_challenge_pages():
    parked = "<html><head><title>shinespa.fr</title></head><body><h1>Too bad!</h1><p>This domain was successfully registered for the highest bidder in our weekly auction.</p></body></html>"
    r = analyze_html(parked, final_url="https://www.shinespa.fr", load_seconds=1, page_bytes=900, https_ok=True, ssl_valid=True)
    assert [i.code for i in r.issues] == ["parked"] and r.score == 0 and not r.reachable
    deleted = "<html><title>La Maison</title><body><h1>Le site de La Maison a été supprimé.</h1></body></html>"
    assert analyze_html(deleted, final_url="https://maison.fr", load_seconds=1, page_bytes=500, https_ok=True, ssl_valid=True).issues[0].code == "parked"
    captcha = '<html><head><meta http-equiv="refresh" content="0;/.well-known/sgcaptcha/?r=%2F"></head></html>'
    assert not analyze_html(captcha, final_url="https://riad.com", load_seconds=1, page_bytes=169, https_ok=True, ssl_valid=True).verified


def test_site_content_extraction():
    from ywiad.content import extract_site_content
    html = """<html><head><title>Riad Nour — Marrakech</title><meta name="description" content="Un riad paisible au cœur de la médina, avec patio et hammam traditionnel.">
    <meta name="theme-color" content="#7a3b2e"></head><body><header><img src="/img/logo.png" alt="Riad Nour logo"></header>
    <h1>Riad Nour, la douceur de la médina</h1><div style="background-image:url('/uploads/patio-1600x900.jpg')"></div>
    <img data-src="/uploads/chambre.jpg" src="data:image/gif;base64,xx" alt="Chambre"><h2>Nos chambres</h2><p>Six chambres décorées à la main, toutes avec salle de bain privée et climatisation.</p>
    <h2>Le hammam</h2><p>Un hammam traditionnel et des soins aux huiles d'argan pour se ressourcer après la visite.</p></body></html>"""
    c = extract_site_content(html, "https://riadnour.ma/")
    assert c["headline"] == "Riad Nour, la douceur de la médina"
    assert c["logo"] == "https://riadnour.ma/img/logo.png" and c["theme_color"] == "#7a3b2e"
    assert "https://riadnour.ma/uploads/patio-1600x900.jpg" in c["photos"] and "https://riadnour.ma/uploads/chambre.jpg" in c["photos"]
    assert [s["title"] for s in c["sections"]] == ["Nos chambres", "Le hammam"] and not c["parked"]


def test_mockups_vary_between_businesses(cfg):
    from ywiad.mockup import mockup_spec
    base = {"category": "hotel", "market": "MA", "city": "Marrakech", "extra": {}}
    specs = [mockup_spec({**base, "name": n}, cfg) for n in ("Riad A", "Riad Bahia", "Dar Chams", "Hotel Atlas", "Riad Zitoun")]
    assert len({(s["layout"], s["accent"], s["tagline"]) for s in specs}) == len(specs)
    assert any("Marrakech" in s["tagline"] for s in specs)


def test_www_missing_but_domain_alive_is_not_expired(monkeypatch):
    import requests
    from ywiad import audit
    def boom(*a, **k):
        raise requests.ConnectionError("dns")
    monkeypatch.setattr(audit, "_fetch", boom)
    monkeypatch.setattr(audit, "domain_exists", lambda d: d == "resto.be")
    r = audit.audit_url("https://www.resto.be")
    assert r.issues[0].code == "down"  # le domaine vit (messagerie), seul le site manque
    monkeypatch.setattr(audit, "domain_exists", lambda d: False)
    assert audit.audit_url("https://www.resto.be").issues[0].code == "expired"
