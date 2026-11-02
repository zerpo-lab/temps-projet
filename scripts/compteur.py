#!/usr/bin/env python3
"""Le temps passé par projet, et les tokens de la conversation en cours.

Le temps compte ici comme la somme des intervalles entre messages inférieurs
au seuil d'inactivité ; au-delà, on considère que la personne était partie et
le compteur reprend à son message de retour.

CE QUE ÇA NE MESURE PAS : tout le travail hors de la conversation. Rien de ce
qui se fait ailleurs — la CAO, les essais, les trajets, les réunions — ne
compte. Le chiffre est un plancher, jamais une base de facturation.

Lecture incrémentale : un offset par session dans le cache, donc on ne relit
jamais ce qu'on a déjà lu. Les transcripts font leur vie — Claude Code les
efface au bout de trente jours ; le cache garde ce qu'il a lu, même quand ils
disparaissent.

Appelé de trois façons :
  --ligne <id de session>   par le mod : une ligne sur stdout
  --amorcer                 à la main : relit tous les projets, réécrit le relevé
  (sans rien)               par le réglage `statusLine` du CLI : état sur stdin
"""
import json, io, os, sys, glob, datetime

# ---------------------------------------------------------------- les réglages

DEFAUTS = {
    'journee': 8.0,         # heures ; l'unité dans laquelle le temps s'affiche
    'inactivite': 15.0,     # minutes ; au-delà, le temps ne compte plus
    'langue': 'fr',
    'groupes': '',          # 'dossier=Nom; dossier=Nom', ou un objet JSON
}

# Le mod passe les réglages par l'environnement ; appelé autrement, le script
# les relit lui-même dans les settings. Les deux chemins donnent le même
# résultat, ce qui compte pour `--amorcer`, lancé à la main sans le mod.
ENV = {cle: 'TEMPS_PROJET_' + cle.upper() for cle in DEFAUTS}

TEXTES = {
    'fr': {
        'projet': 'projet {j:.1f} j',
        'tokens': '{n} tok',
        'date': '%d/%m/%Y',
        'titre': 'Temps par projet',
        'maj': 'Mis à jour le {d}.',
        'colonnes': ('| projet | total | devant Claude | saisi | sessions | '
                     'période mesurée |'),
        'total': '**Total : {j:.1f} jours de {h:g} h.**',
        'inconnu': '?',
        'journal': '# date\tprojet\tjours de {h:g} h cumulés\tdont saisis à la main',
        'amorce': ('{v} transcripts relus, {s} sessions en cache '
                   '({n} nouvelles, aucune perdue). Relevé : {f}'),
        'entete': """# {titre}

Temps passé en conversation avec Claude, en journées de {h:g} h. Écrit par le
mod `temps-projet`. **Ce fichier est la mémoire longue** : les transcripts dont
il tire ces chiffres peuvent disparaître, lui non.

⚠️ **Ce n'est pas du temps facturable.** Il ne compte que le temps devant la
conversation. Tout ce qui se fait ailleurs compte pour zéro. C'est un plancher.

Ce qui est écrit en dehors des deux marqueurs n'est jamais réécrit : notes et
journées hors Claude peuvent vivre ici.

""",
    },
    'en': {
        'projet': 'project {j:.1f} d',
        'tokens': '{n} tok',
        'date': '%Y-%m-%d',
        'titre': 'Time per project',
        'maj': 'Updated {d}.',
        'colonnes': ('| project | total | with Claude | logged | sessions | '
                     'measured period |'),
        'total': '**Total: {j:.1f} days of {h:g} h.**',
        'inconnu': '?',
        'journal': '# date\tproject\tcumulative {h:g} h days\tof which logged by hand',
        'amorce': ('{v} transcripts re-read, {s} sessions cached '
                   '({n} new, none lost). Report: {f}'),
        'entete': """# {titre}

Time spent in conversation with Claude, in days of {h:g} h. Written by the
`temps-projet` mod. **This file is the long memory**: the transcripts these
figures come from may disappear, this one will not.

⚠️ **This is not billable time.** It counts only the time in front of the
conversation. Everything done elsewhere counts as zero. It is a floor.

Whatever is written outside the two markers is never rewritten: notes and days
away from Claude can live here.

""",
    },
}


def bases():
    """Les dossiers de configuration possibles de Claude Code.

    Il y en a deux par défaut (`~/.claude` et `~/.config/claude`), et
    CLAUDE_CONFIG_DIR les remplace, en acceptant une liste séparée par des
    virgules. Ne lire que le premier ferait un compteur faux en silence.
    """
    perso = os.environ.get('CLAUDE_CONFIG_DIR')
    brutes = perso.split(',') if perso else ['~/.claude', '~/.config/claude']
    return [os.path.expanduser(b.strip()) for b in brutes if b.strip()]


def dossier_config():
    """Celui qui existe, le premier : c'est là que vivent cache et relevé."""
    for b in bases():
        if os.path.isdir(b):
            return b
    liste = bases()
    return liste[0] if liste else os.path.expanduser('~/.claude')


def options_des_settings():
    """Les réglages du mod tels que les settings les gardent.

    `pluginConfigs[<nom>].options`, le nom pouvant porter le suffixe `@inline`
    selon la façon dont le mod a été chargé.
    """
    for b in bases():
        try:
            d = json.load(io.open(os.path.join(b, 'settings.json'), encoding='utf-8'))
        except Exception:
            continue
        pc = d.get('pluginConfigs') or {}
        for cle in ('temps-projet', 'temps-projet@inline'):
            o = (pc.get(cle) or {}).get('options')
            if isinstance(o, dict):
                return o
    return {}


def reglages():
    """L'environnement d'abord, les settings ensuite, les défauts enfin."""
    stockees = options_des_settings()
    valeurs = {}
    for cle, defaut in DEFAUTS.items():
        brut = os.environ.get(ENV[cle])
        if brut is None or brut == '':
            brut = stockees.get(cle)
        if brut is None or brut == '':
            valeurs[cle] = defaut
        elif isinstance(defaut, float):
            try:
                valeurs[cle] = float(str(brut).replace(',', '.'))
            except (TypeError, ValueError):
                valeurs[cle] = defaut
        else:
            valeurs[cle] = str(brut)
    if valeurs['langue'] not in TEXTES:
        valeurs['langue'] = DEFAUTS['langue']
    return valeurs


def table_groupes(brut):
    """Plusieurs dossiers de conversation sous un même nom de projet.

    Une table de noms, pas un système : ce qui est groupé ici est de l'histoire
    figée — un projet renommé, deux conversations ouvertes sur le même travail —
    pas une règle à entretenir. Un projet absent s'affiche avec son chemin :
    c'est seulement un nom moins joli, rien ne se perd. Un projet renommé, par
    contre, doit être ajouté ici, sinon il repart de zéro sur une deuxième ligne.

    Deux écritures, `dossier=Nom` séparés par `;` ou par des retours à la ligne,
    ou un objet JSON. La clé est le nom du dossier de transcripts, le chemin du
    projet aplati (`-Users-moi-Documents-Chose`).
    """
    brut = (brut or '').strip()
    if not brut:
        return {}
    if brut.startswith('{'):
        try:
            charge = json.loads(brut)
            if isinstance(charge, dict):
                return {str(k): str(v) for k, v in charge.items()}
        except Exception:
            pass
    table = {}
    for morceau in brut.replace('\n', ';').split(';'):
        if '=' not in morceau:
            continue
        cle, _, nom = morceau.partition('=')
        cle, nom = cle.strip(), nom.strip()
        if cle and nom:
            table[cle] = nom
    return table


R = reglages()
L = TEXTES[R['langue']]
JOURNEE_H = max(0.25, R['journee'])
JOURNEE_MS = JOURNEE_H * 3600000
SEUIL_MS = max(1.0, R['inactivite']) * 60000
GROUPES = table_groupes(R['groupes'])

CONFIG = dossier_config()
CACHE = os.path.join(CONFIG, 'statusline-temps-cache.json')
RELEVE = os.path.join(CONFIG, 'temps-projets.md')
JOURNAL = os.path.join(CONFIG, 'temps-projets-journal.tsv')
MANUEL = os.path.join(CONFIG, 'temps-manuel.tsv')
DEBUT, FIN = '<!-- compteur: debut -->', '<!-- compteur: fin -->'

# Le chemin de la maison, aplati comme Claude Code aplatit les chemins de
# projet : c'est ce qu'on retire d'un nom de dossier pour l'afficher.
PREFIXE_MAISON = os.path.expanduser('~').replace(os.sep, '-') + '-'


# ---------------------------------------------------------------- la lecture

def racines():
    """Les dossiers où Claude Code écrit ses transcripts."""
    trouves = []
    for b in bases():
        d = os.path.join(b, 'projects')
        if os.path.isdir(d):
            trouves.append(d)
    return trouves


def groupe(dossier):
    """Le nom affiché. Un projet inconnu prend la fin de son chemin aplati."""
    if dossier in GROUPES:
        return GROUPES[dossier]
    nom = dossier
    if nom.startswith(PREFIXE_MAISON):
        nom = nom[len(PREFIXE_MAISON):]
    nom = nom.replace('-', ' ').strip()
    return ('…' + nom[-33:]) if len(nom) > 34 else nom


def iso_ms(s):
    for fmt in ('%Y-%m-%dT%H:%M:%S.%f%z', '%Y-%m-%dT%H:%M:%S%z'):
        try:
            return int(datetime.datetime.strptime(s.replace('Z', '+0000'), fmt).timestamp() * 1000)
        except (ValueError, AttributeError):
            pass
    return None


def avance(path, etat):
    """Lit le transcript depuis l'offset mémorisé et cumule le temps actif."""
    try:
        taille = os.path.getsize(path)
    except OSError:
        return etat
    off = etat.get('offset', 0)
    if taille < off:                       # fichier réécrit depuis zéro
        etat = {'offset': 0, 'actif': 0, 'last': None}
        off = 0
    if taille == off:
        return etat
    with open(path, 'rb') as fh:
        fh.seek(off)
        donnees = fh.read()
    coupe = donnees.rfind(b'\n') + 1       # ne jamais consommer une ligne en cours d'écriture
    if not coupe:
        return etat
    etat['offset'] = off + coupe
    last, actif, premier = etat.get('last'), etat.get('actif', 0), None
    for brut in donnees[:coupe].split(b'\n'):
        if len(brut) > 200000 or b'"timestamp"' not in brut:
            continue
        if b'"type":"user"' not in brut and b'"type":"assistant"' not in brut:
            continue
        try:
            d = json.loads(brut.decode('utf-8', 'replace'))
        except Exception:
            continue
        if d.get('type') not in ('user', 'assistant') or d.get('isSidechain'):
            continue
        ms = iso_ms(d.get('timestamp') or '')
        if not ms:
            continue
        if last and 0 < ms - last <= SEUIL_MS:
            actif += ms - last
        if premier is None:
            premier = ms
        last = ms
    etat['actif'], etat['last'] = actif, last
    if premier:
        etat['debut'] = min(premier, etat.get('debut') or premier)
    if last:
        etat['fin'] = max(last, etat.get('fin') or last)
    return etat


def charge():
    try:
        return json.load(io.open(CACHE, encoding='utf-8'))
    except Exception:
        return {'sessions': {}}


def sauve(cache):
    try:
        json.dump(cache, io.open(CACHE, 'w', encoding='utf-8'))
    except Exception:
        pass


def court(n):
    for seuil, suffixe in ((1e9, 'G'), (1e6, 'M'), (1e3, 'k')):
        if n >= seuil:
            return f'{n / seuil:.1f}{suffixe}'
    return str(int(n))


def heures_saisies():
    """Les journées déclarées à la main : `date, projet, heures, note`, une par
    ligne dans MANUEL.

    C'est la moitié que la mesure ne voit pas. Elle s'ajoute au compte du projet
    mais reste affichée à part : une mesure et une déclaration ne se confondent
    pas.
    """
    par_projet = {}
    try:
        for brut in io.open(MANUEL, encoding='utf-8'):
            if brut.startswith('#') or not brut.strip():
                continue
            bouts = brut.rstrip('\n').split('\t')
            if len(bouts) < 3:
                continue
            try:
                par_projet[bouts[1]] = par_projet.get(bouts[1], 0.0) + float(bouts[2].replace(',', '.'))
            except ValueError:
                continue
    except OSError:
        pass
    return par_projet


def ecrit_journal(par):
    """Ajoute le cumul du jour, par projet, seulement quand il a bougé.

    Le relevé dit où on en est ; ce journal dit ce qui a changé depuis la
    dernière fois. C'est lui qui permet de répondre à « qu'est-ce qui a été fait
    dans l'intervalle » : la différence entre deux cumuls datés. Jamais réécrit,
    on ajoute à la fin.
    """
    aujourdhui = f"{datetime.date.today():%Y-%m-%d}"
    derniers = {}
    try:
        for brut in io.open(JOURNAL, encoding='utf-8'):
            bouts = brut.rstrip('\n').split('\t')
            if len(bouts) >= 3:
                derniers[bouts[1]] = bouts[2]
    except OSError:
        pass
    saisi = heures_saisies()
    lignes = []
    for nom in sorted(set(par) | set(saisi)):
        mesure = par.get(nom, {}).get('actif', 0) / JOURNEE_MS
        j_saisi = saisi.get(nom, 0.0) / JOURNEE_H
        jours = f"{mesure + j_saisi:.2f}"
        if derniers.get(nom) != jours:
            lignes.append(f"{aujourdhui}\t{nom}\t{jours}\t{j_saisi:.2f}")
    if not lignes:
        return
    try:
        neuf = not os.path.exists(JOURNAL)
        with io.open(JOURNAL, 'a', encoding='utf-8') as fh:
            if neuf:
                fh.write(L['journal'].format(h=JOURNEE_H) + '\n')
            fh.write('\n'.join(lignes) + '\n')
    except Exception:
        pass


def ecrit_releve(sessions, force=False):
    """Le relevé lisible : ce qui survit quand les transcripts disparaissent.

    Réécrit au plus une fois par jour, et SEULEMENT entre ses deux marqueurs :
    ce qui est écrit autour (temps hors Claude, notes) est préservé.
    """
    try:
        if not force and os.path.exists(RELEVE) and (
                datetime.datetime.now().timestamp() - os.path.getmtime(RELEVE)) < 86400:
            return
    except OSError:
        pass
    par = {}
    for s in sessions.values():
        p = par.setdefault(s.get('projet') or L['inconnu'],
                           {'actif': 0, 'n': 0, 'debut': None, 'fin': None})
        p['actif'] += s.get('actif', 0)
        p['n'] += 1
        for cle, f in (('debut', min), ('fin', max)):
            v = s.get(cle)
            if v:
                p[cle] = f(v, p[cle] or v)

    def jour(ms):
        return datetime.datetime.fromtimestamp(ms / 1000).strftime(L['date'])

    saisi = heures_saisies()
    for nom in saisi:                      # un projet connu du seul fichier manuel
        par.setdefault(nom, {'actif': 0, 'n': 0, 'debut': None, 'fin': None})
    bloc = [DEBUT, '',
            L['maj'].format(d=f"{datetime.date.today():{L['date']}}"), '',
            L['colonnes'],
            '|---|---|---|---|---|---|']
    for nom, p in sorted(par.items(),
                         key=lambda x: -(x[1]['actif'] / 3600000 + saisi.get(x[0], 0.0))):
        mesure = p['actif'] / JOURNEE_MS
        decl = saisi.get(nom, 0.0) / JOURNEE_H
        if mesure + decl < 0.02:
            continue
        periode = f"{jour(p['debut'])} → {jour(p['fin'])}" if p['debut'] else '—'
        bloc.append(f"| {nom} | **{mesure + decl:.1f}** | {mesure:.1f} | "
                    f"{decl:.1f} | {p['n']} | {periode} |")
    ecrit_journal(par)
    total = sum(p['actif'] for p in par.values()) + sum(saisi.values()) * 3600000
    bloc += ['', L['total'].format(j=total / JOURNEE_MS, h=JOURNEE_H), '', FIN]
    bloc = '\n'.join(bloc)

    entete = L['entete'].format(titre=L['titre'], h=JOURNEE_H)
    ancien = ''
    try:
        ancien = io.open(RELEVE, encoding='utf-8').read()
    except Exception:
        pass
    if DEBUT in ancien and FIN in ancien:
        avant, reste = ancien.split(DEBUT, 1)
        apres = reste.split(FIN, 1)[1]
        contenu = avant + bloc + apres
    else:
        contenu = entete + bloc + '\n'
    try:
        io.open(RELEVE, 'w', encoding='utf-8').write(contenu)
    except Exception:
        pass


def amorce_tout():
    """Relit tous les projets. FUSIONNE : une session dont le transcript a
    disparu garde le temps déjà compté, elle n'est jamais effacée."""
    cache = charge()
    sessions = cache.setdefault('sessions', {})
    avant, vus = len(sessions), 0
    dossiers = [(d, os.path.join(r, d))
                for r in racines() for d in sorted(os.listdir(r))]
    for d, dossier in dossiers:
        if not os.path.isdir(dossier):
            continue
        for f in sorted(glob.glob(os.path.join(dossier, '*.jsonl'))):
            cle = os.path.basename(f)
            etat = avance(f, sessions.get(cle, {'offset': 0, 'actif': 0, 'last': None}))
            etat['projet'] = groupe(d)
            sessions[cle] = etat
            vus += 1
    for cle, s in sessions.items():        # renommage d'un groupe : on suit
        for d, nom in GROUPES.items():
            if s.get('projet') in (d, groupe(d)):
                s['projet'] = nom
    sauve(cache)
    ecrit_releve(sessions, force=True)
    print(L['amorce'].format(v=vus, s=len(sessions),
                             n=len(sessions) - avant, f=RELEVE))


def tokens_du_transcript(path):
    """Le cumul consommé par la session, lu dans son dernier bloc `cost-state`.

    Pas le remplissage de la fenêtre de contexte : ce qui a été dépensé depuis
    l'ouverture, cache exclu.
    """
    dernier = None
    try:
        with open(path, 'rb') as fh:
            for brut in fh:
                if b'"cost-state"' in brut[:60]:
                    dernier = brut
    except OSError:
        return 0
    if not dernier:
        return 0
    try:
        mu = json.loads(dernier.decode('utf-8', 'replace')).get('modelUsage') or {}
    except Exception:
        return 0
    return sum((u.get('inputTokens') or 0) + (u.get('outputTokens') or 0) for u in mu.values())


def cherche_transcript(session_id):
    """Le transcript d'une session, par son id : le nom du dossier de projet
    est un chemin aplati qu'on ne peut pas reconstruire (les accents y
    deviennent des tirets), alors on le cherche."""
    for r in racines():
        trouve = glob.glob(os.path.join(r, '*', f'{session_id}.jsonl'))
        if trouve:
            return trouve[0]
    return ''


def main(session_id=None):
    etat_cc = {}
    if session_id:
        tp = cherche_transcript(session_id)
    else:
        try:
            etat_cc = json.load(sys.stdin)
        except Exception:
            pass
        tp = etat_cc.get('transcript_path') or ''
    cache = charge()
    sessions = cache.setdefault('sessions', {})
    dossier = os.path.dirname(tp)
    nom_projet = groupe(os.path.basename(dossier))

    # Un projet jamais vu : on lit tout son dossier, une fois. C'est ainsi
    # qu'un nouveau projet entre tout seul dans le relevé.
    a_lire = [tp] if tp else []
    if dossier and not any(s.get('projet') == nom_projet for s in sessions.values()):
        a_lire = sorted(set(glob.glob(os.path.join(dossier, '*.jsonl')) + a_lire))
    for f in a_lire:
        cle = os.path.basename(f)
        etat = avance(f, sessions.get(cle, {'offset': 0, 'actif': 0, 'last': None}))
        etat['projet'] = nom_projet
        sessions[cle] = etat
    sauve(cache)
    ecrit_releve(sessions)

    total = sum(s.get('actif', 0) for s in sessions.values() if s.get('projet') == nom_projet)
    total += heures_saisies().get(nom_projet, 0.0) * 3600000   # les journées déclarées comptent
    cw = etat_cc.get('context_window') or {}
    toks = ((cw.get('total_input_tokens') or 0) + (cw.get('total_output_tokens') or 0)
            or (tokens_du_transcript(tp) if tp else 0))
    bouts = [nom_projet, L['projet'].format(j=total / JOURNEE_MS)]
    if toks:
        bouts.append(L['tokens'].format(n=court(toks)))
    print(' · '.join(bouts))


if __name__ == '__main__':
    if '--amorcer' in sys.argv:
        amorce_tout()
    elif '--ligne' in sys.argv:          # appelé par le mod : l'id de session suffit
        i = sys.argv.index('--ligne')
        main(sys.argv[i + 1] if len(sys.argv) > i + 1 else None)
    else:
        main()                           # appelé par le CLI : l'état arrive sur stdin
