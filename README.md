# Roblox Script Explorer

Application Windows avec interface native pour capturer les scripts transmis au client Roblox, reconstruire du Luau lisible et explorer leur fonctionnement localement.

## Fonctionnalités

- Capture mémoire en lecture seule, avec validation RSB/XXH32, SHA-256 et Zstandard.
- Ouverture d'une capture existante ou d'un fichier `.luac` / `.luauc`.
- Arborescence des scripts, filtres par chemin, classe et état.
- Décompilation locale Luau v3–v14, dont le bytecode Roblox v14 testé.
- Code coloré, numéros de ligne, recherche dans un script et recherche globale.
- Analyse des services, fonctions, appels distants et dépendances `require`.
- Navigation vers les chemins de modules résolus statiquement.
- Constantes du bytecode, désassemblage et métadonnées d'intégrité.
- Traitement par lot, déduplication par contenu, cache local et arrêt du lot.
- Export des sources reconstruites et d'un rapport JSON.
- Vérification de syntaxe optionnelle avec le compilateur officiel Luau.

Aucun script récupéré n'est exécuté, et les captures ne sont envoyées à aucun service. L'analyse des dépendances reste statique : les références calculées peuvent demeurer non résolues.

## Démarrage depuis les sources

Prérequis : Windows 64 bits, Python **3.14+ avec Tkinter**, Git, les **Visual Studio C++ Build Tools** et CMake. Le CMake inclus dans les Build Tools est détecté automatiquement. Aucune dépendance Python supplémentaire n'est nécessaire pour utiliser l'application depuis les sources.

```powershell
git clone https://github.com/Nagaraaa/RobloxScriptExplorer.git
cd RobloxScriptExplorer
.\Build-Backend.ps1 -WithCompiler
python .\explorer_gui.py
```

`Start-RobloxScriptExplorer.cmd` permet ensuite de démarrer l'interface sans conserver de console ouverte. Un moteur déjà compilé peut aussi être choisi dans le menu **Moteur**.

Le moteur externe est téléchargé et construit localement à partir des commits de `backend.lock.json`. Ses sources et ses binaires ne sont pas redistribués dans ce dépôt ; voir [THIRD_PARTY.md](THIRD_PARTY.md).

## Utilisation

1. Cliquez sur **Ouvrir capture** et choisissez le dossier contenant `validated-manifest.json` et `bytecode/`. Le menu **Fichier** permet également d'ouvrir un manifeste ou un fichier de bytecode.
2. Sélectionnez un script dans l'arborescence pour reconstruire son code. Le mode **roblox** est adapté aux captures Roblox ; **standard** et **auto** sont disponibles pour les autres imports.
3. Consultez **Analyse**, **Constantes**, **Désassemblage** ou **Métadonnées**.
4. Cliquez sur **Décompiler tout** pour traiter toute la capture, puis lancez une recherche globale ou exportez le projet analysé.

**Capturer Roblox** crée une nouvelle capture lorsque le client compatible est ouvert dans une partie. L'outil conserve les captures, caches et réglages dans `.local/`, exclu de Git.

Raccourcis : `Ctrl+O` ouvrir une capture, `Ctrl+F` recherche dans le code, `Ctrl+G` aller à la ligne, `Ctrl+S` exporter le script affiché.

L'interface suit par défaut le thème clair ou sombre de Windows. L'interrupteur **Thème sombre** de l'en-tête (clic, ou `Tab` puis `Espace`) et le menu **Affichage** permettent d'en choisir un autre ; ce choix est conservé dans `.local/settings.json`. Les couleurs sont définies dans `explorer_theme.py`.

## États de reconstruction

| État | Signification |
|---|---|
| À décompiler | Aucun résultat chargé pour ce contenu et ce mode. |
| Reconstruit | Le moteur a produit du code sans diagnostic détecté. |
| Partiel | Le moteur signale une limite, ou la vérification de syntaxe échoue. |
| Échec | Pas de résultat exploitable : erreur, format invalide ou dépassement du délai. |

La mention **Syntaxe vérifiée** signifie que le compilateur Luau accepte le texte. Elle ne prouve pas l'équivalence comportementale avec le programme original. Le code reconstruit peut perdre les commentaires, certains noms et certaines structures du code initial. Les diagnostics et le désassemblage restent consultables pour examiner les cas partiels.

## Compatibilité de la capture

La capture en direct est actuellement verrouillée sur Roblox Windows `0.744.0.7441080`, build `version-9ac8595712b447a7`. Une autre build est refusée avant lecture. La décompilation de captures existantes reste disponible après une mise à jour de Roblox.

Périmètre parcouru : `ReplicatedStorage`, `ReplicatedFirst`, `StarterPlayer`, `StarterGui`, `StarterPack` et `Players`. `Workspace`, `CoreGui`, `CorePackages`, les instances détachées et les scripts non répliqués sont exclus. La présence d'un module dans la capture ne permet pas de récupérer la logique restée sur le serveur.

Limites du parcours : 25 000 instances, 16 000 pages mémoire et 45 secondes. Les erreurs et les limites atteintes figurent dans le manifeste. La capture n'est pas atomique : des objets peuvent changer pendant la lecture.

## Validation

```powershell
python -m unittest discover -s tests -v
$env:RSE_RUN_INTEGRATION='1'
python -m unittest discover -s tests -v
```

Les tests d'intégration nécessitent les moteurs locaux construits avec `-WithCompiler`. Les 13 tests comprennent 33 aller-retours de programmes synthétiques à trois niveaux d'optimisation : compilation, décompilation, vérification de syntaxe et comparaison des résultats exécutés. Les programmes couvrent notamment les conditions avec effets de bord, les sorties de branches, les boucles, `break`, `continue` et les appels parenthésés. Aucun script de jeu n'est exécuté ou inclus dans le dépôt. Le workflow Windows construit les moteurs verrouillés et exécute ces tests.

Le 25 octobre 2026, une capture locale de 1 207 instances / 1 085 contenus distincts a produit 1 207 sorties Luau : 1 092 classées **Reconstruit** et 115 **Partiel**. Parmi les 1 207 sorties, 1 145 passent la vérification de syntaxe. Les scripts de cette capture restent locaux et ne sont pas publiés.

### Corrections de récupération v2

| Mesure sur la même capture | Initial | Avec les corrections |
|---|---:|---:|
| Sorties sans diagnostic détecté | 1 092 | 1 159 |
| Sorties partielles signalées | 115 | 48 |
| Sorties acceptées par le compilateur | 1 145 | 1 207 |

Les 67 changements d'état concernent les cas initialement partiels ; aucun nouveau script n'est devenu partiel dans cette comparaison. Ce bilan ne prouve pas l'équivalence des scripts du jeu : celle-ci n'a pas été testée par exécution.

Les corrections transportent explicitement les sauts qui sortent d'une branche, préservent les passages vers un bloc `else` sans réévaluer ses conditions et réinitialisent les indicateurs de sortie dans les boucles. Lorsqu'une région présente plusieurs sorties incompatibles, une reconstruction conservatrice reste marquée partielle et le désassemblage est disponible.

L'émission des appels parenthésés et des instructions après un terminateur est également corrigée. Les instructions conservées après un terminateur sont signalées, même si le texte compile. La détection de diagnostics examine uniquement les commentaires du moteur, jamais les chaînes de caractères du jeu. Les écritures de cache concurrentes sont protégées.

`patch_backend.py` applique `backend-fixes.json` au moteur téléchargé. Les empreintes du contenu avant et après correction sont verrouillées, l'application est idempotente et une autre version de source est refusée. Le cache de validation utilise une nouvelle révision, afin de ne pas réutiliser les anciens états.

## Construire l'application Windows

```powershell
python -m pip install -r requirements-build.txt
.\Build-App.ps1
```

Le dossier portable est créé dans `.local/dist/RobloxScriptExplorer/`. Il contient un exécutable GUI et le runtime Python/Tk. Les outils locaux présents dans `tools/` sont copiés dans ce paquet local ; ils ne sont pas ajoutés au Git ni publiés automatiquement.

## Licence

Code original de l'application : MIT. Les moteurs externes et runtimes conservent leurs propres conditions ; voir [THIRD_PARTY.md](THIRD_PARTY.md).
