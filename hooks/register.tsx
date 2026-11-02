/**
 * Le temps passé sur le projet, au-dessus du champ de saisie.
 *
 * Tout le calcul vit dans `scripts/compteur.py`, à côté : il lit les
 * transcripts, tient son cache et écrit le relevé. Ce mod ne fait que
 * l'appeler et afficher sa ligne.
 *
 * Pourquoi un mod : le réglage `statusLine` de `settings.json` fait exactement
 * cela dans le CLI en terminal, mais l'app desktop ne l'exécute pas (vérifié le
 * 02/11/2026 : ni appel du script, ni trace dans les logs de l'app).
 *
 * Recalculé à l'ouverture de la session et à la fin de chaque tour du modèle,
 * jamais pour un sous-agent. Le script lit en incrémental, un offset par
 * session dans son cache : l'appel coûte quelques centièmes de seconde.
 */
import type { EngineInterface, Register } from 'claude-code'

const COMPTEUR = 'scripts/compteur.py'

// Dans l'app desktop le PATH est maigre, d'où des chemins d'abord et `python3`
// en dernier recours. Le premier qui répond est retenu pour la session : on
// essaie en lançant, plutôt qu'en interrogeant le disque, pour que l'ordre
// décide seul et qu'aucun refus de lecture ne fasse taire le compteur.
const PYTHONS = ['/usr/bin/python3', '/opt/homebrew/bin/python3',
                 '/usr/local/bin/python3', 'python3']

let ligne: string | null = null
let python: string | null = null

const calcule = async ($: EngineInterface, env: Record<string, string>) => {
  const sessionId = await $.session.id().catch(() => '')
  if (!sessionId) {
    return
  }
  for (const chemin of python === null ? PYTHONS : [python]) {
    try {
      const { exitCode, stdout } = await $.process.run(
        [chemin, `${$.plugin.root}/${COMPTEUR}`, '--ligne', sessionId],
        { timeoutMs: 10000, env },
      )
      const texte = stdout.trim()
      if (exitCode === 0 && texte) {
        python = chemin
        ligne = texte
        $.ui.invalidate('ui.render')

        return
      }
    } catch {
      // Un compteur n'a aucune raison d'interrompre une session.
    }
  }
  python = null                 // celui qui marchait ne marche plus : on cherchera à nouveau
}

export const register: Register = (on, options) => {
  // Les réglages passent par l'environnement ; le script les relit aussi
  // lui-même dans les settings, pour qu'un `--amorcer` lancé à la main et le
  // réglage `statusLine` du terminal comptent comme le mod.
  const env = {
    TEMPS_PROJET_JOURNEE: String(options.journee ?? ''),
    TEMPS_PROJET_INACTIVITE: String(options.inactivite ?? ''),
    TEMPS_PROJET_LANGUE: String(options.langue ?? ''),
    TEMPS_PROJET_GROUPES: String(options.groupes ?? ''),
  }

  on('session.start', async ($, e, next) => {
    await calcule($, env)

    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    if (e.agentId === undefined) {
      await calcule($, env)
    }

    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (!ligne || e.props.hasSurvey) {
      return next(e)
    }
    const { Box, Text } = $.ui.resolve(e)

    return (
      <Box>
        <Text dimColor>{ligne}</Text>
      </Box>
    )
  })
}
