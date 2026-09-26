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
from .pricing import pricing_table


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ywiad", description="YourWebsiteInADay — prospection de refontes de sites")
    parser.add_argument("-c", "--config", help="fichier de configuration (défaut : config.yaml)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="routine complète : découverte, audit, maquettes, contacts, relances, rapport")
    r.add_argument("--skip-discover", action="store_true", help="ne pas chercher de nouveaux leads")
    sub.add_parser("discover", help="chercher de nouveaux commerçants")
    i = sub.add_parser("import", help="importer des leads depuis un CSV")
    i.add_argument("csv")
    sub.add_parser("audit", help="auditer les leads nouveaux")
    a = sub.add_parser("audit-url", help="auditer une URL isolée (démo client)")
    a.add_argument("url")
    sub.add_parser("mockups", help="générer les maquettes des leads qualifiés")
    sub.add_parser("outreach", help="premier contact + relances dues")
    sub.add_parser("report", help="générer le rapport du jour")
    sub.add_parser("pricing", help="afficher la grille tarifaire")
    ls = sub.add_parser("leads", help="lister les leads")
    ls.add_argument("--status", choices=STATUSES)
    m = sub.add_parser("mark", help="changer le statut d'un lead (ex. won, lost, replied)")
    m.add_argument("lead_id", type=int)
    m.add_argument("status", choices=STATUSES)
    o = sub.add_parser("optout", help="ajouter un email à la liste d'opposition")
    o.add_argument("email")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")

    if args.cmd == "audit-url":
        cfg = load_config(args.config) if args.config else {"audit": {}}
        res = audit_url(args.url, timeout=cfg["audit"].get("timeout_seconds", 15))
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
        return 0

    cfg = load_config(args.config)
    db = DB(cfg["paths"]["database"])

    if args.cmd == "run":
        stats, report = pipeline.run_all(cfg, skip_discover=args.skip_discover)
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        print(f"Rapport : {report}")
    elif args.cmd == "discover":
        print(f"{pipeline.step_discover(db, cfg)} nouveaux leads")
    elif args.cmd == "import":
        print(f"{pipeline.step_import(db, args.csv)} leads importés")
    elif args.cmd == "audit":
        print(pipeline.step_audit(db, cfg))
    elif args.cmd == "mockups":
        print(f"{pipeline.step_mockups(db, cfg)} maquettes générées dans {cfg['paths']['mockups']}")
    elif args.cmd == "outreach":
        mailer = Mailer(cfg)
        budget = max(0, cfg["outreach"].get("daily_send_limit", 20) - db.sent_today())
        fu = pipeline.step_followups(db, cfg, mailer, budget)
        print({**fu, **pipeline.step_outreach(db, cfg, mailer, budget - fu["followups"])})
    elif args.cmd == "report":
        print(pipeline.write_report(db, cfg, {}))
    elif args.cmd == "pricing":
        for t in pricing_table(cfg):
            print(f"\n{t['label']}\n  {t['price']} + {t['monthly']} — livré en {t['delivery']}")
            for f in t["features"]:
                print(f"   • {f}")
    elif args.cmd == "leads":
        for l in db.leads(args.status):
            print(f"#{l['id']:<4} {l['status']:<13} {str(l['score']):>4}  {l['recommended_tier'] or '-':<9} "
                  f"{l['name'][:32]:<32} {l['email'] or l['phone'] or ''}")
    elif args.cmd == "mark":
        db.update_lead(args.lead_id, status=args.status)
        print(f"Lead #{args.lead_id} → {args.status}")
    elif args.cmd == "optout":
        db.add_optout(args.email)
        print(f"{args.email} ne sera plus contacté")
    return 0


if __name__ == "__main__":
    sys.exit(main())
