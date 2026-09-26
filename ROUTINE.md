# Routine pilotée par Claude

La commande `ywiad run` fait le travail mécanique (découverte, audit, maquettes, emails, relances).
Cette routine ajoute ce que le script ne sait pas faire seul : **relire, personnaliser, répondre et closer**.

Elle est prévue pour une routine Claude Code planifiée (tous les jours ouvrés), avec les connecteurs
Gmail et Google Calendar activés.

## Prompt de la routine

> Tu gères l'activité YourWebsiteInADay (refonte de sites de commerçants). Dans le dépôt
> `YourWebsiteInADay` :
>
> 1. Récupère l'état du CRM depuis la branche `crm-data` (dossiers `data/` et `out/`) et lance
>    `ywiad -v run`.
> 2. **Relecture des brouillons** (`out/outbox/*.eml` du jour) : vérifie chaque email — nom du
>    commerce correct, problèmes cités plausibles, ton professionnel. Écarte (`ywiad mark <id> lost`)
>    les faux positifs évidents : chaînes/franchises, administrations, sites manifestement corrects.
>    Personnalise la première phrase avec un détail réel du commerce quand c'est possible.
> 3. **Gmail** : crée un brouillon Gmail pour chaque email validé (ne les envoie pas tant que
>    `outreach.mode` vaut `draft` — c'est moi qui clique « Envoyer »).
> 4. **Réponses** : cherche dans Gmail les réponses des prospects contactés.
>    - « STOP » / refus → `ywiad optout <email>`.
>    - Intérêt → `ywiad mark <id> replied`, prépare un brouillon de réponse avec le devis de l'offre
>      conseillée (grille : `ywiad pricing`) et propose deux créneaux libres de 15 min trouvés dans
>      mon agenda.
>    - Question → rédige une réponse en brouillon.
> 5. Sauvegarde l'état sur `crm-data` et envoie-moi un résumé : nouveaux leads, emails prêts,
>    réponses reçues, liste WhatsApp (`out/reports/whatsapp_a_envoyer.csv`), valeur du pipeline.

## Ce qui reste humain (volontairement)

| Étape | Pourquoi |
|---|---|
| Clic « Envoyer » tant que le mode est `draft` | Valider le ton et la délivrabilité les premières semaines |
| Messages WhatsApp | WhatsApp interdit l'automatisation non officielle ; les textes sont prêts à copier |
| Signature du devis, encaissement | Engagement contractuel et paiement |
| Production du site final | Claude peut la faire à partir de la maquette, mais la mise en ligne sur le domaine du client demande ses accès |

Pour passer en envoi automatique : `outreach.mode: smtp`, secret `SMTP_PASSWORD`, et commencer
avec `daily_send_limit` bas (10–20/jour) sur un domaine d'envoi dédié, SPF/DKIM/DMARC configurés.
