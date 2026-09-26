# Routine autonome YourWebsiteInADay

Ce fichier est la procédure suivie par Claude à chaque exécution planifiée. Il fait foi : pour changer le
comportement de la routine, on modifie ce fichier (pas le planning).

- Pilote : **AZ** (Adil Zriouil) — azriouil.az@gmail.com — WhatsApp +212 6 62 45 81 51
- Code : dépôt `AgentBuilderZ86/YourWebsiteInADay`, branche par défaut
- État (CRM) : branche `crm-data`, fichier **chiffré** `ywiad.sqlite.enc` (le dépôt est public)
- Clé de chiffrement : variable `YWIAD_STATE_KEY` du site Netlify `yourwebsiteinaday`
  (id `1b1f8625-7784-4333-b757-e44c5ccc8c36`)
- Site public (offre + maquettes) : https://yourwebsiteinaday.netlify.app
- Envoi : Gmail d'AZ, via le connecteur Gmail (aucun mot de passe stocké)

## Règles non négociables

1. **Jamais** de base CRM en clair dans git. Seul `scripts/state.sh push` écrit sur `crm-data`.
2. **Jamais** plus de `outreach.daily_send_limit` emails par jour (25), tous types confondus. Pause de
   20 à 40 secondes entre deux envois. Au moindre signe de limitation Gmail (quota, « rate limit »,
   blocage, 4xx/5xx répétés) : arrêter les envois, marquer les messages non envoyés avec `ywiad fail`,
   et le signaler dans le résumé.
3. **Jamais** de relance à quelqu'un qui a répondu, dit STOP, ou dont l'email a rebondi.
4. **Jamais** de RIB, de lien de paiement ni d'engagement contractuel : toute intention d'achat est
   transmise à AZ (email « [CLOSING] »). Claude répond aux questions, envoie le devis et propose un appel.
5. Ne jamais inventer : dans les emails, ne citer que les problèmes relevés par l'audit.
6. Pas d'envoi aux marchés sans base légale configurée (GB, DE, AT, CH), ni aux US/CA tant que
   `business.postal_address` est vide (le code bloque ces leads : statut `blocked`).

## Déroulé

### 0. Préparation

```bash
# Si le dépôt n'est pas présent : outil add_repo (AgentBuilderZ86/YourWebsiteInADay, access "push"), puis clone.
git fetch origin && git checkout "$(git remote show origin | sed -n 's/.*HEAD branch: //p')" && git pull
pip install -q -e .
```

Lire la clé : connecteur Netlify → `netlify-project-services-updater`, opération `manage-env-vars`,
`getAllEnvVars: true` sur le site ci-dessus → valeur de `YWIAD_STATE_KEY`. Puis :

```bash
export YWIAD_STATE_KEY='<valeur>'
scripts/state.sh pull
```

### 1. Réponses et rebonds (avant tout envoi)

- `ywiad leads --status contacted --json` → liste des emails contactés.
- Gmail `search_threads` : `in:inbox newer_than:4d -from:me` puis ne garder que les expéditeurs de
  la liste ; lire chaque fil avec `get_thread`.
  - « STOP », refus, « pas intéressé » → `ywiad optout <email>` (aucune réponse).
  - Intérêt, question, demande de prix → `ywiad inbound <email> "<texte>"` (statut `replied`), puis
    répondre dans le fil (`reply`) : réponse précise et courte, dans la langue du prospect, avec le
    prix de l'offre conseillée (`ywiad pricing --market <code>`), le lien de sa maquette, et deux
    créneaux de 15 min libres dans Google Calendar (jours ouvrés, heures de bureau du prospect).
  - Intention d'acheter / de payer / de signer → répondre qu'AZ revient vers lui dans la journée, et
    envoyer à azriouil.az@gmail.com un email « [CLOSING] <commerce> — <offre> — <prix> » avec le fil.
- Rebonds : `from:(mailer-daemon OR postmaster) newer_than:4d` → pour chaque adresse en échec,
  retrouver le lead (`ywiad leads --json`), `ywiad mark <id> lost` et `ywiad optout <email>`.

### 2. Prospection

```bash
ywiad -v run
```

Découverte (rotation mondiale marché × ville × métier), audit, maquettes, relances dues et premiers
contacts **mis en file** uniquement pour les marchés en heures de bureau à cet instant.

### 3. Publication des maquettes (avant l'envoi : les liens doivent fonctionner)

```bash
ywiad site
```

Connecteur Netlify → `netlify-deploy-services-updater`, opération `deploy-site`, `siteId` ci-dessus →
exécuter la commande `npx … @netlify/mcp …` renvoyée **depuis le dossier `out/site`** (jamais depuis
la racine du dépôt). Vérifier ensuite qu'une maquette en file répond en 200.

### 4. Relecture puis envoi

`ywiad queue --json`. Pour chaque message, relecture rapide :
- nom de commerce plausible (pas une chaîne, une administration, un hôpital, une école) ;
- langue cohérente avec le pays ; pas de caractères cassés ; lien de maquette présent ;
- sinon : `ywiad fail <id> --reason "écarté à la relecture"` et `ywiad mark <lead_id> lost`.

Envoi : Gmail `send_message` avec `to: [to_addr]`, `subject`, `htmlBody: html_body` (version mise en
page), `body` (version texte, alternative obligatoire — les deux tels quels, sans retouche) et, si
`reply_thread_id` est renseigné, `replyThreadId`. Avant le premier envoi de la session, vérifier que
l'image `…/preview.png` d'un message en file répond en 200 (sinon ne pas envoyer : redéployer). Puis :

```bash
ywiad confirm <id> --thread <threadId renvoyé par Gmail>
```

Échec d'envoi : `ywiad fail <id> --reason "<erreur>"` (ajouter `--bounce` si l'adresse est invalide).
Pause de 20 à 40 s entre deux envois (`python3 -c "import time,random; time.sleep(random.randint(20,40))"`).

### 5. Sauvegarde et résumé

```bash
scripts/state.sh push
ywiad report
```

Terminer par un résumé court (il part en notification) : nouveaux leads, emails envoyés
(premiers contacts / relances), réponses et closings, erreurs, valeur du pipeline par marché.
Le vendredi à l'exécution de 14 h 40 UTC, envoyer aussi ce résumé par email à azriouil.az@gmail.com
(objet « YWIAD — bilan semaine <n° ISO> »).

## Planning (UTC, jours ouvrés du destinataire)

| Heure UTC | Marchés en fenêtre d'envoi (8 h–18 h locales) |
|---|---|
| 07:40 lun–ven | Maroc, France, Belgique, Émirats |
| 14:40 lun–ven | France, Belgique, Maroc (relances, réponses) + bilan hebdo le vendredi |
| 22:40 dim–jeu | Australie (lendemain matin) |

États-Unis et Canada / Québec : désactivés dans la config tant qu'aucune adresse postale d'expéditeur
n'est renseignée. Pour les activer : `business.postal_address` puis `enabled: true` sur US, CA, QC.
