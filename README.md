# YourWebsiteInADay

Machine de prospection pour vendre des refontes de sites web à des commerçants :

1. **Découverte** des commerces d'une ville (OpenStreetMap gratuit, Google Places, ou import CSV).
2. **Audit automatique** de leur site : note /100 et problèmes formulés pour un commerçant
   (pas de HTTPS, pas adapté mobile, lent, obsolète, site en panne, pas de site du tout…).
3. **Qualification** : sous 60/100 (configurable), le commerce devient un prospect, avec une
   **offre conseillée** (Basique / Standard / Premium) selon son métier et l'état de son site.
4. **Maquette instantanée** : une page d'accueil de démonstration à son nom, prête à montrer.
5. **Premier contact** par email personnalisé (audit + lien vers la maquette + grille tarifaire),
   ou message WhatsApp prêt à copier si aucun email n'est trouvé.
6. **Relances** automatiques à J+3, J+7, J+14, puis clôture. Gestion du « STOP » (opposition).
7. **Rapport quotidien** : pipeline, valeur potentielle, réponses à traiter, liste WhatsApp.

## Grille tarifaire (par défaut, modifiable dans `config.yaml`)

| Offre | Prix | Délai | Maintenance | Contenu |
|---|---|---|---|---|
| **Basique** | 2 990 MAD | 24 h | 149 MAD/mois | One-page mobile, infos clés, WhatsApp, HTTPS + hébergement, Google Business |
| **Standard** | 6 990 MAD | 72 h | 290 MAD/mois | 5 pages, réservation/RDV, SEO local, rédaction |
| **Premium** | 14 990 MAD | 7 jours | 590 MAD/mois | Boutique / réservation avec paiement, sur-mesure, photos, multilingue FR/AR/EN |

Offre conseillée par métier : café, boulangerie, garage → Basique ; restaurant, coiffeur, beauté,
opticien, dentiste → Standard ; boutique, fleuriste, hôtel → Premium. Un commerce **sans site** se voit
proposer au maximum Standard ; un site existant très mauvais (< 30) monte de Basique à Standard.

## Démarrage

```bash
pip install -e ".[dev]"
cp config.example.yaml config.yaml      # renseignez vos coordonnées, votre ville, vos prix
ywiad pricing                           # vérifier la grille
ywiad audit-url https://un-site.ma      # démo : auditer un site
ywiad -v run                            # routine complète (mode brouillon par défaut)
ywiad leads                             # voir le pipeline
```

Commandes utiles : `ywiad import fichier.csv` (colonnes `name,category,website,email,phone,address,city`),
`ywiad mark <id> won|lost|replied`, `ywiad optout <email>`, `ywiad report`.

Sorties : `out/outbox/*.eml` (emails en brouillon), `out/mockups/<commerce>/index.html` (maquettes),
`out/reports/AAAA-MM-JJ.md` et `out/reports/whatsapp_a_envoyer.csv`.

### Publier les maquettes

Déployez le dossier `out/mockups/` sur un hébergement statique (Netlify, GitHub Pages…) et indiquez
son URL dans `business.mockup_base_url` : les emails incluront alors le lien vers la maquette de
chaque prospect. Les maquettes sont en `noindex`.

## Automatisation

- **GitHub Actions** (`.github/workflows/routine.yml`) : exécution chaque jour ouvré ; l'état du CRM
  est conservé sur la branche `crm-data`. Mettez votre `config.yaml` dans la variable de dépôt
  `YWIAD_CONFIG` et les secrets (`SMTP_PASSWORD`, `IMAP_PASSWORD`, `GOOGLE_PLACES_API_KEY`,
  `PAGESPEED_API_KEY`) dans les secrets du dépôt. Gardez le dépôt **privé** (il contient des données
  de prospects).
- **Routine Claude** (`ROUTINE.md`) : relecture des emails, création des brouillons Gmail, traitement
  des réponses, devis et prise de rendez-vous.

## Envoi : brouillon d'abord

Par défaut `outreach.mode: draft` : rien n'est envoyé, les emails sont écrits dans `out/outbox/`.
Passez en `smtp` quand le ton est validé, avec un **domaine d'envoi dédié** (SPF, DKIM, DMARC) et une
limite quotidienne basse au début (10–20/jour) pour protéger la délivrabilité.

## Cadre légal (à valider pour votre pays)

- Prospection **B2B uniquement**, vers des adresses professionnelles publiées par le commerce.
- Chaque email identifie l'expéditeur, précise l'origine des données et offre un désabonnement
  simple (« STOP » + en-tête `List-Unsubscribe`) ; les oppositions sont définitives (`optouts`).
- Au Maroc, la loi 09-08 et la CNDP encadrent la prospection électronique ; en France, le RGPD et la
  CNIL. Vérifiez vos obligations (déclaration, information, durée de conservation) avant l'envoi réel.

## Limites connues

- L'audit lit le HTML initial : un site entièrement généré en JavaScript peut être mal noté.
- La couverture OpenStreetMap (sites, emails) est inégale selon les villes : Google Places est plus
  complet (payant au-delà du quota gratuit).
- Les relances supposent que les brouillons sont envoyés le jour même en mode `draft`.

## Tests

```bash
pytest
```
