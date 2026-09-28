"""Interface en ligne de commande : `ywiad <commande>`."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import pipeline
from .audit import audit_url
from .config import load_config
from .db import DB, STATUSES
from .mailer import Mailer
from .pricing import enabled_markets, market, pricing_table


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ywiad", description="YourWebsiteInADay — prospection de refontes de sites")
    parser.add_argument("-c", "--config", help="fichier de configuration (défaut : config.yaml)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="routine complète : découverte, audit, maquettes, file d'envoi, relances, rapport")
    r.add_argument("--skip-discover", action="store_true", help="ne pas chercher de nouveaux leads")
    sub.add_parser("discover", help="chercher de nouveaux commerçants (cibles suivantes de la rotation)")
    i = sub.add_parser("import", help="importer des leads depuis un CSV")
    i.add_argument("csv")
    sub.add_parser("audit", help="auditer les leads nouveaux")
    a = sub.add_parser("audit-url", help="auditer une URL isolée (démo client)")
    a.add_argument("url")
    sub.add_parser("site", help="(re)construire le site à déployer : page d'accueil + toutes les maquettes actives")
    sub.add_parser("outreach", help="mettre en file les premiers contacts et relances dus")
    q = sub.add_parser("queue", help="emails en attente d'envoi (à envoyer via Gmail)")
    q.add_argument("--json", action="store_true")
    c = sub.add_parser("confirm", help="marquer un email de la file comme envoyé")
    c.add_argument("msg_id", type=int)
    c.add_argument("--thread", help="threadId Gmail renvoyé par l'envoi (pour rattacher les relances)")
    f = sub.add_parser("fail", help="marquer un email de la file comme échoué")
    f.add_argument("msg_id", type=int)
    f.add_argument("--reason", default="erreur d'envoi")
    f.add_argument("--bounce", action="store_true", help="adresse invalide : le lead passe en perdu")
    ib = sub.add_parser("inbound", help="enregistrer une réponse reçue d'un prospect")
    ib.add_argument("email")
    ib.add_argument("text")
    sub.add_parser("report", help="générer le rapport du jour")
    sub.add_parser("reaudit", help="ré-auditer les leads actifs (qualifiés et contactés)")
    pr = sub.add_parser("pricing", help="afficher la grille tarifaire")
    pr.add_argument("--market", help="code marché (FR, US, MA…) ; défaut : tous")
    ls = sub.add_parser("leads", help="lister les leads (par priorité)")
    ls.add_argument("--status", choices=STATUSES)
    ls.add_argument("--json", action="store_true")
    m = sub.add_parser("mark", help="changer le statut d'un lead (ex. won, lost, replied)")
    m.add_argument("lead_id", type=int)
    m.add_argument("status", choices=STATUSES)
    o = sub.add_parser("optout", help="ajouter un email à la liste d'opposition")
    o.add_argument("email")
    w = sub.add_parser("wa-sent", help="enregistrer des WhatsApp envoyés par AZ (ids de leads)")
    w.add_argument("ids", help="ids séparés par des virgules, ex. 591,602")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")

    if args.cmd == "audit-url":
        res = audit_url(args.url)
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
        return 0

    cfg = load_config(args.config)
    db = DB(cfg["paths"]["database"])

    if args.cmd == "run":
        stats, report = pipeline.run_all(cfg, skip_discover=args.skip_discover)
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        print(f"Rapport : {report}")
    elif args.cmd == "discover":
        n, combos = pipeline.step_discover(db, cfg)
        print(f"{n} nouveaux leads — cibles : {', '.join(combos)}")
    elif args.cmd == "import":
        print(f"{pipeline.step_import(db, args.csv)} leads importés")
    elif args.cmd == "audit":
        print(pipeline.step_audit(db, cfg))
    elif args.cmd == "site":
        pipeline.step_mockups(db, cfg)
        print(f"Site prêt à déployer : {cfg['paths']['site']}")
    elif args.cmd == "outreach":
        mailer = Mailer(cfg)
        budget = max(0, cfg["outreach"].get("daily_send_limit", 25) - db.emails_today())
        fu = pipeline.step_followups(db, cfg, mailer, budget)
        print({**fu, **pipeline.step_outreach(db, cfg, mailer, budget - fu["followups"])})
    elif args.cmd == "queue":
        msgs = [{**{k: m[k] for k in ("id", "lead_id", "kind", "to_addr", "subject", "body")}, "html_body": m["html"],
                 "reply_thread_id": db.lead_thread(m["lead_id"]) if m["kind"] != "initial" else None}
                for m in db.messages("queued")]
        if args.json:
            print(json.dumps(msgs, ensure_ascii=False, indent=2))
        else:
            for msg in msgs:
                print(f"#{msg['id']:<5} {msg['kind']:<11} {msg['to_addr']:<35} {msg['subject']}")
            print(f"{len(msgs)} email(s) en file")
    elif args.cmd == "confirm":
        pipeline.confirm(db, args.msg_id, args.thread)
        print(f"Message #{args.msg_id} marqué envoyé")
    elif args.cmd == "fail":
        pipeline.fail(db, args.msg_id, args.reason, bounce=args.bounce)
        print(f"Message #{args.msg_id} en échec : {args.reason}")
    elif args.cmd == "inbound":
        print(pipeline.inbound(db, args.email, args.text))
    elif args.cmd == "reaudit":
        for name, old, new in pipeline.reaudit(db, cfg):
            print(f"{name[:32]:<32} {old} → {new}")
    elif args.cmd == "report":
        print(pipeline.write_report(db, cfg, {}))
    elif args.cmd == "pricing":
        for code in [args.market] if args.market else enabled_markets(cfg):
            print(f"\n=== {code} — {market(cfg, code)['name']}")
            for t in pricing_table(cfg, code):
                print(f"  {t['label']} : {t['price']} + {t['monthly']} — {t['delivery']}")
    elif args.cmd == "leads":
        leads = db.leads(args.status)
        if args.json:
            print(json.dumps(leads, ensure_ascii=False, indent=2))
        else:
            for l in leads:
                print(f"#{l['id']:<5} {l['market'] or '-':<3} {l['status']:<13} {str(l['score']):>4} "
                      f"{str(l['priority']):>6}  {l['recommended_tier'] or '-':<9} {l['name'][:30]:<30} "
                      f"{l['email'] or l['phone'] or ''}")
    elif args.cmd == "mark":
        db.update_lead(args.lead_id, status=args.status)
        print(f"Lead #{args.lead_id} → {args.status}")
    elif args.cmd == "optout":
        db.add_optout(args.email)
        print(f"{args.email} ne sera plus contacté")
    elif args.cmd == "wa-sent":
        for i in [int(x) for x in args.ids.replace(" ", "").split(",") if x]:
            print(pipeline.mark_whatsapp_sent(db, i))
    return 0


if __name__ == "__main__":
    sys.exit(main())
