# Composants externes

## Christian Stolev — LuauDecompiler / luaudec

- Projet : https://github.com/stolevchristian/LuauDecompiler
- Commit verrouillé : `6d0a7a2ef292171f306bf022ad56362139ac738e`
- Luau utilisé : `7a3d37eea5b0b26c97f05f6137ee0e411e6cd6ed`
- Intégration : sous-processus local, lecture de bytecode uniquement.

Le dépôt amont consulté ne fournit pas de licence générale explicite. Ses sources et binaires ne sont donc pas inclus dans ce dépôt public. `Build-Backend.ps1` récupère les sources verrouillées et les compile sur la machine de l’utilisateur. La licence MIT de Roblox Script Explorer couvre uniquement le code original de cette application.

## Luau

https://github.com/luau-lang/luau — licence MIT, disponible dans le dépôt amont. Le compilateur optionnel ne sert qu'à vérifier la syntaxe des sources reconstruites ; ces sources ne sont pas exécutées.

## Informations sur le format Roblox

Le conteneur RSB1 et son checksum sont décrits dans la fonction `decompress` de https://github.com/tyronetheqt/Xeno/blob/main/Xeno/src/worker.cpp. Aucun injecteur ni fonction d'écriture de ce projet n'est utilisé.

Les points d'entrée du client Windows ont été recoupés avec https://offsets.imtheo.lol/Offsets.hpp et https://rbxoffsets.com/api/v1/windows/offsets/version-9ac8595712b447a7/final/offsets.json. Les offsets du conteneur des scripts ont été découverts et validés localement pour la build prise en charge.

## Distribution Windows locale

Les paquets créés localement par `Build-App.ps1` contiennent Python, Tcl/Tk et leurs bibliothèques avec les licences fournies par ces distributions. PyInstaller utilise sa licence avec l'exception de distribution des applications ; voir https://pyinstaller.org/en/stable/license.html. Les binaires externes du décompilateur sont conservés hors du Git et ne sont pas publiés par le workflow CI.
