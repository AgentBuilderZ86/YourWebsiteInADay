# YourWebsiteInADay

Activité de refonte de sites web pour commerçants, **pilotée de bout en bout par Claude** :
détection des commerces au site défaillant partout dans le monde, audit, maquette offerte, email
personnalisé, relances, réponses et transmission des closings à AZ.

- Site de l'offre et maquettes : https://yourwebsiteinaday.netlify.app
- Procédure de la routine autonome : [`ROUTINE.md`](ROUTINE.md)

## Comment ça marche

1. **Ciblage mondial tournant** : chaque exécution explore quelques combinaisons *marché × ville ×
   métier* (hôtels, dentistes, agences immobilières, restaurants, instituts de beauté…) via
   OpenStreetMap. Les chaînes et franchises sont exclues.
2. **Audit prudent** (note /100) : HTTPS, certificat, mobile, vitesse, technologies obsolètes, site vide,
   domaine expiré, sous-domaine gratuit… Si l'audit n'est pas fiable (pare-feu anti-robot, site en
   JavaScript), le commerce est écarté plutôt que d'affirmer quelque chose de faux.
3. **Qualification et priorisation** : site < 60/100 **et** email vérifié (enregistrement MX). Priorité =
   gravité × valeur du métier × commerce actif.
4. **Maquette offerte** à son nom, dans sa langue, publiée sur Netlify.
5. **Email** dans la langue du pays, avec l'audit, la maquette et la grille tarifaire locale — envoyé
   depuis le Gmail d'AZ par Claude, en heures de bureau du destinataire, 25 par jour maximum.
6. **Relances** J+3, J+7, J+14 dans le même fil Gmail, puis clôture. « STOP » et rebonds respectés.
7. **Réponses** : Claude répond, envoie le devis, propose deux créneaux ; les achats sont transmis à AZ.

## Marchés et prix

| Marché | Langue | Basique (24 h) | Standard (72 h) | Premium (7 j) | Statut |
|---|---|---|---|---|---|
| France, Belgique | FR | 490 € | 1 290 € | 2 900 € | actif |
| Maroc | FR | 2 990 MAD | 6 990 MAD | 14 990 MAD | actif (+ WhatsApp si pas d'email) |
| Émirats | EN | 1 990 AED | 4 990 AED | 11 900 AED | actif |
| Australie | EN | 790 AUD | 1 990 AUD | 4 900 AUD | actif |
| États-Unis | EN | $590 | $1,490 | $3,490 | en attente d'adresse postale (CAN-SPAM) |
| Canada / Québec | EN / FR | 790 CAD | 1 990 CAD | 4 500 CAD | en attente d'adresse postale (LCAP) |
| Royaume-Uni | EN | £450 | £1,190 | £2,790 | désactivé (PECR) |

Plus un abonnement mensuel (hébergement, maintenance) de 29 à 590 selon l'offre et le pays.
Allemagne, Autriche et Suisse sont exclues : la prospection par email y exige un consentement préalable,
même entre professionnels. Chaque email porte la mention légale du pays et un désabonnement « STOP ».

Tout est modifiable dans `config.example.yaml` (marchés, villes, métiers, prix, limites d'envoi).

## Commandes

```bash
pip install -e ".[dev]"
ywiad -v run                 # découverte + audit + maquettes + file d'envoi + relances + rapport
ywiad queue --json           # emails en attente (envoyés par Claude via Gmail)
ywiad confirm <id> --thread <threadId>
ywiad fail <id> --reason "…" [--bounce]
ywiad inbound <email> "<texte de la réponse>"
ywiad leads [--status qualified] [--json]
ywiad pricing [--market FR]
ywiad site                   # reconstruit out/site (page d'accueil + maquettes) pour Netlify
ywiad audit-url https://…    # audit d'un site isolé (démo)
ywiad mark <id> won|lost     # après un closing
```

État : `scripts/state.sh pull|push` restaure / sauvegarde la base **chiffrée** (AES-256) sur la
branche `crm-data` ; la clé est la variable Netlify `YWIAD_STATE_KEY`.

## Tests

```bash
pytest
```
